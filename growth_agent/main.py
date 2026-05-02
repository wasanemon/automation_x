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
    DraftGenerateRequest,
    DraftResponse,
    EvaluationResponse,
    FeedbackRunResponse,
    IdeaIngestRequest,
    IdeaResponse,
    MetricsCollectRequest,
    MetricsCollectResponse,
    MetricsSummaryResponse,
    PlaybookRuleResponse,
    PostResponse,
    ReconcileRequest,
    ReconcileResponse,
    RejectRequest,
    ScheduleDraftRequest,
    WeeklyReportResponse,
)
from growth_agent.services.drafts import create_drafts_for_idea
from growth_agent.services.evaluator import DraftEvaluator
from growth_agent.services.feedback import active_playbook_rules, run_feedback
from growth_agent.services.metrics import collect_metrics, count_metric_candidates, metrics_summary
from growth_agent.services.reports import weekly_report
from growth_agent.services.text import similarity

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


@app.post(
    "/posts/reconcile-x-ids",
    response_model=ReconcileResponse,
    dependencies=[Depends(require_api_key)],
)
def reconcile_x_ids(
    payload: ReconcileRequest,
    db: Session = Depends(get_db),
    x_client: XApiClient = Depends(get_x_client),
) -> ReconcileResponse:
    reconciled_posts: list[Post] = []
    for mapping in payload.mappings:
        post = db.get(Post, mapping.post_id)
        if post is None:
            raise HTTPException(status_code=404, detail=f"Post {mapping.post_id} not found.")
        post.x_post_id = mapping.x_post_id
        db.add(post)
        reconciled_posts.append(post)

    if not payload.mappings:
        settings = get_settings()
        if not settings.x_bearer_token or not settings.x_user_id:
            return ReconcileResponse(reconciled=0, posts=[])
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(days=payload.lookback_days)
        try:
            owned_posts = x_client.list_owned_posts(start_time=start_time, end_time=end_time)
        except ExternalClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        local_posts = list(db.scalars(select(Post).where(Post.x_post_id.is_(None))))
        for local_post in local_posts:
            match = next(
                (
                    owned
                    for owned in owned_posts
                    if similarity(local_post.content, owned.text) >= 0.92
                ),
                None,
            )
            if match is not None:
                local_post.x_post_id = match.x_post_id
                if match.created_at is not None:
                    local_post.published_at = match.created_at
                    local_post.status = "published"
                db.add(local_post)
                reconciled_posts.append(local_post)

    db.commit()
    for post in reconciled_posts:
        db.refresh(post)
    return ReconcileResponse(reconciled=len(reconciled_posts), posts=reconciled_posts)


@app.post(
    "/metrics/collect",
    response_model=MetricsCollectResponse,
    dependencies=[Depends(require_api_key)],
)
def collect_post_metrics(
    payload: MetricsCollectRequest,
    db: Session = Depends(get_db),
    x_client: XApiClient = Depends(get_x_client),
) -> MetricsCollectResponse:
    if not get_settings().x_bearer_token:
        return MetricsCollectResponse(
            collected=0,
            skipped=count_metric_candidates(db, payload.post_ids),
        )
    try:
        collected, skipped = collect_metrics(db, x_client, payload.post_ids)
    except ExternalClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return MetricsCollectResponse(collected=collected, skipped=skipped)


@app.get(
    "/metrics/summary",
    response_model=MetricsSummaryResponse,
    dependencies=[Depends(require_api_key)],
)
def get_metrics_summary(db: Session = Depends(get_db)) -> dict[str, int | float]:
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
