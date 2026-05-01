from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.config import Settings
from growth_agent.models import Draft, Post
from growth_agent.services.text import contains_url, similarity

HIGH_RISK_TERMS = {
    "guaranteed",
    "guarantee",
    "risk-free",
    "free money",
    "get rich",
    "investment advice",
    "financial advice",
    "medical advice",
    "legal advice",
    "hate",
    "harass",
}

CLAIM_TERMS = {
    "best",
    "only",
    "proven",
    "100%",
    "always",
    "never",
    "secret",
    "urgent",
    "limited time",
}


@dataclass(frozen=True)
class EvaluationResult:
    score: int
    risk_level: str
    has_url: bool
    requires_approval: bool
    duplicate_of_draft_id: int | None
    duplicate_reason: str | None
    notes: list[str]

    @property
    def can_auto_schedule(self) -> bool:
        return (
            self.risk_level == "low"
            and self.score >= 80
            and not self.requires_approval
            and self.duplicate_of_draft_id is None
        )


class DraftEvaluator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(self, db: Session, draft: Draft) -> EvaluationResult:
        text = draft.content
        lower_text = text.lower()
        notes: list[str] = []
        score = 95

        has_url = contains_url(text)
        if has_url:
            score -= 10
            notes.append("Contains a URL; mark has_url=true and require review.")

        high_terms = sorted(term for term in HIGH_RISK_TERMS if term in lower_text)
        claim_terms = sorted(term for term in CLAIM_TERMS if term in lower_text)
        if high_terms:
            score -= 45
            notes.append(f"High-risk language detected: {', '.join(high_terms)}.")
        if claim_terms:
            score -= 15
            notes.append(f"Claim or urgency language detected: {', '.join(claim_terms)}.")

        if len(text) > 280:
            score -= 20
            notes.append("Draft is longer than the X 280-character limit.")
        if len(text.strip()) < 20:
            score -= 10
            notes.append("Draft is very short.")
        if text.isupper() and len(text) > 20:
            score -= 20
            notes.append("Draft uses all caps.")

        duplicate_of_draft_id, duplicate_reason = self._find_duplicate(db, draft)
        if duplicate_of_draft_id is not None:
            score -= 50
            notes.append(duplicate_reason or "Duplicate or near-duplicate detected.")

        score = max(0, min(100, score))
        risk_level = self._risk_level(
            score,
            high_terms,
            claim_terms,
            has_url,
            duplicate_of_draft_id,
        )
        requires_approval = (
            risk_level in {"medium", "high"}
            or score < self.settings.auto_schedule_score_threshold
            or has_url
            or duplicate_of_draft_id is not None
        )

        if requires_approval and risk_level == "low":
            notes.append("Human approval required by safety policy.")
        if not notes:
            notes.append("Low-risk draft passed automated checks.")

        return EvaluationResult(
            score=score,
            risk_level=risk_level,
            has_url=has_url,
            requires_approval=requires_approval,
            duplicate_of_draft_id=duplicate_of_draft_id,
            duplicate_reason=duplicate_reason,
            notes=notes,
        )

    def apply(self, db: Session, draft: Draft) -> EvaluationResult:
        result = self.evaluate(db, draft)
        draft.score = result.score
        draft.risk_level = result.risk_level
        draft.has_url = result.has_url
        draft.requires_approval = result.requires_approval
        draft.duplicate_of_draft_id = result.duplicate_of_draft_id
        draft.duplicate_reason = result.duplicate_reason
        draft.evaluation_notes = result.notes
        if draft.status == "generated":
            draft.status = "evaluated"
        db.add(draft)
        db.commit()
        db.refresh(draft)
        return result

    def can_auto_schedule(self, draft: Draft) -> bool:
        return (
            draft.risk_level == "low"
            and (draft.score or 0) >= self.settings.auto_schedule_score_threshold
            and not draft.requires_approval
            and draft.duplicate_of_draft_id is None
        )

    def _find_duplicate(self, db: Session, draft: Draft) -> tuple[int | None, str | None]:
        threshold = self.settings.duplicate_similarity_threshold
        existing_drafts = db.scalars(select(Draft).where(Draft.id != draft.id)).all()
        for existing in existing_drafts:
            ratio = similarity(draft.content, existing.content)
            if ratio >= threshold:
                return existing.id, f"Near-duplicate of draft {existing.id} ({ratio:.2f})."

        existing_posts = db.scalars(select(Post)).all()
        for post in existing_posts:
            ratio = similarity(draft.content, post.content)
            if ratio >= threshold:
                return (
                    post.draft_id,
                    (
                        "Near-duplicate of already scheduled or published "
                        f"post {post.id} ({ratio:.2f})."
                    ),
                )
        return None, None

    @staticmethod
    def _risk_level(
        score: int,
        high_terms: list[str],
        claim_terms: list[str],
        has_url: bool,
        duplicate_of_draft_id: int | None,
    ) -> str:
        if high_terms or duplicate_of_draft_id is not None or score < 50:
            return "high"
        if claim_terms or has_url or score < 80:
            return "medium"
        return "low"
