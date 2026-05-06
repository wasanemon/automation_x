from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from growth_agent.clients.postiz import ExternalClientError, PostizClient
from growth_agent.clients.x_api import XApiClient
from growth_agent.config import get_settings
from growth_agent.database import get_db
from growth_agent.deps import get_postiz_client, get_x_client, require_api_key
from growth_agent.models import Draft, Idea, Post
from growth_agent.schemas import (
    ApprovalRequest,
    AutomationCycleResponse,
    AutomationStatusResponse,
    DraftGenerateRequest,
    DraftImportRequest,
    DraftImportResponse,
    DraftResponse,
    EvaluationResponse,
    FeedbackRunResponse,
    IdeaIngestRequest,
    IdeaResponse,
    MetricsCollectRequest,
    MetricsCollectResponse,
    MetricsCollectResultItem,
    MetricsSummaryResponse,
    PlaybookRuleResponse,
    PostResponse,
    ReconcileRequest,
    ReconcileResponse,
    ReconcileResultItem,
    RejectRequest,
    ScheduleDraftRequest,
    WeeklyReportResponse,
)
from growth_agent.services.automation import automation_status, run_automation_cycle
from growth_agent.services.draft_imports import DraftImportSafetyError, import_draft_candidates
from growth_agent.services.drafts import create_drafts_for_idea
from growth_agent.services.evaluator import DraftEvaluator
from growth_agent.services.feedback import active_playbook_rules, run_feedback
from growth_agent.services.metrics import collect_metrics, count_metric_candidates, metrics_summary
from growth_agent.services.reconcile import apply_manual_mappings
from growth_agent.services.reconcile import reconcile_x_ids as reconcile_posts
from growth_agent.services.reports import weekly_report

app = FastAPI(title="Growth Agent", version="0.1.0")


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("select 1"))
    return {"status": "ok", "database": "ok"}


@app.post(
    "/ideas/ingest",
    response_model=IdeaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
def ingest_idea(payload: IdeaIngestRequest, db: Session = Depends(get_db)) -> Idea:
    idea = Idea(
        source=payload.source,
        title=payload.title,
        description=payload.description,
        audience=payload.audience,
        metadata_json=payload.metadata,
    )
    db.add(idea)
    db.commit()
    db.refresh(idea)
    return idea


@app.get("/ideas", response_model=list[IdeaResponse], dependencies=[Depends(require_api_key)])
def list_ideas(db: Session = Depends(get_db)) -> list[Idea]:
    return list(db.scalars(select(Idea).order_by(Idea.created_at.desc(), Idea.id.desc())))


@app.post(
    "/drafts/generate",
    response_model=list[DraftResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
def generate_drafts(payload: DraftGenerateRequest, db: Session = Depends(get_db)) -> list[Draft]:
    idea = db.get(Idea, payload.idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea not found.")
    return create_drafts_for_idea(db, idea, payload.count)


@app.post(
    "/drafts/import",
    response_model=DraftImportResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
def import_generated_drafts(
    payload: DraftImportRequest,
    db: Session = Depends(get_db),
) -> DraftImportResponse:
    idea = db.get(Idea, payload.idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea not found.")
    try:
        drafts = import_draft_candidates(db, idea, payload, get_settings())
    except DraftImportSafetyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DraftImportResponse(
        imported_count=len(drafts),
        drafts=[DraftResponse.model_validate(draft) for draft in drafts],
    )


@app.post(
    "/drafts/{draft_id}/evaluate",
    response_model=EvaluationResponse,
    dependencies=[Depends(require_api_key)],
)
def evaluate_draft(draft_id: int, db: Session = Depends(get_db)) -> EvaluationResponse:
    draft = _get_draft_or_404(db, draft_id)
    result = DraftEvaluator(get_settings()).apply(db, draft)
    return EvaluationResponse(
        draft=DraftResponse.model_validate(draft),
        can_auto_schedule=result.can_auto_schedule,
    )


@app.post(
    "/drafts/{draft_id}/approve",
    response_model=DraftResponse,
    dependencies=[Depends(require_api_key)],
)
def approve_draft(
    draft_id: int, payload: ApprovalRequest, db: Session = Depends(get_db)
) -> Draft:
    draft = _get_draft_or_404(db, draft_id)
    if draft.score is None:
        DraftEvaluator(get_settings()).apply(db, draft)
    notes = list(draft.evaluation_notes or [])
    if payload.reviewer or payload.note:
        notes.append(f"Approved by {payload.reviewer or 'reviewer'}: {payload.note or 'no note'}")
    draft.status = "approved"
    draft.approved_at = datetime.now(UTC)
    draft.evaluation_notes = notes
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


@app.post(
    "/drafts/{draft_id}/reject",
    response_model=DraftResponse,
    dependencies=[Depends(require_api_key)],
)
def reject_draft(draft_id: int, payload: RejectRequest, db: Session = Depends(get_db)) -> Draft:
    draft = _get_draft_or_404(db, draft_id)
    notes = list(draft.evaluation_notes or [])
    notes.append(f"Rejected by {payload.reviewer or 'reviewer'}: {payload.reason}")
    draft.status = "rejected"
    draft.rejected_at = datetime.now(UTC)
    draft.evaluation_notes = notes
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


@app.post(
    "/drafts/{draft_id}/schedule",
    response_model=PostResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
def schedule_draft(
    draft_id: int,
    payload: ScheduleDraftRequest,
    db: Session = Depends(get_db),
    postiz_client: PostizClient = Depends(get_postiz_client),
) -> Post:
    settings = get_settings()
    draft = _get_draft_or_404(db, draft_id)
    evaluator = DraftEvaluator(settings)
    if draft.score is None or draft.status in {"generated", "evaluated", "approved"}:
        evaluator.apply(db, draft)

    existing_post = db.scalar(
        select(Post).where(Post.draft_id == draft.id).order_by(Post.created_at.desc())
    )
    if existing_post is not None:
        raise HTTPException(
            status_code=409,
            detail="Draft already has a schedule record; inspect /posts before retrying.",
        )
    if draft.duplicate_of_draft_id is not None:
        raise HTTPException(status_code=409, detail=draft.duplicate_reason or "Duplicate draft.")
    if draft.status == "rejected":
        raise HTTPException(status_code=409, detail="Rejected drafts cannot be scheduled.")
    if draft.requires_approval and draft.status != "approved":
        raise HTTPException(status_code=409, detail="Human approval is required before scheduling.")
    if not draft.requires_approval and not evaluator.can_auto_schedule(draft):
        raise HTTPException(
            status_code=409,
            detail="Draft does not meet auto-scheduling thresholds.",
        )

    scheduled_for = payload.scheduled_for or (datetime.now(UTC) + timedelta(hours=1))
    post = Post(
        draft_id=draft.id,
        content=draft.content,
        status="scheduled" if settings.scheduling_dry_run else "scheduling",
        scheduled_for=scheduled_for,
        has_url=draft.has_url,
        dry_run=settings.scheduling_dry_run,
    )

    if settings.scheduling_dry_run:
        draft.status = "scheduled"
        db.add(draft)
        db.add(post)
        db.commit()
        db.refresh(post)
        return post

    draft.status = "scheduling"
    db.add(draft)
    db.add(post)
    db.commit()
    db.refresh(post)

    try:
        scheduled = postiz_client.schedule_x_post(
            content=draft.content,
            scheduled_for=scheduled_for,
            has_url=draft.has_url,
        )
    except ExternalClientError as exc:
        draft.status = "approved" if draft.approved_at else "evaluated"
        post.status = "schedule_failed"
        db.add(draft)
        db.add(post)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

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


@app.get("/posts", response_model=list[PostResponse], dependencies=[Depends(require_api_key)])
def list_posts(db: Session = Depends(get_db)) -> list[Post]:
    return list(db.scalars(select(Post).order_by(Post.created_at.desc(), Post.id.desc())))


@app.get(
    "/automation/status",
    response_model=AutomationStatusResponse,
    dependencies=[Depends(require_api_key)],
)
def get_automation_status(db: Session = Depends(get_db)) -> AutomationStatusResponse:
    return automation_status(db, get_settings())


@app.post(
    "/automation/run-cycle",
    response_model=AutomationCycleResponse,
    dependencies=[Depends(require_api_key)],
)
def run_automation_cycle_endpoint(
    db: Session = Depends(get_db),
    postiz_client: PostizClient = Depends(get_postiz_client),
    x_client: XApiClient = Depends(get_x_client),
) -> AutomationCycleResponse:
    return run_automation_cycle(db, postiz_client, x_client, get_settings())


@app.post(
    "/posts/reconcile-x-ids",
    response_model=ReconcileResponse,
    dependencies=[Depends(require_api_key)],
)
def reconcile_x_ids(
    payload: ReconcileRequest | None = None,
    db: Session = Depends(get_db),
    x_client: XApiClient = Depends(get_x_client),
) -> ReconcileResponse:
    payload = payload or ReconcileRequest()
    settings = get_settings()
    if payload.mappings:
        outcome = apply_manual_mappings(
            db,
            [(mapping.post_id, mapping.x_post_id) for mapping in payload.mappings],
            force=payload.force,
        )
    else:
        lookback_hours = payload.lookback_hours
        if lookback_hours is None and payload.lookback_days is not None:
            lookback_hours = payload.lookback_days * 24
        outcome = reconcile_posts(
            db,
            x_client,
            settings,
            lookback_hours=lookback_hours,
        )

    results = [
        ReconcileResultItem(
            post_id=item.post_id,
            status=item.status,
            x_post_id=item.x_post_id,
            score=item.score,
            reason=item.reason,
        )
        for item in outcome.results
    ]
    return ReconcileResponse(
        reconciled=outcome.matched,
        matched=outcome.matched,
        skipped=outcome.skipped,
        ambiguous=outcome.ambiguous,
        errors=[item for item in results if item.status == "error"],
        results=results,
        posts=outcome.posts,
    )


@app.post(
    "/metrics/collect",
    response_model=MetricsCollectResponse,
    dependencies=[Depends(require_api_key)],
)
def collect_post_metrics(
    payload: MetricsCollectRequest | None = None,
    db: Session = Depends(get_db),
    x_client: XApiClient = Depends(get_x_client),
) -> MetricsCollectResponse:
    payload = payload or MetricsCollectRequest()
    post_ids = _metric_post_ids(payload)
    if not get_settings().x_bearer_token:
        return MetricsCollectResponse(
            collected=0,
            skipped=count_metric_candidates(db, post_ids),
            errors=0,
            results=[],
        )
    outcome = collect_metrics(db, x_client, post_ids)
    return MetricsCollectResponse(
        collected=outcome.collected,
        skipped=outcome.skipped,
        errors=outcome.errors,
        results=[
            MetricsCollectResultItem(
                post_id=item.post_id,
                status=item.status,
                x_post_id=item.x_post_id,
                reason=item.reason,
            )
            for item in outcome.results
        ],
    )


@app.get(
    "/metrics/summary",
    response_model=MetricsSummaryResponse,
    dependencies=[Depends(require_api_key)],
)
def get_metrics_summary(db: Session = Depends(get_db)) -> dict[str, object]:
    return metrics_summary(db)


@app.post(
    "/feedback/run",
    response_model=FeedbackRunResponse,
    dependencies=[Depends(require_api_key)],
)
def run_feedback_endpoint(db: Session = Depends(get_db)) -> FeedbackRunResponse:
    feedback_run = run_feedback(db)
    return FeedbackRunResponse(
        id=feedback_run.id,
        summary=feedback_run.summary,
        recommendations=feedback_run.recommendations_json,
    )


@app.get(
    "/feedback/playbook",
    response_model=list[PlaybookRuleResponse],
    dependencies=[Depends(require_api_key)],
)
def get_playbook(db: Session = Depends(get_db)):
    return active_playbook_rules(db)


@app.get(
    "/reports/weekly",
    response_model=WeeklyReportResponse,
    dependencies=[Depends(require_api_key)],
)
def get_weekly_report(db: Session = Depends(get_db)) -> WeeklyReportResponse:
    period_start, period_end, report = weekly_report(db)
    return WeeklyReportResponse(period_start=period_start, period_end=period_end, report=report)


def _get_draft_or_404(db: Session, draft_id: int) -> Draft:
    draft = db.get(Draft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return draft


def _metric_post_ids(payload: MetricsCollectRequest) -> list[int] | None:
    if payload.post_id is not None:
        return [payload.post_id]
    return payload.post_ids
