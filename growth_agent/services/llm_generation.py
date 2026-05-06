from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from growth_agent.clients.openai_client import OpenAIClient
from growth_agent.clients.postiz import ExternalClientError
from growth_agent.config import Settings
from growth_agent.models import Draft, Idea, LLMRun
from growth_agent.services.feedback import active_playbook_rules
from growth_agent.services.metrics import metrics_summary
from growth_agent.services.text import truncate_sentence

LLM_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["drafts", "hypotheses"],
    "properties": {
        "drafts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "content",
                    "hypothesis",
                    "target_audience",
                    "expected_metric",
                    "confidence",
                    "risk_notes",
                    "contains_url",
                    "contains_claim",
                    "requires_human_review_by_model",
                ],
                "properties": {
                    "content": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "target_audience": {"type": "string"},
                    "expected_metric": {
                        "type": "string",
                        "enum": [
                            "impressions",
                            "likes",
                            "replies",
                            "reposts",
                            "quotes",
                            "bookmarks",
                        ],
                    },
                    "confidence": {"type": "number"},
                    "risk_notes": {"type": "array", "items": {"type": "string"}},
                    "contains_url": {"type": "boolean"},
                    "contains_claim": {"type": "boolean"},
                    "requires_human_review_by_model": {"type": "boolean"},
                },
            },
        },
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "title",
                    "rationale",
                    "target_metric",
                    "expected_effect",
                    "confidence",
                ],
                "properties": {
                    "title": {"type": "string"},
                    "rationale": {"type": "string"},
                    "target_metric": {
                        "type": "string",
                        "enum": [
                            "impressions",
                            "likes",
                            "replies",
                            "reposts",
                            "quotes",
                            "bookmarks",
                        ],
                    },
                    "expected_effect": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
    },
}

SYSTEM_PROMPT = """You are the draft and hypothesis generator for Growth Agent.

Return only JSON that matches the provided schema.

Safety policy:
- Never create automated replies, mentions, likes, follows, reposts, DMs, or keyword outreach.
- Do not instruct the system to use X for posting; X API is read-only.
- Posting and scheduling can happen only through Postiz after deterministic safety gates.
- External URLs, shortened URLs, pricing/legal language, and strong claims require human review.
- Do not include or infer secrets, API keys, bearer tokens, database URLs, or credentials.
- Keep drafts concise enough for X and avoid unverifiable performance claims.
- Prefer practical, concrete, observable workflow posts.
"""


@dataclass(frozen=True)
class LLMGenerationOutcome:
    drafts: list[Draft]
    hypotheses_count: int = 0
    skipped: bool = False
    error: str | None = None
    llm_run_id: int | None = None


def create_llm_drafts_for_idea(
    db: Session,
    idea: Idea | None,
    openai_client: OpenAIClient,
    settings: Settings,
) -> LLMGenerationOutcome:
    input_payload = _input_payload(db, idea, settings)
    llm_run = LLMRun(
        kind="draft_generation",
        model=settings.openai_model,
        prompt_version=settings.llm_prompt_version,
        status="running",
        input_json=input_payload,
        output_json={},
        error_json={},
        usage_json={},
    )
    db.add(llm_run)
    db.commit()
    db.refresh(llm_run)

    if not openai_client.credentials_ready:
        _finish_llm_run(
            db,
            llm_run,
            status="skipped",
            error={"reason": "OPENAI_API_KEY is not configured."},
        )
        return LLMGenerationOutcome(drafts=[], skipped=True, llm_run_id=llm_run.id)

    if idea is None:
        _finish_llm_run(
            db,
            llm_run,
            status="skipped",
            error={"reason": "No idea was available for LLM generation."},
        )
        return LLMGenerationOutcome(drafts=[], skipped=True, llm_run_id=llm_run.id)

    try:
        result = openai_client.create_structured_response(
            system_prompt=SYSTEM_PROMPT,
            user_payload=input_payload,
            schema_name="growth_agent_llm_generation",
            schema=LLM_DRAFT_SCHEMA,
        )
    except ExternalClientError as exc:
        error = _safe_error(str(exc), settings)
        _finish_llm_run(db, llm_run, status="failed", error={"message": error})
        return LLMGenerationOutcome(drafts=[], skipped=True, error=error, llm_run_id=llm_run.id)

    output = _normalized_output(result.output, settings)
    _finish_llm_run(
        db,
        llm_run,
        status="completed",
        output=output,
        usage=result.usage,
        response_id=result.response_id,
    )
    drafts = _save_llm_drafts(db, idea, llm_run, output, settings)
    _attach_idea_llm_metadata(db, idea, llm_run, output)
    return LLMGenerationOutcome(
        drafts=drafts,
        hypotheses_count=len(output["hypotheses"]),
        llm_run_id=llm_run.id,
    )


def _input_payload(db: Session, idea: Idea | None, settings: Settings) -> dict[str, Any]:
    summary = metrics_summary(db)
    rules = active_playbook_rules(db)
    top_posts = summary.get("top_posts", [])
    top_posts = top_posts[: settings.llm_max_recent_posts] if isinstance(top_posts, list) else []
    return {
        "prompt_version": settings.llm_prompt_version,
        "draft_count": settings.llm_drafts_per_cycle,
        "hypothesis_analysis_enabled": settings.llm_analysis_enabled,
        "llm_full_auto_enabled": settings.llm_full_auto_enabled,
        "llm_min_confidence": settings.llm_min_confidence,
        "idea": (
            {
                "id": idea.id,
                "title": idea.title,
                "description": idea.description,
                "audience": idea.audience,
                "source": idea.source,
            }
            if idea is not None
            else None
        ),
        "metrics_summary": {
            "posts": summary.get("posts", 0),
            "impressions": summary.get("impressions", 0),
            "engagement_total": summary.get("engagement_total", 0),
            "engagement_rate": summary.get("engagement_rate", 0),
            "top_posts": top_posts,
        },
        "playbook_rules": [
            {"name": rule.name, "description": rule.description, "weight": rule.weight}
            for rule in rules
        ],
        "safety_policy": {
            "x_api": "read-only owned lookup and public metrics only",
            "posting_path": "Postiz only",
            "forbidden_automation": [
                "replies",
                "mentions",
                "likes",
                "follows",
                "reposts",
                "DMs",
                "keyword outreach",
            ],
            "human_review_required_for": [
                "external URLs",
                "shortened URLs",
                "pricing language",
                "legal language",
                "strong claims",
                "uncertainty",
            ],
        },
    }


def _normalized_output(output: dict[str, Any], settings: Settings) -> dict[str, Any]:
    drafts = output.get("drafts")
    hypotheses = output.get("hypotheses")
    raw_drafts = drafts if isinstance(drafts, list) else []
    raw_hypotheses = hypotheses if isinstance(hypotheses, list) else []
    normalized_drafts = [
        item
        for item in (_normalize_draft(item) for item in raw_drafts)
        if item is not None
    ][: settings.llm_drafts_per_cycle]
    normalized_hypotheses = (
        [
            item
            for item in (_normalize_hypothesis(item) for item in raw_hypotheses)
            if item is not None
        ]
        if settings.llm_analysis_enabled
        else []
    )
    return {"drafts": normalized_drafts, "hypotheses": normalized_hypotheses}


def _normalize_draft(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    content = str(item.get("content") or "").strip()
    if not content:
        return None
    confidence = _confidence(item.get("confidence"))
    risk_notes = item.get("risk_notes", [])
    if not isinstance(risk_notes, list):
        risk_notes = []
    return {
        "content": content,
        "hypothesis": str(item.get("hypothesis") or "").strip(),
        "target_audience": str(item.get("target_audience") or "").strip(),
        "expected_metric": _metric(item.get("expected_metric")),
        "confidence": confidence,
        "risk_notes": [
            str(note).strip()
            for note in risk_notes
            if str(note).strip()
        ][:5],
        "contains_url": bool(item.get("contains_url")),
        "contains_claim": bool(item.get("contains_claim")),
        "requires_human_review_by_model": bool(item.get("requires_human_review_by_model")),
    }


def _normalize_hypothesis(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    return {
        "title": title,
        "rationale": str(item.get("rationale") or "").strip(),
        "target_metric": _metric(item.get("target_metric")),
        "expected_effect": str(item.get("expected_effect") or "").strip(),
        "confidence": _confidence(item.get("confidence")),
    }


def _save_llm_drafts(
    db: Session,
    idea: Idea,
    llm_run: LLMRun,
    output: dict[str, Any],
    settings: Settings,
) -> list[Draft]:
    drafts: list[Draft] = []
    for item in output["drafts"]:
        confidence = float(item["confidence"])
        requires_review = (
            item["requires_human_review_by_model"]
            or confidence < settings.llm_min_confidence
            or not settings.llm_full_auto_enabled
        )
        notes = [
            f"Generated by LLM run {llm_run.id}.",
            f"LLM hypothesis: {truncate_sentence(item['hypothesis'], 180)}",
            f"LLM target metric: {item['expected_metric']}.",
            f"LLM confidence: {confidence:.2f}.",
        ]
        if item["requires_human_review_by_model"]:
            notes.append("LLM requires human review.")
        if confidence < settings.llm_min_confidence:
            notes.append("LLM confidence below threshold.")
        if not settings.llm_full_auto_enabled:
            notes.append("LLM_FULL_AUTO_ENABLED=false; human review required.")
        notes.extend(
            f"LLM risk note: {truncate_sentence(note, 160)}" for note in item["risk_notes"]
        )
        draft = Draft(
            idea=idea,
            content=item["content"],
            status="generated",
            has_url=bool(item["contains_url"]),
            requires_approval=requires_review,
            evaluation_notes=notes,
        )
        db.add(draft)
        drafts.append(draft)
    db.commit()
    for draft in drafts:
        db.refresh(draft)
    return drafts


def _attach_idea_llm_metadata(
    db: Session,
    idea: Idea,
    llm_run: LLMRun,
    output: dict[str, Any],
) -> None:
    metadata = dict(idea.metadata_json or {})
    metadata["last_llm_run_id"] = llm_run.id
    metadata["last_llm_hypotheses"] = output["hypotheses"]
    idea.metadata_json = metadata
    db.add(idea)
    db.commit()


def _finish_llm_run(
    db: Session,
    llm_run: LLMRun,
    *,
    status: str,
    output: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    response_id: str | None = None,
) -> None:
    llm_run.finished_at = datetime.now(UTC)
    llm_run.status = status
    if output is not None:
        llm_run.output_json = output
    if error is not None:
        llm_run.error_json = error
    if usage is not None:
        llm_run.usage_json = usage
    if response_id is not None:
        llm_run.response_id = response_id
    db.add(llm_run)
    db.commit()
    db.refresh(llm_run)


def _confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _metric(value: Any) -> str:
    metric = str(value or "").strip().lower()
    if metric in {"impressions", "likes", "replies", "reposts", "quotes", "bookmarks"}:
        return metric
    return "impressions"


def _safe_error(message: str, settings: Settings) -> str:
    if settings.openai_api_key:
        message = message.replace(settings.openai_api_key, "****")
    return message
