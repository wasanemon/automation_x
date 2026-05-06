from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.config import Settings
from growth_agent.models import AutomationRun, DecisionLog, DraftImportRun, Hypothesis
from growth_agent.services.feedback import active_playbook_rules
from growth_agent.services.metrics import metrics_summary

DEFAULT_MEMORY_LIMIT = 10
MAX_MEMORY_LIMIT = 100


def create_decision_log(
    db: Session,
    *,
    stage: str,
    decision: str,
    reason: dict[str, Any] | None = None,
    actor: str = "growth_agent",
    automation_run_id: int | None = None,
    draft_id: int | None = None,
    post_id: int | None = None,
) -> DecisionLog:
    log = DecisionLog(
        automation_run_id=automation_run_id,
        draft_id=draft_id,
        post_id=post_id,
        stage=stage,
        decision=decision,
        reason_json=reason or {},
        actor=actor,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def recent_hypotheses(db: Session, *, limit: int = DEFAULT_MEMORY_LIMIT) -> list[Hypothesis]:
    return list(
        db.scalars(
            select(Hypothesis)
            .order_by(Hypothesis.created_at.desc(), Hypothesis.id.desc())
            .limit(_bounded_limit(limit))
        )
    )


def recent_draft_import_runs(
    db: Session, *, limit: int = DEFAULT_MEMORY_LIMIT
) -> list[DraftImportRun]:
    return list(
        db.scalars(
            select(DraftImportRun)
            .order_by(DraftImportRun.started_at.desc(), DraftImportRun.id.desc())
            .limit(_bounded_limit(limit))
        )
    )


def recent_decision_logs(
    db: Session,
    *,
    limit: int = DEFAULT_MEMORY_LIMIT,
    draft_id: int | None = None,
    automation_run_id: int | None = None,
) -> list[DecisionLog]:
    query = select(DecisionLog)
    if draft_id is not None:
        query = query.where(DecisionLog.draft_id == draft_id)
    if automation_run_id is not None:
        query = query.where(DecisionLog.automation_run_id == automation_run_id)
    return list(
        db.scalars(
            query.order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc()).limit(
                _bounded_limit(limit)
            )
        )
    )


def recent_automation_runs(
    db: Session, *, limit: int = DEFAULT_MEMORY_LIMIT
) -> list[AutomationRun]:
    return list(
        db.scalars(
            select(AutomationRun)
            .order_by(AutomationRun.started_at.desc(), AutomationRun.id.desc())
            .limit(_bounded_limit(limit))
        )
    )


def build_memory_context(db: Session, *, limit: int = DEFAULT_MEMORY_LIMIT) -> dict[str, Any]:
    runs = recent_automation_runs(db, limit=limit)
    return {
        "metrics_summary": metrics_summary(db),
        "playbook_rules": active_playbook_rules(db),
        "recent_hypotheses": recent_hypotheses(db, limit=limit),
        "recent_draft_import_runs": recent_draft_import_runs(db, limit=limit),
        "recent_decision_logs": recent_decision_logs(db, limit=limit),
        "recent_automation_runs": runs,
        "last_automation_run": runs[0] if runs else None,
    }


def sanitize_json(value: Any, settings: Settings) -> Any:
    if isinstance(value, str):
        return _redact(value, settings)
    if isinstance(value, dict):
        return {key: sanitize_json(item, settings) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_json(item, settings) for item in value]
    return value


def _redact(value: str, settings: Settings) -> str:
    safe_value = value
    secrets = (
        settings.growth_agent_api_key,
        settings.postiz_api_key,
        settings.x_bearer_token,
        settings.database_url,
    )
    for secret in secrets:
        if secret and len(secret) >= 8:
            safe_value = safe_value.replace(secret, "****")
    return safe_value


def _bounded_limit(limit: int) -> int:
    return max(1, min(limit, MAX_MEMORY_LIMIT))
