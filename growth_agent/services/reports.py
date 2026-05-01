from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.models import FeedbackRun, Post
from growth_agent.services.metrics import metrics_summary


def weekly_report(db: Session) -> tuple[datetime, datetime, str]:
    period_end = datetime.now(UTC)
    period_start = period_end - timedelta(days=7)
    summary = metrics_summary(db)
    posts = list(
        db.scalars(
            select(Post)
            .where(Post.created_at >= period_start)
            .order_by(Post.created_at.desc())
            .limit(10)
        )
    )
    latest_feedback = db.scalar(select(FeedbackRun).order_by(FeedbackRun.created_at.desc()))

    lines = [
        "# Weekly Growth Agent Report",
        "",
        f"Period: {period_start.date().isoformat()} to {period_end.date().isoformat()}",
        "",
        "## Metrics",
        "",
        f"- Posts tracked: {summary['posts']}",
        f"- Impressions: {summary['impressions']}",
        f"- Engagements: {summary['engagement_total']}",
        f"- Engagement rate: {summary['engagement_rate']:.2%}",
        "",
        "## Recent Posts",
        "",
    ]
    if posts:
        lines.extend(f"- Post {post.id}: {post.status}, has_url={post.has_url}" for post in posts)
    else:
        lines.append("- No posts scheduled or tracked this week.")

    lines.extend(["", "## Feedback", ""])
    if latest_feedback:
        lines.append(latest_feedback.summary)
        lines.extend(f"- {item}" for item in latest_feedback.recommendations_json)
    else:
        lines.append("No feedback run has been recorded yet.")

    return period_start, period_end, "\n".join(lines)
