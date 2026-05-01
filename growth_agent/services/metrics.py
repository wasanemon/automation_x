from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from growth_agent.clients.x_api import XApiClient
from growth_agent.models import MetricSnapshot, Post


def collect_metrics(
    db: Session,
    x_client: XApiClient,
    post_ids: list[int] | None = None,
) -> tuple[int, int]:
    query = select(Post).where(Post.x_post_id.is_not(None))
    if post_ids is not None:
        query = query.where(Post.id.in_(post_ids))

    posts = list(db.scalars(query))
    collected = 0
    skipped = 0
    for post in posts:
        if not post.x_post_id:
            skipped += 1
            continue
        metrics = x_client.get_post_metrics(post.x_post_id)
        snapshot = MetricSnapshot(
            post_id=post.id,
            collected_at=datetime.now(UTC),
            impressions=metrics.impressions,
            likes=metrics.likes,
            replies=metrics.replies,
            reposts=metrics.reposts,
            quotes=metrics.quotes,
            bookmarks=metrics.bookmarks,
        )
        post.status = "published"
        if post.published_at is None:
            post.published_at = datetime.now(UTC)
        db.add(snapshot)
        db.add(post)
        collected += 1
    db.commit()
    return collected, skipped


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


def metrics_summary(db: Session) -> dict[str, int | float]:
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
    return {
        **totals,
        "engagement_total": engagement_total,
        "engagement_rate": round(engagement_total / impressions, 4) if impressions else 0.0,
    }
