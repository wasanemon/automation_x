from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from growth_agent.clients.postiz import ExternalClientError
from growth_agent.clients.x_api import XApiClient
from growth_agent.models import MetricSnapshot, Post
from growth_agent.services.text import truncate_sentence


@dataclass(frozen=True)
class MetricsCollectItem:
    post_id: int
    status: Literal["collected", "skipped", "error"]
    x_post_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class MetricsCollectOutcome:
    results: list[MetricsCollectItem]

    @property
    def collected(self) -> int:
        return sum(1 for item in self.results if item.status == "collected")

    @property
    def skipped(self) -> int:
        return sum(1 for item in self.results if item.status == "skipped")

    @property
    def errors(self) -> int:
        return sum(1 for item in self.results if item.status == "error")


def collect_metrics(
    db: Session,
    x_client: XApiClient,
    post_ids: list[int] | None = None,
) -> MetricsCollectOutcome:
    posts = _metric_candidates(db, post_ids)
    results: list[MetricsCollectItem] = []

    now = datetime.now(UTC)
    for post in posts:
        if post.dry_run:
            results.append(
                MetricsCollectItem(
                    post_id=post.id,
                    status="skipped",
                    x_post_id=post.x_post_id,
                    reason="Post is dry_run=true.",
                )
            )
            continue
        if not post.x_post_id:
            results.append(
                MetricsCollectItem(
                    post_id=post.id,
                    status="skipped",
                    reason="Post has no x_post_id.",
                )
            )
            continue

        try:
            metrics = x_client.get_post_metrics(post.x_post_id)
        except ExternalClientError as exc:
            results.append(
                MetricsCollectItem(
                    post_id=post.id,
                    status="error",
                    x_post_id=post.x_post_id,
                    reason=str(exc),
                )
            )
            continue

        snapshot = MetricSnapshot(
            post_id=post.id,
            collected_at=now,
            impressions=metrics.impressions,
            likes=metrics.likes,
            replies=metrics.replies,
            reposts=metrics.reposts,
            quotes=metrics.quotes,
            bookmarks=metrics.bookmarks,
        )
        post.status = "published"
        if post.published_at is None:
            post.published_at = now
        db.add(snapshot)
        db.add(post)
        results.append(
            MetricsCollectItem(
                post_id=post.id,
                status="collected",
                x_post_id=post.x_post_id,
            )
        )
    db.commit()
    return MetricsCollectOutcome(results=results)


def count_metric_candidates(db: Session, post_ids: list[int] | None = None) -> int:
    return len(
        [
            post
            for post in _metric_candidates(db, post_ids)
            if post.x_post_id and not post.dry_run
        ]
    )


def _metric_candidates(db: Session, post_ids: list[int] | None = None) -> list[Post]:
    query = select(Post)
    if post_ids is not None:
        query = query.where(Post.id.in_(post_ids))
    else:
        query = query.where(Post.x_post_id.is_not(None))
    return list(db.scalars(query.order_by(Post.id.asc())))


def latest_metric_snapshots(db: Session) -> list[MetricSnapshot]:
    latest_ids = (
        select(func.max(MetricSnapshot.id).label("id"))
        .group_by(MetricSnapshot.post_id)
        .subquery()
    )
    return list(
        db.scalars(
            select(MetricSnapshot)
            .join(latest_ids, MetricSnapshot.id == latest_ids.c.id)
            .order_by(MetricSnapshot.post_id.asc())
        )
    )


def metrics_summary(db: Session) -> dict[str, int | float | list[dict[str, int | str]]]:
    snapshots = latest_metric_snapshots(db)
    totals = {
        "posts": len(snapshots),
        "impressions": sum(snapshot.impressions for snapshot in snapshots),
        "likes": sum(snapshot.likes for snapshot in snapshots),
        "replies": sum(snapshot.replies for snapshot in snapshots),
        "reposts": sum(snapshot.reposts for snapshot in snapshots),
        "quotes": sum(snapshot.quotes for snapshot in snapshots),
        "bookmarks": sum(snapshot.bookmarks for snapshot in snapshots),
    }
    engagement_total = (
        totals["likes"]
        + totals["replies"]
        + totals["reposts"]
        + totals["quotes"]
        + totals["bookmarks"]
    )
    impressions = totals["impressions"]
    engagement_rate = round(engagement_total / impressions, 4) if impressions else 0.0
    top_posts = sorted(
        snapshots,
        key=lambda snapshot: (
            snapshot.impressions,
            (
                snapshot.likes
                + snapshot.replies
                + snapshot.reposts
                + snapshot.quotes
                + snapshot.bookmarks
            ),
            snapshot.post_id,
        ),
        reverse=True,
    )[:5]
    return {
        "total_posts_with_metrics": len(snapshots),
        "latest_snapshot_count": len(snapshots),
        "total_impressions": totals["impressions"],
        "total_likes": totals["likes"],
        "total_reposts": totals["reposts"],
        "total_replies": totals["replies"],
        "total_quotes": totals["quotes"],
        "total_bookmarks": totals["bookmarks"],
        "average_engagement_rate": engagement_rate,
        "top_posts": [
            {
                "post_id": snapshot.post_id,
                "x_post_id": snapshot.post.x_post_id or "",
                "text_preview": truncate_sentence(snapshot.post.content, 90),
                "impressions": snapshot.impressions,
                "likes": snapshot.likes,
                "replies": snapshot.replies,
                "reposts": snapshot.reposts,
                "quotes": snapshot.quotes,
                "bookmarks": snapshot.bookmarks,
            }
            for snapshot in top_posts
        ],
        **totals,
        "engagement_total": engagement_total,
        "engagement_rate": engagement_rate,
    }
