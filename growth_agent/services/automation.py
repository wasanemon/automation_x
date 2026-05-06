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
from growth_agent.services.memory import create_decision_log
from growth_agent.services.metrics import collect_metrics, count_metric_candidates
from growth_agent.services.reconcile import reconcile_x_ids

AUTOMATION_DRAFT_BATCH_SIZE = 1
GENERATED_DRAFT_EVALUATION_LIMIT = 20
SCHEDULABLE_DRAFT_STATUSES = {"evaluated", "approved"}


@dataclass
class CycleCounters:
    created_drafts: int = 0
    evaluated_drafts: int = 0
    auto_schedule_candidates: int = 0
    auto_scheduled: int = 0
    dry_run_scheduled: int = 0
    live_scheduled: int = 0
    approval_required: int = 0
    rejected: int = 0
    duplicate_skipped: int = 0
    frequency_limited: int = 0
    reconciled: int = 0
    metrics_collected: int = 0
    metrics_skipped: int = 0
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
        auto_posting_enabled=settings.auto_posting_enabled,
        kill_switch_active=settings.automation_kill_switch,
        error_json=[],
        errors_json=[],
        summary_json={},
        metadata_json={},
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
            create_decision_log(
                db,
                automation_run_id=run.id,
                stage="draft_generation",
                decision="skipped",
                actor="automation",
                reason={"reason": "No unprocessed ideas were available."},
            )
        else:
            for draft in created_drafts:
                create_decision_log(
                    db,
                    automation_run_id=run.id,
                    draft_id=draft.id,
                    stage="draft_generation",
                    decision="created",
                    actor="automation",
                    reason={"idea_id": draft.idea_id},
                )

        evaluator = DraftEvaluator(settings)
        evaluated_drafts = _evaluate_generated_drafts(db, evaluator, errors, settings, run.id)
        counters.evaluated_drafts = len(evaluated_drafts)
        counters.approval_required += _mark_approval_required(db, evaluated_drafts, run.id)
        counters.rejected += sum(1 for draft in evaluated_drafts if draft.status == "rejected")
        counters.duplicate_skipped += _duplicate_skipped_draft_count(db)

        schedule_candidates = _schedule_candidates(db, settings)
        counters.auto_schedule_candidates = len(schedule_candidates)
        _schedule_eligible_drafts(
            db,
            postiz_client,
            settings,
            schedule_candidates,
            counters,
            errors,
            summary,
            live_scheduling_allowed=live_scheduling_allowed,
            automation_run_id=run.id,
        )

        reconcile_outcome = reconcile_x_ids(db, x_client, settings)
        counters.reconciled = reconcile_outcome.matched
        counters.skipped += reconcile_outcome.skipped + reconcile_outcome.ambiguous
        for item in reconcile_outcome.results:
            create_decision_log(
                db,
                automation_run_id=run.id,
                post_id=item.post_id,
                stage="reconcile",
                decision=item.status,
                actor="automation",
                reason={
                    "x_post_id": item.x_post_id,
                    "score": item.score,
                    "reason": item.reason,
                },
            )
        for item in reconcile_outcome.errors:
            errors.append(_error("reconcile", item.reason or "Reconcile failed.", settings))

        if settings.x_bearer_token:
            metrics_outcome = collect_metrics(db, x_client)
            counters.metrics_collected = metrics_outcome.collected
            counters.metrics_skipped = metrics_outcome.skipped
            counters.skipped += metrics_outcome.skipped
            for item in metrics_outcome.results:
                create_decision_log(
                    db,
                    automation_run_id=run.id,
                    post_id=item.post_id,
                    stage="metrics",
                    decision=item.status,
                    actor="automation",
                    reason={
                        "x_post_id": item.x_post_id,
                        "reason": item.reason,
                    },
                )
                if item.status == "error":
                    errors.append(_error("metrics", item.reason or "Metrics failed.", settings))
        else:
            skipped_metrics = count_metric_candidates(db)
            counters.metrics_skipped = skipped_metrics
            counters.skipped += skipped_metrics
            if skipped_metrics:
                summary["metrics"] = {
                    "status": "skipped",
                    "reason": "X_BEARER_TOKEN is not configured.",
                    "skipped": skipped_metrics,
                }
                create_decision_log(
                    db,
                    automation_run_id=run.id,
                    stage="metrics",
                    decision="skipped",
                    actor="automation",
                    reason={
                        "reason": "X_BEARER_TOKEN is not configured.",
                        "skipped": skipped_metrics,
                    },
                )

        run.status = "completed_with_errors" if errors else "completed"
    except Exception as exc:  # pragma: no cover - protects run history for unexpected failures
        run.status = "failed"
        errors.append(_error("cycle", str(exc), settings))

    _finish_run(db, run, counters, errors, summary)
    return AutomationCycleResponse(
        cycle_id=run.id,
        created_drafts_count=run.created_drafts_count,
        evaluated_drafts_count=run.evaluated_drafts_count,
        auto_schedule_candidates_count=run.auto_schedule_candidates_count,
        auto_scheduled_count=run.auto_scheduled_count,
        dry_run_scheduled_count=run.dry_run_scheduled_count,
        live_scheduled_count=run.live_scheduled_count,
        approval_required_count=run.approval_required_count,
        rejected_count=run.rejected_count,
        duplicate_skipped_count=run.duplicate_skipped_count,
        frequency_limited_count=run.frequency_limited_count,
        reconciled_count=run.reconciled_count,
        metrics_collected_count=run.metrics_collected_count,
        metrics_skipped_count=run.metrics_skipped_count,
        skipped_count=run.skipped_count,
        errors=run.errors_json,
        error_details=run.error_json,
        next_recommended_action=_next_recommended_action(run, settings),
        dry_run=run.dry_run,
        kill_switch_active=settings.automation_kill_switch,
    )


def automation_status(db: Session, settings: Settings) -> AutomationStatusResponse:
    limits = _schedule_limits(db, settings)
    last_run = db.scalar(select(AutomationRun).order_by(AutomationRun.started_at.desc()))
    warnings = _system_warnings(db, settings, limits)
    return AutomationStatusResponse(
        auto_posting_enabled=settings.auto_posting_enabled,
        scheduling_dry_run=settings.scheduling_dry_run,
        kill_switch_active=settings.automation_kill_switch,
        today_auto_scheduled_count=limits.today_auto_scheduled_count,
        max_auto_schedule_per_day=settings.max_auto_schedule_per_day,
        max_auto_schedule_per_cycle=settings.max_auto_schedule_per_cycle,
        min_hours_between_auto_posts=settings.min_hours_between_auto_posts,
        next_post_available_at=limits.next_post_available_at,
        approval_waiting_draft_count=_approval_waiting_draft_count(db),
        unreconciled_post_count=_unreconciled_post_count(db),
        metrics_missing_post_count=_metrics_missing_post_count(db),
        last_automation_run=last_run,
        warnings=warnings,
        system_warnings=warnings,
    )


def _create_drafts_from_next_idea(db: Session) -> list[Draft]:
    idea = db.scalar(
        select(Idea)
        .where(Idea.status.in_(("new", "queued")))
        .order_by(Idea.created_at.asc(), Idea.id.asc())
        .limit(1)
    )
    if idea is None:
        if _has_pending_draft_work(db):
            return []
        idea = _create_default_idea(db)

    drafts = create_drafts_for_idea(db, idea, AUTOMATION_DRAFT_BATCH_SIZE)
    idea.status = "processed"
    db.add(idea)
    db.commit()
    return drafts


def _has_pending_draft_work(db: Session) -> bool:
    return bool(
        db.scalar(
            select(Draft.id)
            .where(Draft.status.in_(("generated", "evaluated", "approved", "approval_required")))
            .limit(1)
        )
    )


def _create_default_idea(db: Session) -> Idea:
    now = datetime.now(UTC)
    idea = Idea(
        source="automation",
        title=f"Automation loop note {now.date().isoformat()}",
        description=(
            "Share one practical note about keeping a small operating loop observable and safe."
        ),
        audience="builders",
        status="new",
        metadata_json={"created_by": "automation_default"},
    )
    db.add(idea)
    db.commit()
    db.refresh(idea)
    return idea


def _evaluate_generated_drafts(
    db: Session,
    evaluator: DraftEvaluator,
    errors: list[dict[str, Any]],
    settings: Settings,
    automation_run_id: int,
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
            create_decision_log(
                db,
                automation_run_id=automation_run_id,
                draft_id=draft.id,
                stage="evaluate",
                decision="error",
                actor="automation",
                reason={"reason": str(exc)},
            )
            continue
        create_decision_log(
            db,
            automation_run_id=automation_run_id,
            draft_id=draft.id,
            stage="evaluate",
            decision=_evaluation_decision(draft, evaluator),
            actor="automation",
            reason={
                "score": draft.score,
                "risk_level": draft.risk_level,
                "requires_approval": draft.requires_approval,
                "duplicate_of_draft_id": draft.duplicate_of_draft_id,
                "duplicate_reason": draft.duplicate_reason,
                "notes": draft.evaluation_notes,
            },
        )
        evaluated.append(draft)
    return evaluated


def _mark_approval_required(db: Session, drafts: list[Draft], automation_run_id: int) -> int:
    count = 0
    for draft in drafts:
        if draft.requires_approval and draft.status != "rejected":
            draft.status = "approval_required"
            db.add(draft)
            count += 1
            create_decision_log(
                db,
                automation_run_id=automation_run_id,
                draft_id=draft.id,
                stage="approval",
                decision="approval_required",
                actor="automation",
                reason={
                    "score": draft.score,
                    "risk_level": draft.risk_level,
                    "duplicate_of_draft_id": draft.duplicate_of_draft_id,
                },
            )
    if count:
        db.commit()
    return count


def _duplicate_skipped_draft_count(db: Session) -> int:
    existing_post = select(Post.id).where(Post.draft_id == Draft.id).exists()
    return int(
        db.scalar(
            select(func.count(Draft.id))
            .where(Draft.duplicate_of_draft_id.is_not(None))
            .where(Draft.status.not_in(("rejected", "scheduled", "scheduling")))
            .where(~existing_post)
        )
        or 0
    )


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
    automation_run_id: int,
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
            _log_schedule_decision(
                db,
                automation_run_id,
                draft,
                "skipped",
                {"reason": "AUTOMATION_KILL_SWITCH=true."},
            )
        return

    if settings.max_auto_schedule_per_cycle <= 0:
        counters.skipped += len(candidates)
        counters.frequency_limited += len(candidates)
        for draft in candidates:
            summary["schedule_decisions"].append(
                {
                    "draft_id": draft.id,
                    "status": "skipped",
                    "reason": "MAX_AUTO_SCHEDULE_PER_CYCLE is 0.",
                }
            )
            _log_schedule_decision(
                db,
                automation_run_id,
                draft,
                "frequency_limited",
                {"reason": "MAX_AUTO_SCHEDULE_PER_CYCLE is 0."},
            )
        return

    limits = _schedule_limits(db, settings)
    if settings.max_auto_schedule_per_day <= 0:
        counters.skipped += len(candidates)
        counters.frequency_limited += len(candidates)
        for draft in candidates:
            summary["schedule_decisions"].append(
                {
                    "draft_id": draft.id,
                    "status": "skipped",
                    "reason": "MAX_AUTO_SCHEDULE_PER_DAY is 0.",
                }
            )
            _log_schedule_decision(
                db,
                automation_run_id,
                draft,
                "frequency_limited",
                {"reason": "MAX_AUTO_SCHEDULE_PER_DAY is 0."},
            )
        return

    scheduled_this_cycle = 0
    last_scheduled_for = limits.last_auto_scheduled_for
    for draft in candidates:
        if scheduled_this_cycle >= settings.max_auto_schedule_per_cycle:
            counters.skipped += 1
            counters.frequency_limited += 1
            summary["schedule_decisions"].append(
                {
                    "draft_id": draft.id,
                    "status": "skipped",
                    "reason": "MAX_AUTO_SCHEDULE_PER_CYCLE reached.",
                }
            )
            _log_schedule_decision(
                db,
                automation_run_id,
                draft,
                "frequency_limited",
                {"reason": "MAX_AUTO_SCHEDULE_PER_CYCLE reached."},
            )
            continue
        if (
            limits.today_auto_scheduled_count + scheduled_this_cycle
            >= settings.max_auto_schedule_per_day
        ):
            counters.skipped += 1
            counters.frequency_limited += 1
            summary["schedule_decisions"].append(
                {
                    "draft_id": draft.id,
                    "status": "skipped",
                    "reason": "MAX_AUTO_SCHEDULE_PER_DAY reached.",
                }
            )
            _log_schedule_decision(
                db,
                automation_run_id,
                draft,
                "frequency_limited",
                {"reason": "MAX_AUTO_SCHEDULE_PER_DAY reached."},
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
            _log_schedule_decision(
                db,
                automation_run_id,
                draft,
                "error",
                {"reason": "Postiz schedule failed."},
            )
            continue

        if post.dry_run:
            counters.dry_run_scheduled += 1
        else:
            counters.live_scheduled += 1
        counters.auto_scheduled = counters.dry_run_scheduled + counters.live_scheduled
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
        _log_schedule_decision(
            db,
            automation_run_id,
            draft,
            "dry_run_scheduled" if post.dry_run else "live_scheduled",
            {
                "post_id": post.id,
                "scheduled_for": _iso_utc(post.scheduled_for),
                "dry_run": post.dry_run,
            },
            post_id=post.id,
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


def _evaluation_decision(draft: Draft, evaluator: DraftEvaluator) -> str:
    if draft.duplicate_of_draft_id is not None:
        return "duplicate"
    if draft.requires_approval:
        return "approval_required"
    if evaluator.can_auto_schedule(draft):
        return "auto_schedule_candidate"
    if draft.status == "rejected":
        return "rejected"
    return "evaluated"


def _log_schedule_decision(
    db: Session,
    automation_run_id: int,
    draft: Draft,
    decision: str,
    reason: dict[str, Any],
    *,
    post_id: int | None = None,
) -> None:
    create_decision_log(
        db,
        automation_run_id=automation_run_id,
        draft_id=draft.id,
        post_id=post_id,
        stage="schedule",
        decision=decision,
        actor="automation",
        reason=reason,
    )


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
        warnings.append(f"POSTIZ_* missing: {', '.join(missing_postiz)}.")
    missing_x = [
        name
        for name, value in (
            ("X_BEARER_TOKEN", settings.x_bearer_token),
            ("X_USER_ID", settings.x_user_id),
        )
        if not value
    ]
    if missing_x:
        warnings.append(f"X_* missing: {', '.join(missing_x)}; reconcile/metrics will skip.")
    if _unreconciled_post_count(db) and missing_x:
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
    run.auto_scheduled_count = counters.dry_run_scheduled + counters.live_scheduled
    run.created_drafts_count = counters.created_drafts
    run.evaluated_drafts_count = counters.evaluated_drafts
    run.auto_schedule_candidates_count = counters.auto_schedule_candidates
    run.dry_run_scheduled_count = counters.dry_run_scheduled
    run.live_scheduled_count = counters.live_scheduled
    run.approval_required_count = counters.approval_required
    run.rejected_count = counters.rejected
    run.duplicate_skipped_count = counters.duplicate_skipped
    run.frequency_limited_count = counters.frequency_limited
    run.reconciled_count = counters.reconciled
    run.metrics_collected_count = counters.metrics_collected
    run.metrics_skipped_count = counters.metrics_skipped
    run.skipped_count = counters.skipped
    run.error_json = errors
    run.errors_json = _error_messages(errors)
    run.summary_json = summary
    run.metadata_json = {
        "auto_posting_enabled": run.auto_posting_enabled,
        "kill_switch_active": run.kill_switch_active,
        "dry_run": run.dry_run,
        "live_scheduling_allowed": not run.dry_run,
    }
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


def _error_messages(errors: list[dict[str, Any]]) -> list[str]:
    messages: list[str] = []
    for error in errors:
        stage = error.get("stage")
        message = error.get("message")
        if stage and message:
            messages.append(f"{stage}: {message}")
        elif message:
            messages.append(str(message))
    return messages


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
