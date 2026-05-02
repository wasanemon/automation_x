from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from growth_agent.clients.postiz import ExternalClientError, PostizClient
from growth_agent.clients.x_api import XApiClient
from growth_agent.config import Settings
from growth_agent.models import AutomationRun, Draft, Idea, MetricSnapshot, Post
from growth_agent.schemas import AutomationCycleResponse, AutomationStatusResponse
from growth_agent.services.drafts import create_drafts_for_idea
from growth_agent.services.evaluator import DraftEvaluator
from growth_agent.services.metrics import collect_metrics, count_metric_candidates
from growth_agent.services.reconcile import reconcile_x_ids

AUTOMATION_DRAFT_BATCH_SIZE = 1
GENERATED_DRAFT_EVALUATION_LIMIT = 20
SCHEDULABLE_DRAFT_STATUSES = {"evaluated", "approved"}


@dataclass
class CycleCounters:
    created_drafts: int = 0
    evaluated_drafts: int = 0
    auto_scheduled: int = 0
    approval_required: int = 0
    rejected: int = 0
    reconciled: int = 0
    metrics_collected: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class ScheduleLimits:
    today_auto_scheduled_count: int
    next_post_available_at: datetime | None
    last_auto_scheduled_for: datetime | None


def run_automation_cycle(
    db: Session,
    postiz_client: PostizClient,
    x_client: XApiClient,
    settings: Settings,
) -> AutomationCycleResponse:
    live_scheduling_allowed = _live_scheduling_allowed(settings)
    run = AutomationRun(
        status="running",
        dry_run=not live_scheduling_allowed,
        error_json=[],
        summary_json={},
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    counters = CycleCounters()
    errors: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "kill_switch_active": settings.automation_kill_switch,
        "live_scheduling_allowed": live_scheduling_allowed,
        "schedule_decisions": [],
        "scheduled_posts": [],
    }

    try:
        created_drafts = _create_drafts_from_next_idea(db)
        counters.created_drafts = len(created_drafts)
        if not created_drafts:
            counters.skipped += 1
            summary["schedule_decisions"].append(
                {"status": "skipped", "reason": "No unprocessed ideas were available."}
            )

        evaluator = DraftEvaluator(settings)
        evaluated_drafts = _evaluate_generated_drafts(db, evaluator, errors, settings)
        counters.evaluated_drafts = len(evaluated_drafts)
        counters.approval_required += _mark_approval_required(db, evaluated_drafts)
        counters.rejected += sum(1 for draft in evaluated_drafts if draft.status == "rejected")

        schedule_candidates = _schedule_candidates(db, settings)
        _schedule_eligible_drafts(
            db,
            postiz_client,
            settings,
            schedule_candidates,
            counters,
            errors,
            summary,
            live_scheduling_allowed=live_scheduling_allowed,
        )

        reconcile_outcome = reconcile_x_ids(db, x_client, settings)
        counters.reconciled = reconcile_outcome.matched
        counters.skipped += reconcile_outcome.skipped + reconcile_outcome.ambiguous
        for item in reconcile_outcome.errors:
            errors.append(_error("reconcile", item.reason or "Reconcile failed.", settings))

        if settings.x_bearer_token:
            metrics_outcome = collect_metrics(db, x_client)
            counters.metrics_collected = metrics_outcome.collected
            counters.skipped += metrics_outcome.skipped
            for item in metrics_outcome.results:
                if item.status == "error":
                    errors.append(_error("metrics", item.reason or "Metrics failed.", settings))
        else:
            skipped_metrics = count_metric_candidates(db)
            counters.skipped += skipped_metrics
            if skipped_metrics:
                summary["metrics"] = {
                    "status": "skipped",
                    "reason": "X_BEARER_TOKEN is not configured.",
                    "skipped": skipped_metrics,
                }

        run.status = "completed_with_errors" if errors else "completed"
    except Exception as exc:  # pragma: no cover - protects run history for unexpected failures
        run.status = "failed"
        errors.append(_error("cycle", str(exc), settings))

    _finish_run(db, run, counters, errors, summary)
    return AutomationCycleResponse(
        cycle_id=run.id,
        created_drafts_count=run.created_drafts_count,
        evaluated_drafts_count=run.evaluated_drafts_count,
        auto_scheduled_count=run.auto_scheduled_count,
        approval_required_count=run.approval_required_count,
        rejected_count=run.rejected_count,
        reconciled_count=run.reconciled_count,
        metrics_collected_count=run.metrics_collected_count,
        skipped_count=run.skipped_count,
        errors=run.error_json,
        next_recommended_action=_next_recommended_action(run, settings),
        dry_run=run.dry_run,
        kill_switch_active=settings.automation_kill_switch,
    )


def automation_status(db: Session, settings: Settings) -> AutomationStatusResponse:
    limits = _schedule_limits(db, settings)
    last_run = db.scalar(select(AutomationRun).order_by(AutomationRun.started_at.desc()))
    return AutomationStatusResponse(
        auto_posting_enabled=settings.auto_posting_enabled,
        scheduling_dry_run=settings.scheduling_dry_run,
        kill_switch_active=settings.automation_kill_switch,
        today_auto_scheduled_count=limits.today_auto_scheduled_count,
        next_post_available_at=limits.next_post_available_at,
        approval_waiting_draft_count=_approval_waiting_draft_count(db),
        unreconciled_post_count=_unreconciled_post_count(db),
        metrics_missing_post_count=_metrics_missing_post_count(db),
        last_automation_run=last_run,
        system_warnings=_system_warnings(db, settings, limits),
    )


def _create_drafts_from_next_idea(db: Session) -> list[Draft]:
    idea = db.scalar(
        select(Idea)
        .where(Idea.status.in_(("new", "queued")))
        .order_by(Idea.created_at.asc(), Idea.id.asc())
        .limit(1)
    )
    if idea is None:
        return []

    drafts = create_drafts_for_idea(db, idea, AUTOMATION_DRAFT_BATCH_SIZE)
    idea.status = "processed"
    db.add(idea)
    db.commit()
    return drafts


def _evaluate_generated_drafts(
    db: Session,
    evaluator: DraftEvaluator,
    errors: list[dict[str, Any]],
    settings: Settings,
) -> list[Draft]:
    drafts = list(
        db.scalars(
            select(Draft)
            .where(Draft.status == "generated")
            .order_by(Draft.created_at.asc(), Draft.id.asc())
            .limit(GENERATED_DRAFT_EVALUATION_LIMIT)
        )
    )
    evaluated: list[Draft] = []
    for draft in drafts:
        try:
            evaluator.apply(db, draft)
        except Exception as exc:  # pragma: no cover - defensive per-draft isolation
            errors.append(_error("evaluate", f"Draft {draft.id}: {exc}", settings))
            continue
        evaluated.append(draft)
    return evaluated


def _mark_approval_required(db: Session, drafts: list[Draft]) -> int:
    count = 0
    for draft in drafts:
        if draft.requires_approval and draft.status != "rejected":
            draft.status = "approval_required"
            db.add(draft)
            count += 1
    if count:
        db.commit()
    return count


def _schedule_candidates(db: Session, settings: Settings) -> list[Draft]:
    existing_post = select(Post.id).where(Post.draft_id == Draft.id).exists()
    return list(
        db.scalars(
            select(Draft)
            .where(Draft.status.in_(SCHEDULABLE_DRAFT_STATUSES))
            .where(Draft.requires_approval.is_(False))
            .where(Draft.risk_level == "low")
            .where(Draft.score >= settings.auto_schedule_score_threshold)
            .where(Draft.duplicate_of_draft_id.is_(None))
            .where(~existing_post)
            .order_by(Draft.created_at.asc(), Draft.id.asc())
        )
    )


def _schedule_eligible_drafts(
    db: Session,
    postiz_client: PostizClient,
    settings: Settings,
    candidates: list[Draft],
    counters: CycleCounters,
    errors: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    live_scheduling_allowed: bool,
) -> None:
    if not candidates:
        return

    if settings.automation_kill_switch:
        counters.skipped += len(candidates)
        for draft in candidates:
            summary["schedule_decisions"].append(
                {
                    "draft_id": draft.id,
                    "status": "skipped",
                    "reason": "AUTOMATION_KILL_SWITCH=true.",
                }
            )
        return

    if settings.max_auto_schedule_per_cycle <= 0:
        counters.skipped += len(candidates)
        for draft in candidates:
            summary["schedule_decisions"].append(
                {
                    "draft_id": draft.id,
                    "status": "skipped",
                    "reason": "MAX_AUTO_SCHEDULE_PER_CYCLE is 0.",
                }
            )
        return

    limits = _schedule_limits(db, settings)
    if settings.max_auto_schedule_per_day <= 0:
        counters.skipped += len(candidates)
        for draft in candidates:
            summary["schedule_decisions"].append(
                {
                    "draft_id": draft.id,
                    "status": "skipped",
                    "reason": "MAX_AUTO_SCHEDULE_PER_DAY is 0.",
                }
            )
        return

    scheduled_this_cycle = 0
    last_scheduled_for = limits.last_auto_scheduled_for
    for draft in candidates:
        if scheduled_this_cycle >= settings.max_auto_schedule_per_cycle:
            counters.skipped += 1
            summary["schedule_decisions"].append(
                {
                    "draft_id": draft.id,
                    "status": "skipped",
                    "reason": "MAX_AUTO_SCHEDULE_PER_CYCLE reached.",
                }
            )
            continue
        if (
            limits.today_auto_scheduled_count + scheduled_this_cycle
            >= settings.max_auto_schedule_per_day
        ):
            counters.skipped += 1
            summary["schedule_decisions"].append(
                {
                    "draft_id": draft.id,
                    "status": "skipped",
                    "reason": "MAX_AUTO_SCHEDULE_PER_DAY reached.",
                }
            )
            continue

        scheduled_for = _scheduled_time(settings, last_scheduled_for)
        try:
            post = _create_schedule_record(
                db,
                postiz_client,
                draft,
                scheduled_for,
                live_scheduling_allowed=live_scheduling_allowed,
            )
        except ExternalClientError as exc:
            counters.skipped += 1
            errors.append(_error("schedule", str(exc), settings, draft_id=draft.id))
            summary["schedule_decisions"].append(
                {"draft_id": draft.id, "status": "error", "reason": "Postiz schedule failed."}
            )
            continue

        counters.auto_scheduled += 1
        scheduled_this_cycle += 1
        last_scheduled_for = scheduled_for
        summary["scheduled_posts"].append(
            {
                "post_id": post.id,
                "draft_id": draft.id,
                "scheduled_for": _iso_utc(post.scheduled_for),
                "dry_run": post.dry_run,
                "live": not post.dry_run,
            }
        )
        summary["schedule_decisions"].append(
            {
                "draft_id": draft.id,
                "post_id": post.id,
                "status": "scheduled",
                "dry_run": post.dry_run,
            }
        )


def _create_schedule_record(
    db: Session,
    postiz_client: PostizClient,
    draft: Draft,
    scheduled_for: datetime,
    *,
    live_scheduling_allowed: bool,
) -> Post:
    post = Post(
        draft_id=draft.id,
        content=draft.content,
        status="scheduling" if live_scheduling_allowed else "scheduled",
        scheduled_for=scheduled_for,
        has_url=draft.has_url,
        dry_run=not live_scheduling_allowed,
    )
    draft.status = "scheduling" if live_scheduling_allowed else "scheduled"
    db.add(draft)
    db.add(post)
    db.commit()
    db.refresh(post)

    if not live_scheduling_allowed:
        return post

    try:
        scheduled = postiz_client.schedule_x_post(
            content=draft.content,
            scheduled_for=scheduled_for,
            has_url=draft.has_url,
        )
    except ExternalClientError:
        draft.status = "evaluated"
        post.status = "schedule_failed"
        db.add(draft)
        db.add(post)
        db.commit()
        raise

    draft.status = "scheduled"
    post.status = "scheduled"
    post.postiz_post_id = scheduled.postiz_post_id
    post.postiz_integration_id = scheduled.integration_id
    post.dry_run = False
    db.add(draft)
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def _schedule_limits(db: Session, settings: Settings) -> ScheduleLimits:
    now = datetime.now(UTC)
    today_start = datetime.combine(now.date(), time.min, tzinfo=UTC)
    today_count = int(
        db.scalar(
            select(func.coalesce(func.sum(AutomationRun.auto_scheduled_count), 0)).where(
                AutomationRun.started_at >= today_start
            )
        )
        or 0
    )
    last_scheduled_for = _last_auto_scheduled_for(db)
    next_available = _next_post_available_at(
        now,
        settings,
        today_auto_scheduled_count=today_count,
        last_auto_scheduled_for=last_scheduled_for,
    )
    return ScheduleLimits(
        today_auto_scheduled_count=today_count,
        next_post_available_at=next_available,
        last_auto_scheduled_for=last_scheduled_for,
    )


def _last_auto_scheduled_for(db: Session) -> datetime | None:
    runs = db.scalars(select(AutomationRun).order_by(AutomationRun.started_at.desc())).all()
    scheduled_times: list[datetime] = []
    for run in runs:
        summary = run.summary_json or {}
        for item in summary.get("scheduled_posts", []):
            if not isinstance(item, dict):
                continue
            scheduled_for = item.get("scheduled_for")
            if not isinstance(scheduled_for, str):
                continue
            parsed = _parse_datetime(scheduled_for)
            if parsed is not None:
                scheduled_times.append(parsed)
    if not scheduled_times:
        return None
    return max(scheduled_times)


def _next_post_available_at(
    now: datetime,
    settings: Settings,
    *,
    today_auto_scheduled_count: int,
    last_auto_scheduled_for: datetime | None,
) -> datetime | None:
    if settings.max_auto_schedule_per_day <= 0 or settings.max_auto_schedule_per_cycle <= 0:
        return None

    next_available = now
    if today_auto_scheduled_count >= settings.max_auto_schedule_per_day:
        next_available = datetime.combine(
            (now + timedelta(days=1)).date(),
            time.min,
            tzinfo=UTC,
        )

    if last_auto_scheduled_for is not None:
        interval_available = last_auto_scheduled_for + timedelta(
            hours=settings.min_hours_between_auto_posts
        )
        next_available = max(next_available, interval_available)
    return next_available


def _scheduled_time(settings: Settings, last_scheduled_for: datetime | None) -> datetime:
    now = datetime.now(UTC)
    scheduled_for = now + timedelta(minutes=settings.default_schedule_delay_minutes)
    if last_scheduled_for is not None:
        scheduled_for = max(
            scheduled_for,
            last_scheduled_for + timedelta(hours=settings.min_hours_between_auto_posts),
        )
    return scheduled_for


def _approval_waiting_draft_count(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count(Draft.id))
            .where(Draft.requires_approval.is_(True))
            .where(Draft.status.not_in(("rejected", "scheduled", "scheduling")))
        )
        or 0
    )


def _unreconciled_post_count(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count(Post.id))
            .where(Post.dry_run.is_(False))
            .where(Post.postiz_post_id.is_not(None))
            .where(Post.x_post_id.is_(None))
            .where(Post.status.in_(("scheduled", "posted", "published")))
        )
        or 0
    )


def _metrics_missing_post_count(db: Session) -> int:
    posts_with_metrics = select(MetricSnapshot.post_id)
    return int(
        db.scalar(
            select(func.count(Post.id))
            .where(Post.dry_run.is_(False))
            .where(Post.x_post_id.is_not(None))
            .where(Post.id.not_in(posts_with_metrics))
        )
        or 0
    )


def _system_warnings(
    db: Session,
    settings: Settings,
    limits: ScheduleLimits,
) -> list[str]:
    warnings: list[str] = []
    if not settings.auto_posting_enabled:
        warnings.append("AUTO_POSTING_ENABLED=false; automation will not call Postiz live.")
    if settings.scheduling_dry_run:
        warnings.append("SCHEDULING_DRY_RUN=true; scheduling creates local dry-run records.")
    if settings.automation_kill_switch:
        warnings.append("AUTOMATION_KILL_SWITCH=true; automation scheduling is paused.")
    if limits.next_post_available_at is None:
        warnings.append("Auto scheduling is disabled by the current per-cycle or daily limit.")
    if settings.auto_posting_enabled and not settings.scheduling_dry_run:
        missing_postiz = [
            name
            for name, value in (
                ("POSTIZ_BASE_URL", settings.postiz_base_url),
                ("POSTIZ_API_KEY", settings.postiz_api_key),
                ("POSTIZ_X_INTEGRATION_ID", settings.postiz_x_integration_id),
            )
            if not value
        ]
        if missing_postiz:
            warnings.append(
                "Postiz live scheduling is enabled but required Postiz settings are missing."
            )
    if _unreconciled_post_count(db) and (not settings.x_bearer_token or not settings.x_user_id):
        warnings.append("X reconcile credentials are missing; x_post_id lookup will skip.")
    if _metrics_missing_post_count(db) and not settings.x_bearer_token:
        warnings.append("X_BEARER_TOKEN is missing; metrics collection will skip.")
    return warnings


def _finish_run(
    db: Session,
    run: AutomationRun,
    counters: CycleCounters,
    errors: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    run.finished_at = datetime.now(UTC)
    run.created_drafts_count = counters.created_drafts
    run.evaluated_drafts_count = counters.evaluated_drafts
    run.auto_scheduled_count = counters.auto_scheduled
    run.approval_required_count = counters.approval_required
    run.rejected_count = counters.rejected
    run.reconciled_count = counters.reconciled
    run.metrics_collected_count = counters.metrics_collected
    run.skipped_count = counters.skipped
    run.error_json = errors
    run.summary_json = summary
    db.add(run)
    db.commit()
    db.refresh(run)


def _live_scheduling_allowed(settings: Settings) -> bool:
    return (
        settings.auto_posting_enabled
        and not settings.scheduling_dry_run
        and not settings.automation_kill_switch
    )


def _next_recommended_action(run: AutomationRun, settings: Settings) -> str:
    if settings.automation_kill_switch:
        return (
            "Kill switch is active; review drafts and metrics, then re-enable scheduling when safe."
        )
    if run.error_json:
        return "Review automation run errors before enabling or increasing scheduling."
    if run.approval_required_count:
        return "Review approval-required drafts before scheduling them manually."
    if run.auto_scheduled_count and run.dry_run:
        return "Inspect dry-run schedule records; enable AUTO_POSTING only for the test X account."
    if run.auto_scheduled_count:
        return "Wait for Postiz publishing, then let the next cycle reconcile and collect metrics."
    return "Add a new idea or wait for posts that need reconciliation or metrics collection."


def _error(stage: str, message: str, settings: Settings, **extra: Any) -> dict[str, Any]:
    safe_message = message
    for secret in (
        settings.growth_agent_api_key,
        settings.postiz_api_key,
        settings.x_bearer_token,
    ):
        if secret:
            safe_message = safe_message.replace(secret, "****")
    return {"stage": stage, "message": safe_message, **extra}


def _iso_utc(value: datetime) -> str:
    value = _as_utc(value)
    return value.isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime | None:
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
