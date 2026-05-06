from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.models import DecisionLog, Draft, DraftImportRun, Hypothesis, Post


def test_draft_import_persists_hypotheses_context_and_links(client, db_session: Session):
    idea = client.post(
        "/ideas/ingest",
        json={
            "title": "Memory-backed generation",
            "description": "Store the hypothesis and context behind a generated draft.",
            "source": "codex_mcp",
        },
    ).json()

    response = client.post(
        "/drafts/import",
        json={
            "idea_id": idea["id"],
            "source": "codex_mcp",
            "prompt_version": "memory-v1",
            "context_snapshot": {
                "metrics_summary": {"posts": 0},
                "playbook": ["keep claims specific"],
            },
            "hypotheses": [
                {
                    "statement": "Process-oriented posts should be easier to trust.",
                    "target_metric": "likes",
                    "confidence": 0.81,
                    "evidence": ["No historical metrics yet."],
                }
            ],
            "drafts": [
                {
                    "content": (
                        "A useful growth loop keeps generation, review, and scheduling "
                        "separate."
                    ),
                    "hypothesis_index": 0,
                    "target_metric": "likes",
                    "confidence": 0.88,
                    "risk_notes": ["No URL.", "No strong claim."],
                }
            ],
            "metadata": {"operator": "codex"},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["imported_count"] == 1
    assert body["draft_import_run_id"] is not None
    assert len(body["hypothesis_ids"]) == 1

    draft = db_session.get(Draft, body["drafts"][0]["id"])
    assert draft is not None
    assert draft.hypothesis_id == body["hypothesis_ids"][0]
    assert draft.draft_import_run_id == body["draft_import_run_id"]
    assert draft.metadata_json["confidence"] == 0.88

    hypothesis = db_session.get(Hypothesis, body["hypothesis_ids"][0])
    assert hypothesis is not None
    assert hypothesis.statement == "Process-oriented posts should be easier to trust."
    assert hypothesis.target_metric == "likes"

    import_run = db_session.get(DraftImportRun, body["draft_import_run_id"])
    assert import_run is not None
    assert import_run.status == "completed"
    assert import_run.prompt_version == "memory-v1"
    assert import_run.input_context_json["metrics_summary"]["posts"] == 0
    assert import_run.imported_draft_ids_json == [draft.id]

    log = db_session.scalar(select(DecisionLog).where(DecisionLog.stage == "draft_import"))
    assert log is not None
    assert log.decision == "imported"
    assert log.draft_id == draft.id
    assert log.reason_json["draft_import_run_id"] == import_run.id


def test_draft_import_rejects_invalid_hypothesis_index(client, db_session: Session):
    idea = client.post(
        "/ideas/ingest",
        json={"title": "Invalid link", "description": "Reject a bad hypothesis reference."},
    ).json()

    response = client.post(
        "/drafts/import",
        json={
            "idea_id": idea["id"],
            "hypotheses": [],
            "drafts": [
                {
                    "content": "This draft points at a missing hypothesis.",
                    "hypothesis_index": 0,
                }
            ],
        },
    )

    assert response.status_code == 400
    assert "hypothesis_index" in response.json()["detail"]
    assert db_session.scalars(select(DraftImportRun)).all() == []


def test_run_cycle_writes_decision_logs_for_evaluate_and_schedule(
    client,
    db_session: Session,
):
    idea = client.post(
        "/ideas/ingest",
        json={
            "title": "Decision logging",
            "description": "A safe generated draft should leave a decision trail.",
            "source": "codex_mcp",
        },
    ).json()
    imported = client.post(
        "/drafts/import",
        json={
            "idea_id": idea["id"],
            "source": "codex_mcp",
            "drafts": [
                {
                    "content": "A reliable automation loop stores every decision it makes.",
                    "confidence": 0.91,
                    "requires_human_review_by_model": False,
                }
            ],
        },
    ).json()
    draft_id = imported["drafts"][0]["id"]

    cycle = client.post("/automation/run-cycle")

    assert cycle.status_code == 200
    assert cycle.json()["dry_run_scheduled_count"] == 1
    post = db_session.scalar(select(Post).where(Post.draft_id == draft_id))
    assert post is not None

    logs = db_session.scalars(
        select(DecisionLog).where(DecisionLog.draft_id == draft_id).order_by(DecisionLog.id)
    ).all()
    decisions = {(log.stage, log.decision) for log in logs}
    assert ("draft_import", "imported") in decisions
    assert ("evaluate", "auto_schedule_candidate") in decisions
    assert ("schedule", "dry_run_scheduled") in decisions
    cycle_id = cycle.json()["cycle_id"]
    assert all(
        log.automation_run_id == cycle_id or log.stage == "draft_import" for log in logs
    )


def test_memory_context_and_history_endpoints_return_operational_memory(client):
    idea = client.post(
        "/ideas/ingest",
        json={"title": "Memory context", "description": "Expose compact memory context."},
    ).json()
    client.post(
        "/drafts/import",
        json={
            "idea_id": idea["id"],
            "hypotheses": [
                {
                    "statement": "Stored context improves the next generation cycle.",
                    "target_metric": "likes",
                    "confidence": 0.8,
                }
            ],
            "drafts": [
                {
                    "content": "Store what happened, then let the next draft learn from it.",
                    "hypothesis_index": 0,
                    "confidence": 0.9,
                }
            ],
        },
    )
    client.post("/automation/run-cycle")

    context = client.get("/memory/context?limit=5")
    hypotheses = client.get("/hypotheses?limit=5")
    import_runs = client.get("/draft-import-runs?limit=5")
    decision_logs = client.get("/decision-logs?limit=5")

    assert context.status_code == 200
    context_body = context.json()
    assert context_body["metrics_summary"]["posts"] == 0
    assert context_body["recent_hypotheses"]
    assert context_body["recent_draft_import_runs"]
    assert context_body["recent_decision_logs"]
    assert context_body["recent_automation_runs"]
    assert context_body["last_automation_run"] is not None

    assert hypotheses.status_code == 200
    assert hypotheses.json()[0]["statement"] == "Stored context improves the next generation cycle."
    assert import_runs.status_code == 200
    assert import_runs.json()[0]["imported_draft_ids_json"]
    assert decision_logs.status_code == 200
    assert any(log["stage"] == "schedule" for log in decision_logs.json())
