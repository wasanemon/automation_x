from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    scheduled_for: datetime
    published_at: datetime | None
    has_url: bool
    dry_run: bool
    created_at: datetime


class ReconcileMapping(BaseModel):
    post_id: int
    x_post_id: str = Field(min_length=1, max_length=160)


class ReconcileRequest(BaseModel):
    mappings: list[ReconcileMapping] = Field(default_factory=list)
    lookback_days: int = Field(default=7, ge=1, le=30)


class ReconcileResponse(BaseModel):
    reconciled: int
    posts: list[PostResponse]


class MetricsCollectRequest(BaseModel):
    post_ids: list[int] | None = None


class MetricsCollectResponse(BaseModel):
    collected: int
    skipped: int


class MetricsSummaryResponse(BaseModel):
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
