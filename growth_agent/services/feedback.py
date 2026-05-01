from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.models import FeedbackRun, PlaybookRule
from growth_agent.services.metrics import latest_metric_snapshots, metrics_summary

DEFAULT_PLAYBOOK_RULES = [
    (
        "prefer_concrete_use_cases",
        "Prefer concrete use cases and workflows over broad positioning claims.",
        3,
    ),
    (
        "invite_specific_replies",
        "End with a specific question when the post is asking for learning.",
        2,
    ),
    (
        "avoid_unverified_claims",
        "Avoid absolute, urgent, or unverified performance claims.",
        5,
    ),
    (
        "review_url_posts",
        "Review URL-bearing drafts manually and track whether links reduce engagement.",
        4,
    ),
]


def ensure_default_playbook_rules(db: Session) -> None:
    existing_names = set(db.scalars(select(PlaybookRule.name)))
    for name, description, weight in DEFAULT_PLAYBOOK_RULES:
        if name not in existing_names:
            db.add(PlaybookRule(name=name, description=description, weight=weight, is_active=True))
    db.commit()


def active_playbook_rules(db: Session) -> list[PlaybookRule]:
    ensure_default_playbook_rules(db)
    return list(
        db.scalars(
            select(PlaybookRule)
            .where(PlaybookRule.is_active.is_(True))
            .order_by(PlaybookRule.weight.desc(), PlaybookRule.name.asc())
        )
    )


def run_feedback(db: Session) -> FeedbackRun:
    ensure_default_playbook_rules(db)
    summary = metrics_summary(db)
    snapshots = latest_metric_snapshots(db)
    recommendations: list[str] = []

    if not snapshots:
        recommendations.append("Collect metrics before changing playbook weights.")
    else:
        engagement_rate = float(summary["engagement_rate"])
        if engagement_rate >= 0.05:
            recommendations.append("Keep reinforcing concrete, repeatable workflow posts.")
            _bump_rule(db, "prefer_concrete_use_cases", 1)
        else:
            recommendations.append("Increase specificity and ask clearer questions in next drafts.")
            _bump_rule(db, "invite_specific_replies", 1)

        url_posts = [snapshot for snapshot in snapshots if snapshot.post.has_url]
        if url_posts:
            recommendations.append(
                "Compare URL-bearing posts against non-URL posts before scaling links."
            )
            _bump_rule(db, "review_url_posts", 1)

    feedback_run = FeedbackRun(
        summary=(
            f"Reviewed {summary['posts']} posts with {summary['impressions']} impressions "
            f"and {summary['engagement_total']} total engagements."
        ),
        metrics_json=summary,
        recommendations_json=recommendations,
    )
    db.add(feedback_run)
    db.commit()
    db.refresh(feedback_run)
    return feedback_run


def _bump_rule(db: Session, name: str, delta: int) -> None:
    rule = db.scalar(select(PlaybookRule).where(PlaybookRule.name == name))
    if rule is None:
        return
    rule.weight = min(10, rule.weight + delta)
    db.add(rule)
    db.commit()
