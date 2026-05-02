from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Idea(Base, TimestampMixin):
    __tablename__ = "ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(80), default="manual")
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    audience: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="new")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    drafts: Mapped[list["Draft"]] = relationship(back_populates="idea")


class Draft(Base, TimestampMixin):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id"))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="generated", index=True)
    risk_level: Mapped[str | None] = mapped_column(String(40), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_url: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    duplicate_of_draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("drafts.id"), nullable=True
    )
    duplicate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evaluation_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    idea: Mapped[Idea] = relationship(back_populates="drafts")
    posts: Mapped[list["Post"]] = relationship(back_populates="draft")


class Post(Base, TimestampMixin):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("drafts.id"), unique=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="scheduled", index=True)
    postiz_post_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    postiz_integration_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    x_post_id: Mapped[str | None] = mapped_column(String(160), nullable=True, unique=True)
    x_post_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    x_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    has_url: Mapped[bool] = mapped_column(Boolean, default=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)

    draft: Mapped[Draft] = relationship(back_populates="posts")
    metrics: Mapped[list["MetricSnapshot"]] = relationship(back_populates="post")


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    replies: Mapped[int] = mapped_column(Integer, default=0)
    reposts: Mapped[int] = mapped_column(Integer, default=0)
    quotes: Mapped[int] = mapped_column(Integer, default=0)
    bookmarks: Mapped[int] = mapped_column(Integer, default=0)

    post: Mapped[Post] = relationship(back_populates="metrics")


class Experiment(Base, TimestampMixin):
    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    hypothesis: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="active")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PlaybookRule(Base, TimestampMixin):
    __tablename__ = "playbook_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str] = mapped_column(Text)
    weight: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class FeedbackRun(Base, TimestampMixin):
    __tablename__ = "feedback_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    summary: Mapped[str] = mapped_column(Text)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    recommendations_json: Mapped[list[str]] = mapped_column(JSON, default=list)


class AutomationRun(Base):
    __tablename__ = "automation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="running", index=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_posting_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_drafts_count: Mapped[int] = mapped_column(Integer, default=0)
    evaluated_drafts_count: Mapped[int] = mapped_column(Integer, default=0)
    auto_schedule_candidates_count: Mapped[int] = mapped_column(Integer, default=0)
    auto_scheduled_count: Mapped[int] = mapped_column(Integer, default=0)
    dry_run_scheduled_count: Mapped[int] = mapped_column(Integer, default=0)
    live_scheduled_count: Mapped[int] = mapped_column(Integer, default=0)
    approval_required_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    frequency_limited_count: Mapped[int] = mapped_column(Integer, default=0)
    reconciled_count: Mapped[int] = mapped_column(Integer, default=0)
    metrics_collected_count: Mapped[int] = mapped_column(Integer, default=0)
    metrics_skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    error_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    errors_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
