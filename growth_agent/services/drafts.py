from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.models import Draft, Idea, PlaybookRule
from growth_agent.services.text import truncate_sentence


class DraftGenerator:
    """Deterministic MVP generator with a narrow interface for later LLM replacement."""

    def generate(self, idea: Idea, rules: list[PlaybookRule], count: int) -> list[str]:
        audience = idea.audience or "builders"
        title = idea.title.strip()
        description = truncate_sentence(idea.description, 120)
        rule_hint = self._rule_hint(rules)

        templates = [
            (
                f"{title}: {description}\n\n"
                f"For {audience}, the useful question is: what changes this week?"
            ),
            (
                f"A practical note for {audience}: {description}\n\n"
                "Small improvements compound when the team can repeat them."
            ),
            (
                f"Working on {title.lower()}? Start with the smallest repeatable workflow, "
                f"measure it, then let the next draft learn from the result. {rule_hint}"
            ),
            (
                f"The best marketing ideas usually begin as field notes. For {audience}: "
                f"{description}"
            ),
            (
                f"Growth loop prompt: turn '{title}' into one observable experiment, "
                "one useful post, and one lesson for the playbook."
            ),
        ]
        return templates[:count]

    @staticmethod
    def _rule_hint(rules: list[PlaybookRule]) -> str:
        active = [rule for rule in rules if rule.is_active]
        if not active:
            return "Keep it specific."
        strongest = sorted(active, key=lambda rule: rule.weight, reverse=True)[0]
        return strongest.description.rstrip(".")


def create_drafts_for_idea(db: Session, idea: Idea, count: int) -> list[Draft]:
    rules = list(
        db.scalars(
            select(PlaybookRule)
            .where(PlaybookRule.is_active.is_(True))
            .order_by(PlaybookRule.weight.desc(), PlaybookRule.id.asc())
        )
    )
    generator = DraftGenerator()
    drafts = [
        Draft(idea_id=idea.id, content=content)
        for content in generator.generate(idea, rules, count)
    ]
    db.add_all(drafts)
    db.commit()
    for draft in drafts:
        db.refresh(draft)
    return drafts
