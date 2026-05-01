from datetime import UTC, datetime, timedelta

from growth_agent.clients.x_api import OwnedPost, XMetrics


def test_health_and_idea_draft_generation(client):
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}

    idea_response = client.post(
        "/ideas/ingest",
        json={
            "title": "Launch a customer proof thread",
            "description": "Turn onboarding lessons into a concise launch observation.",
            "source": "n8n",
            "audience": "founders",
        },
    )
    assert idea_response.status_code == 201
    idea = idea_response.json()
    assert idea["source"] == "n8n"

    drafts_response = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 2})
    assert drafts_response.status_code == 201
    drafts = drafts_response.json()
    assert len(drafts) == 2
    assert drafts[0]["status"] == "generated"

    ideas_response = client.get("/ideas")
    assert ideas_response.status_code == 200
    assert ideas_response.json()[0]["id"] == idea["id"]


def test_url_and_high_risk_drafts_require_approval(client):
    idea = client.post(
        "/ideas/ingest",
        json={
            "title": "Guaranteed growth",
            "description": "Guaranteed 100% growth at https://example.com for every team.",
            "audience": "operators",
        },
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]

    evaluation = client.post(f"/drafts/{draft['id']}/evaluate").json()
    evaluated = evaluation["draft"]
    assert evaluated["has_url"] is True
    assert evaluated["requires_approval"] is True
    assert evaluated["risk_level"] == "high"
    assert evaluation["can_auto_schedule"] is False

    schedule_response = client.post(f"/drafts/{draft['id']}/schedule", json={})
    assert schedule_response.status_code == 409
    assert "Human approval" in schedule_response.json()["detail"]


def test_approved_draft_schedules_through_postiz(client, mock_postiz):
    idea = client.post(
        "/ideas/ingest",
        json={
            "title": "Workflow lesson",
            "description": "A simple repeatable process helped the team learn faster.",
            "audience": "builders",
        },
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]
    client.post(f"/drafts/{draft['id']}/evaluate")

    scheduled_for = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    schedule_response = client.post(
        f"/drafts/{draft['id']}/schedule",
        json={"scheduled_for": scheduled_for},
    )

    assert schedule_response.status_code == 201
    post = schedule_response.json()
    assert post["postiz_post_id"] == "postiz-1"
    assert post["has_url"] is False
    assert len(mock_postiz.calls) == 1
    assert mock_postiz.calls[0]["has_url"] is False


def test_duplicate_prevention_blocks_scheduling(client):
    idea = client.post(
        "/ideas/ingest",
        json={
            "title": "Repeatable launch note",
            "description": "A precise launch workflow is easier to measure.",
        },
    ).json()
    drafts = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()
    draft = drafts[0]
    assert client.post(f"/drafts/{draft['id']}/schedule", json={}).status_code == 201

    duplicate_idea = client.post(
        "/ideas/ingest",
        json={
            "title": "Repeatable launch note",
            "description": "A precise launch workflow is easier to measure.",
        },
    ).json()
    duplicate_draft = client.post(
        "/drafts/generate", json={"idea_id": duplicate_idea["id"], "count": 1}
    ).json()[0]

    evaluation = client.post(f"/drafts/{duplicate_draft['id']}/evaluate").json()
    assert evaluation["draft"]["duplicate_of_draft_id"] is not None
    assert evaluation["draft"]["risk_level"] == "high"

    schedule_response = client.post(f"/drafts/{duplicate_draft['id']}/schedule", json={})
    assert schedule_response.status_code == 409
    assert "duplicate" in schedule_response.json()["detail"].lower()


def test_reject_endpoint_blocks_scheduling(client):
    idea = client.post(
        "/ideas/ingest",
        json={"title": "Reject me", "description": "A small note for later."},
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]
    reject = client.post(
        f"/drafts/{draft['id']}/reject",
        json={"reviewer": "ops", "reason": "Not relevant this week."},
    )
    assert reject.status_code == 200
    assert reject.json()["status"] == "rejected"

    schedule_response = client.post(f"/drafts/{draft['id']}/schedule", json={})
    assert schedule_response.status_code == 409


def test_reconcile_metrics_feedback_and_weekly_report(client, mock_x):
    idea = client.post(
        "/ideas/ingest",
        json={
            "title": "Metrics loop",
            "description": "Learn from owned post performance and update the playbook.",
        },
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]
    post = client.post(f"/drafts/{draft['id']}/schedule", json={}).json()

    reconcile = client.post(
        "/posts/reconcile-x-ids",
        json={"mappings": [{"post_id": post["id"], "x_post_id": "12345"}]},
    )
    assert reconcile.status_code == 200
    assert reconcile.json()["reconciled"] == 1

    mock_x.metrics["12345"] = XMetrics(
        impressions=200, likes=20, replies=4, reposts=5, quotes=1, bookmarks=2
    )
    collect = client.post("/metrics/collect", json={})
    assert collect.status_code == 200
    assert collect.json() == {"collected": 1, "skipped": 0}

    summary = client.get("/metrics/summary").json()
    assert summary["posts"] == 1
    assert summary["impressions"] == 200
    assert summary["engagement_total"] == 32

    feedback = client.post("/feedback/run")
    assert feedback.status_code == 200
    assert feedback.json()["recommendations"]

    playbook = client.get("/feedback/playbook")
    assert playbook.status_code == 200
    assert len(playbook.json()) >= 4

    report = client.get("/reports/weekly")
    assert report.status_code == 200
    assert "# Weekly Growth Agent Report" in report.json()["report"]


def test_automatic_reconcile_uses_owned_x_lookup(client, mock_x):
    idea = client.post(
        "/ideas/ingest",
        json={"title": "Owned lookup", "description": "Match local scheduled content to X."},
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]
    post = client.post(f"/drafts/{draft['id']}/schedule", json={}).json()
    mock_x.owned_posts = [
        OwnedPost(
            x_post_id="owned-1",
            text=post["content"],
            created_at=datetime.now(UTC),
            metrics={},
        )
    ]

    response = client.post("/posts/reconcile-x-ids", json={"lookback_days": 3})
    assert response.status_code == 200
    assert response.json()["reconciled"] == 1
    assert mock_x.list_calls == 1

    posts = client.get("/posts").json()
    assert posts[0]["x_post_id"] == "owned-1"
