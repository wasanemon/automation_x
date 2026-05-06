from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IdeaIngestRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1)
    source: str = Field(default="manual", max_length=80)
    audience: str | None = Field(default=None, max_length=160)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IdeaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    title: str
    description: str
    audience: str | None
    status: str
    metadata_json: dict[str, Any]
    created_at: datetime


class DraftGenerateRequest(BaseModel):
    idea_id: int
    count: int = Field(default=3, ge=1, le=5)


class DraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    idea_id: int
    content: str
    status: str
    risk_level: str | None
    score: int | None
    has_url: bool
    requires_approval: bool
    duplicate_of_draft_id: int | None
    duplicate_reason: str | None
    evaluation_notes: list[str]
    created_at: datetime


class EvaluationResponse(BaseModel):
    draft: DraftResponse
    can_auto_schedule: bool


class ApprovalRequest(BaseModel):
    reviewer: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class RejectRequest(BaseModel):
    reviewer: str | None = Field(default=None, max_length=120)
    reason: str = Field(default="Rejected by reviewer", max_length=500)


class ScheduleDraftRequest(BaseModel):
    scheduled_for: datetime | None = None


class PostResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    draft_id: int
    content: str
    status: str
    postiz_post_id: str | None
    postiz_integration_id: str | None
    x_post_id: str | None
    x_post_created_at: datetime | None
    x_reconciled_at: datetime | None
    scheduled_for: datetime
    published_at: datetime | None
    has_url: bool
    dry_run: bool
    created_at: datetime


class ReconcileMapping(BaseModel):
    post_id: int
    x_post_id: str = Field(min_length=1, max_length=160)

    @field_validator("x_post_id")
    @classmethod
    def validate_x_post_id(cls, value: str) -> str:
        if not value.isdecimal():
            raise ValueError("x_post_id must be a numeric string.")
        return value


class ReconcileRequest(BaseModel):
    mappings: list[ReconcileMapping] = Field(default_factory=list)
    lookback_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    lookback_days: int | None = Field(default=None, ge=1, le=30)
    force: bool = False


class ReconcileResultItem(BaseModel):
    post_id: int | None = None
    status: Literal["matched", "skipped", "ambiguous", "error"]
    x_post_id: str | None = None
    score: float | None = None
    reason: str | None = None


class ReconcileResponse(BaseModel):
    reconciled: int
    matched: int
    skipped: int
    ambiguous: int
    errors: list[ReconcileResultItem] = Field(default_factory=list)
    results: list[ReconcileResultItem] = Field(default_factory=list)
    posts: list[PostResponse]


class MetricsCollectRequest(BaseModel):
    post_id: int | None = None
    post_ids: list[int] | None = None


class MetricsCollectResultItem(BaseModel):
    post_id: int
    status: Literal["collected", "skipped", "error"]
    x_post_id: str | None = None
    reason: str | None = None


class MetricsCollectResponse(BaseModel):
    collected: int
    skipped: int
    errors: int = 0
    results: list[MetricsCollectResultItem] = Field(default_factory=list)


class MetricsTopPost(BaseModel):
    post_id: int
    x_post_id: str
    text_preview: str
    impressions: int
    likes: int
    replies: int
    reposts: int
    quotes: int
    bookmarks: int


class MetricsSummaryResponse(BaseModel):
    total_posts_with_metrics: int
    latest_snapshot_count: int
    total_impressions: int
    total_likes: int
    total_reposts: int
    total_replies: int
    total_quotes: int
    total_bookmarks: int
    average_engagement_rate: float
    top_posts: list[MetricsTopPost]
    posts: int
    impressions: int
    likes: int
    replies: int
    reposts: int
    quotes: int
    bookmarks: int
    engagement_total: int
    engagement_rate: float


class FeedbackRunResponse(BaseModel):
    id: int
    summary: str
    recommendations: list[str]


class PlaybookRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    weight: int
    is_active: bool


class WeeklyReportResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    report: str


class AutomationRunHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    dry_run: bool
    auto_posting_enabled: bool
    kill_switch_active: bool
    created_drafts_count: int
    evaluated_drafts_count: int
    auto_schedule_candidates_count: int
    auto_scheduled_count: int
    dry_run_scheduled_count: int
    live_scheduled_count: int
    approval_required_count: int
    rejected_count: int
    duplicate_skipped_count: int
    frequency_limited_count: int
    reconciled_count: int
    metrics_collected_count: int
    metrics_skipped_count: int
    llm_generated_drafts_count: int
    llm_hypotheses_count: int
    llm_skipped_count: int
    skipped_count: int
    error_json: list[dict[str, Any]]
    errors_json: list[str]
    summary_json: dict[str, Any]
    metadata_json: dict[str, Any]


class AutomationCycleResponse(BaseModel):
    cycle_id: int
    created_drafts_count: int
    evaluated_drafts_count: int
    auto_schedule_candidates_count: int
    auto_scheduled_count: int
    dry_run_scheduled_count: int
    live_scheduled_count: int
    approval_required_count: int
    rejected_count: int
    duplicate_skipped_count: int
    frequency_limited_count: int
    reconciled_count: int
    metrics_collected_count: int
    metrics_skipped_count: int
    llm_generated_drafts_count: int
    llm_hypotheses_count: int
    llm_skipped_count: int
    skipped_count: int
    errors: list[str] = Field(default_factory=list)
    error_details: list[dict[str, Any]] = Field(default_factory=list)
    next_recommended_action: str
    dry_run: bool
    kill_switch_active: bool


class AutomationStatusResponse(BaseModel):
    auto_posting_enabled: bool
    scheduling_dry_run: bool
    kill_switch_active: bool
    today_auto_scheduled_count: int
    max_auto_schedule_per_day: int
    max_auto_schedule_per_cycle: int
    min_hours_between_auto_posts: int
    llm_generation_enabled: bool
    llm_analysis_enabled: bool
    llm_full_auto_enabled: bool
    openai_configured: bool
    next_post_available_at: datetime | None
    approval_waiting_draft_count: int
    unreconciled_post_count: int
    metrics_missing_post_count: int
    last_automation_run: AutomationRunHistoryResponse | None
    warnings: list[str]
    system_warnings: list[str]
