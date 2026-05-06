from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from growth_agent.clients.postiz import ExternalClientError
from growth_agent.clients.x_api import OwnedPost, XApiClient, XCredentialsMissingError
from growth_agent.config import Settings
from growth_agent.models import Post
from growth_agent.services.text import similarity

RECONCILE_STATUSES = {"scheduled", "posted", "published"}
AMBIGUOUS_SCORE_DELTA = 0.03


@dataclass(frozen=True)
class ReconcileItem:
    post_id: int | None
    status: Literal["matched", "skipped", "ambiguous", "error"]
    x_post_id: str | None = None
    score: float | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ReconcileOutcome:
    posts: list[Post]
    results: list[ReconcileItem]

    @property
    def matched(self) -> int:
        return sum(1 for item in self.results if item.status == "matched")

    @property
    def skipped(self) -> int:
        return sum(1 for item in self.results if item.status == "skipped")

    @property
    def ambiguous(self) -> int:
        return sum(1 for item in self.results if item.status == "ambiguous")

    @property
    def errors(self) -> list[ReconcileItem]:
        return [item for item in self.results if item.status == "error"]


@dataclass(frozen=True)
class MatchCandidate:
    owned_post: OwnedPost
    text_score: float
    time_score: float
    combined_score: float


def apply_manual_mappings(
    db: Session,
    mappings: list[tuple[int, str]],
    *,
    force: bool,
) -> ReconcileOutcome:
    now = datetime.now(UTC)
    posts: list[Post] = []
    results: list[ReconcileItem] = []

    for post_id, x_post_id in mappings:
        post = db.get(Post, post_id)
        if post is None:
            results.append(
                ReconcileItem(
                    post_id=post_id,
                    status="error",
                    x_post_id=x_post_id,
                    reason=f"Post {post_id} was not found.",
                )
            )
            continue
        if post.x_post_id and not force:
            results.append(
                ReconcileItem(
                    post_id=post.id,
                    status="skipped",
                    x_post_id=post.x_post_id,
                    reason="Post already has an x_post_id; pass force=true to overwrite.",
                )
            )
            continue

        post.x_post_id = x_post_id
        post.x_reconciled_at = now
        db.add(post)
        posts.append(post)
        results.append(
            ReconcileItem(
                post_id=post.id,
                status="matched",
                x_post_id=x_post_id,
                score=1.0,
                reason="Manual mapping saved.",
            )
        )

    db.commit()
    for post in posts:
        db.refresh(post)
    return ReconcileOutcome(posts=posts, results=results)


def reconcile_x_ids(
    db: Session,
    x_client: XApiClient,
    settings: Settings,
    *,
    lookback_hours: int | None = None,
) -> ReconcileOutcome:
    hours = lookback_hours or settings.x_reconcile_lookback_hours
    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(hours=hours)
    local_posts = _automatic_candidates(db, start_time)
    results: list[ReconcileItem] = []

    if not local_posts:
        return ReconcileOutcome(posts=[], results=[])
    if not settings.x_bearer_token or not settings.x_user_id:
        return ReconcileOutcome(
            posts=[],
            results=[
                ReconcileItem(
                    post_id=post.id,
                    status="skipped",
                    reason="X_BEARER_TOKEN and X_USER_ID are not configured.",
                )
                for post in local_posts
            ],
        )

    try:
        owned_posts = x_client.list_owned_posts(start_time=start_time, end_time=end_time)
    except XCredentialsMissingError as exc:
        return ReconcileOutcome(
            posts=[],
            results=[
                ReconcileItem(post_id=post.id, status="skipped", reason=str(exc))
                for post in local_posts
            ],
        )
    except ExternalClientError as exc:
        return ReconcileOutcome(
            posts=[],
            results=[ReconcileItem(post_id=None, status="error", reason=str(exc))],
        )

    matched_posts: list[Post] = []
    used_x_post_ids: set[str] = set()
    threshold = settings.x_reconcile_text_similarity_threshold
    for post in local_posts:
        candidates = [
            candidate
            for candidate in _rank_candidates(post, owned_posts, lookback_hours=hours)
            if candidate.owned_post.x_post_id not in used_x_post_ids
            and candidate.text_score >= threshold
        ]
        if not candidates:
            results.append(
                ReconcileItem(
                    post_id=post.id,
                    status="skipped",
                    reason="No owned X post met the text similarity threshold.",
                )
            )
            continue

        best = candidates[0]
        if len(candidates) > 1 and best.combined_score - candidates[1].combined_score < (
            AMBIGUOUS_SCORE_DELTA
        ):
            results.append(
                ReconcileItem(
                    post_id=post.id,
                    status="ambiguous",
                    x_post_id=best.owned_post.x_post_id,
                    score=round(best.combined_score, 4),
                    reason="Multiple X posts were too close to choose safely.",
                )
            )
            continue

        _apply_match(post, best.owned_post, end_time)
        used_x_post_ids.add(best.owned_post.x_post_id)
        db.add(post)
        matched_posts.append(post)
        results.append(
            ReconcileItem(
                post_id=post.id,
                status="matched",
                x_post_id=best.owned_post.x_post_id,
                score=round(best.combined_score, 4),
                reason=f"Text similarity {best.text_score:.2f}; time score {best.time_score:.2f}.",
            )
        )

    db.commit()
    for post in matched_posts:
        db.refresh(post)
    return ReconcileOutcome(posts=matched_posts, results=results)


def _automatic_candidates(db: Session, start_time: datetime) -> list[Post]:
    return list(
        db.scalars(
            select(Post)
            .where(Post.dry_run.is_(False))
            .where(Post.x_post_id.is_(None))
            .where(Post.postiz_post_id.is_not(None))
            .where(Post.content.is_not(None))
            .where(Post.content != "")
            .where(Post.status.in_(RECONCILE_STATUSES))
            .where(
                or_(
                    Post.created_at >= start_time,
                    Post.scheduled_for >= start_time,
                    Post.published_at >= start_time,
                )
            )
            .order_by(Post.scheduled_for.asc(), Post.created_at.asc(), Post.id.asc())
        )
    )


def _rank_candidates(
    post: Post,
    owned_posts: list[OwnedPost],
    *,
    lookback_hours: int,
) -> list[MatchCandidate]:
    candidates = [
        MatchCandidate(
            owned_post=owned_post,
            text_score=similarity(post.content, owned_post.text),
            time_score=_time_score(post, owned_post.created_at, lookback_hours),
            combined_score=0,
        )
        for owned_post in owned_posts
    ]
    scored = [
        MatchCandidate(
            owned_post=candidate.owned_post,
            text_score=candidate.text_score,
            time_score=candidate.time_score,
            combined_score=(candidate.text_score * 0.85) + (candidate.time_score * 0.15),
        )
        for candidate in candidates
    ]
    return sorted(
        scored,
        key=lambda candidate: (
            candidate.combined_score,
            candidate.text_score,
            candidate.time_score,
        ),
        reverse=True,
    )


def _time_score(post: Post, x_created_at: datetime | None, lookback_hours: int) -> float:
    if x_created_at is None:
        return 0.0
    local_time = _as_utc(post.scheduled_for or post.created_at)
    remote_time = _as_utc(x_created_at)
    delta_hours = abs((remote_time - local_time).total_seconds()) / 3600
    return max(0.0, 1.0 - (delta_hours / max(lookback_hours, 1)))


def _apply_match(post: Post, owned_post: OwnedPost, reconciled_at: datetime) -> None:
    post.x_post_id = owned_post.x_post_id
    post.x_post_created_at = owned_post.created_at
    post.x_reconciled_at = reconciled_at
    if owned_post.created_at is not None:
        post.published_at = owned_post.created_at
    post.status = "published"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
