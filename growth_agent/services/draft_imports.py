from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from growth_agent.config import Settings
from growth_agent.models import Draft, DraftImportRun, Hypothesis, Idea
from growth_agent.schemas import DraftImportRequest, HypothesisInput, ImportedDraftItem
from growth_agent.services.memory import create_decision_log, sanitize_json
from growth_agent.services.text import truncate_sentence

MCP_HUMAN_REVIEW_NOTE = "MCP requires human review."
MCP_LOW_CONFIDENCE_THRESHOLD = 0.7


class DraftImportSafetyError(ValueError):
    pass


@dataclass(frozen=True)
class DraftImportOutcome:
    drafts: list[Draft]
    import_run: DraftImportRun
    hypotheses: list[Hypothesis]


def import_draft_candidates(
    db: Session,
    idea: Idea,
    payload: DraftImportRequest,
    settings: Settings,
) -> DraftImportOutcome:
    _reject_known_secret_values(payload, settings)
    _validate_hypothesis_indexes(payload)
    context_snapshot = sanitize_json(payload.context_snapshot, settings)
    metadata = sanitize_json(payload.metadata, settings)
    hypothesis_inputs = [
        sanitize_json(hypothesis.model_dump(mode="json"), settings)
        for hypothesis in payload.hypotheses
    ]
    draft_inputs = [
        sanitize_json(item.model_dump(mode="json"), settings) for item in payload.drafts
    ]

    import_run = DraftImportRun(
        source=payload.source,
        idea_id=idea.id,
        status="running",
        prompt_version=payload.prompt_version,
        input_context_json=context_snapshot,
        hypotheses_json=hypothesis_inputs,
        output_json={"drafts": draft_inputs},
        imported_draft_ids_json=[],
        error_json=[],
        metadata_json=metadata,
    )
    db.add(import_run)
    db.commit()
    db.refresh(import_run)

    hypotheses, draft_hypothesis_ids = _create_hypotheses(db, idea, payload, settings)

    drafts = [
        Draft(
            idea_id=idea.id,
            content=item.content.strip(),
            status="generated",
            requires_approval=True,
            hypothesis_id=draft_hypothesis_ids[index],
            draft_import_run_id=import_run.id,
            metadata_json=_draft_metadata(payload.source, item, settings),
            evaluation_notes=_draft_notes(payload.source, item),
        )
        for index, item in enumerate(payload.drafts)
    ]

    idea.status = "processed"
    idea.metadata_json = _updated_idea_metadata(idea, payload)
    db.add(idea)
    db.add_all(drafts)
    db.commit()
    for draft in drafts:
        db.refresh(draft)

    import_run.status = "completed"
    import_run.finished_at = datetime.now(UTC)
    import_run.imported_draft_ids_json = [draft.id for draft in drafts]
    db.add(import_run)
    db.commit()
    db.refresh(import_run)

    for draft in drafts:
        create_decision_log(
            db,
            stage="draft_import",
            decision="imported",
            draft_id=draft.id,
            actor=payload.source,
            reason={
                "idea_id": idea.id,
                "draft_import_run_id": import_run.id,
                "hypothesis_id": draft.hypothesis_id,
                "target_metric": draft.metadata_json.get("target_metric"),
                "confidence": draft.metadata_json.get("confidence"),
                "requires_human_review_by_model": draft.metadata_json.get(
                    "requires_human_review_by_model"
                ),
            },
        )

    return DraftImportOutcome(drafts=drafts, import_run=import_run, hypotheses=hypotheses)


def requires_mcp_human_review(notes: list[str] | None) -> bool:
    return MCP_HUMAN_REVIEW_NOTE in (notes or [])


def _validate_hypothesis_indexes(payload: DraftImportRequest) -> None:
    for item in payload.drafts:
        if item.hypothesis_index is not None and item.hypothesis_index >= len(
            payload.hypotheses
        ):
            raise DraftImportSafetyError("hypothesis_index is out of range.")


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


def _draft_metadata(source: str, item: ImportedDraftItem, settings: Settings) -> dict[str, Any]:
    return sanitize_json(
        {
            "source": source,
            "hypothesis": item.hypothesis,
            "hypothesis_index": item.hypothesis_index,
            "target_metric": item.target_metric,
            "confidence": item.confidence,
            "risk_notes": item.risk_notes,
            "requires_human_review_by_model": item.requires_human_review_by_model,
        },
        settings,
    )


def _create_hypotheses(
    db: Session,
    idea: Idea,
    payload: DraftImportRequest,
    settings: Settings,
) -> tuple[list[Hypothesis], list[int | None]]:
    hypotheses: list[Hypothesis] = []
    draft_hypothesis_refs: list[int | None] = []
    for hypothesis_input in payload.hypotheses:
        hypotheses.append(_new_hypothesis(idea, payload.source, hypothesis_input, settings))

    for item in payload.drafts:
        if item.hypothesis_index is not None:
            if item.hypothesis_index >= len(hypotheses):
                raise DraftImportSafetyError("hypothesis_index is out of range.")
            draft_hypothesis_refs.append(item.hypothesis_index)
            continue
        if item.hypothesis:
            hypotheses.append(
                Hypothesis(
                    idea_id=idea.id,
                    source=payload.source,
                    statement=sanitize_json(item.hypothesis.strip(), settings),
                    target_metric=item.target_metric,
                    confidence=item.confidence,
                    status="proposed",
                    evidence_json=sanitize_json(item.risk_notes[:5], settings),
                    metadata_json={"created_from": "imported_draft_item"},
                )
            )
            draft_hypothesis_refs.append(len(hypotheses) - 1)
        else:
            draft_hypothesis_refs.append(None)

    if hypotheses:
        db.add_all(hypotheses)
        db.commit()
        for hypothesis in hypotheses:
            db.refresh(hypothesis)
    draft_hypothesis_ids = [
        hypotheses[index].id if index is not None else None for index in draft_hypothesis_refs
    ]
    return hypotheses, draft_hypothesis_ids


def _new_hypothesis(
    idea: Idea,
    source: str,
    item: HypothesisInput,
    settings: Settings,
) -> Hypothesis:
    return Hypothesis(
        idea_id=idea.id,
        source=source,
        statement=sanitize_json(item.statement.strip(), settings),
        target_metric=item.target_metric,
        confidence=item.confidence,
        status=item.status,
        evidence_json=sanitize_json(item.evidence, settings),
        metadata_json=sanitize_json(item.metadata, settings),
    )


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
