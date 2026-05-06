from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from growth_agent.config import Settings
from growth_agent.models import Draft, Idea
from growth_agent.schemas import DraftImportRequest, ImportedDraftItem
from growth_agent.services.text import truncate_sentence

MCP_HUMAN_REVIEW_NOTE = "MCP requires human review."
MCP_LOW_CONFIDENCE_THRESHOLD = 0.7


class DraftImportSafetyError(ValueError):
    pass


def import_draft_candidates(
    db: Session,
    idea: Idea,
    payload: DraftImportRequest,
    settings: Settings,
) -> list[Draft]:
    _reject_known_secret_values(payload, settings)

    drafts = [
        Draft(
            idea_id=idea.id,
            content=item.content.strip(),
            status="generated",
            requires_approval=True,
            evaluation_notes=_draft_notes(payload.source, item),
        )
        for item in payload.drafts
    ]

    idea.status = "processed"
    idea.metadata_json = _updated_idea_metadata(idea, payload)
    db.add(idea)
    db.add_all(drafts)
    db.commit()
    for draft in drafts:
        db.refresh(draft)
    return drafts


def requires_mcp_human_review(notes: list[str] | None) -> bool:
    return MCP_HUMAN_REVIEW_NOTE in (notes or [])


def _draft_notes(source: str, item: ImportedDraftItem) -> list[str]:
    notes = [f"Imported from MCP source {source}."]
    if item.hypothesis:
        notes.append(f"MCP hypothesis: {truncate_sentence(item.hypothesis, 240)}")
    notes.append(f"MCP target metric: {item.target_metric}.")
    if item.confidence is not None:
        notes.append(f"MCP confidence: {item.confidence:.2f}.")
    for risk_note in item.risk_notes[:5]:
        clean_note = risk_note.strip()
        if clean_note:
            notes.append(f"MCP risk self-check: {truncate_sentence(clean_note, 240)}")
    if item.requires_human_review_by_model or (
        item.confidence is not None and item.confidence < MCP_LOW_CONFIDENCE_THRESHOLD
    ):
        notes.append(MCP_HUMAN_REVIEW_NOTE)
    return notes


def _updated_idea_metadata(idea: Idea, payload: DraftImportRequest) -> dict[str, Any]:
    metadata = dict(idea.metadata_json or {})
    imports = metadata.get("mcp_imports")
    if not isinstance(imports, list):
        imports = []
    imports.append(
        {
            "source": payload.source,
            "imported_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "draft_count": len(payload.drafts),
        }
    )
    metadata["mcp_imports"] = imports[-20:]
    return metadata


def _reject_known_secret_values(payload: DraftImportRequest, settings: Settings) -> None:
    secrets = [
        settings.growth_agent_api_key,
        settings.postiz_api_key,
        settings.x_bearer_token,
        settings.database_url,
    ]
    secret_values = [secret for secret in secrets if secret and len(secret) >= 12]
    if not secret_values:
        return

    for value in _strings(payload.model_dump(mode="json")):
        for secret in secret_values:
            if secret in value:
                raise DraftImportSafetyError(
                    "Import payload contains a configured secret value."
                )


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_strings(item))
        return strings
    if isinstance(value, list):
        strings: list[str] = []
        for item in value:
            strings.extend(_strings(item))
        return strings
    return []
