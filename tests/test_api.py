from datetime import UTC, datetime, timedelta

from growth_agent.clients.postiz import PostizClient
from growth_agent.clients.x_api import OwnedPost, XMetrics
from growth_agent.config import Settings, get_settings
from growth_agent.deps import get_postiz_client
from growth_agent.main import app


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


def test_api_key_auth_required_when_not_testing(client, monkeypatch):
    monkeypatch.setenv("TESTING", "false")
    monkeypatch.setenv("GROWTH_AGENT_API_KEY", "ga_test_key")
    get_settings.cache_clear()

    assert client.get("/health").status_code == 200

    payload = {
        "title": "Auth check",
        "description": "Only authenticated callers can create ideas.",
    }
    assert client.post("/ideas/ingest", json=payload).status_code == 401
    assert (
        client.post("/ideas/ingest", json=payload, headers={"X-API-Key": "wrong"}).status_code
        == 401
    )

    ok_response = client.post(
        "/ideas/ingest",
        json=payload,
        headers={"X-API-Key": "ga_test_key"},
    )
    assert ok_response.status_code == 201


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


def test_owned_domain_url_can_auto_schedule_when_low_risk(client, monkeypatch):
    monkeypatch.setenv("OWNED_DOMAINS", "example.com,docs.example.com")
    get_settings.cache_clear()
    idea = client.post(
        "/ideas/ingest",
        json={
            "title": "Setup notes",
            "description": "Read the setup notes at https://docs.example.com/start.",
            "audience": "builders",
        },
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]

    evaluation = client.post(f"/drafts/{draft['id']}/evaluate").json()
    evaluated = evaluation["draft"]
    assert evaluated["has_url"] is True
    assert evaluated["requires_approval"] is False
    assert evaluated["risk_level"] == "low"
    assert evaluation["can_auto_schedule"] is True

    schedule_response = client.post(f"/drafts/{draft['id']}/schedule", json={})
    assert schedule_response.status_code == 201
    assert schedule_response.json()["dry_run"] is True


def test_external_url_requires_human_approval_even_with_owned_domains(client, monkeypatch):
    monkeypatch.setenv("OWNED_DOMAINS", "example.com")
    get_settings.cache_clear()
    idea = client.post(
        "/ideas/ingest",
        json={
            "title": "External link",
            "description": "Read the notes at https://external.example/start.",
            "audience": "builders",
        },
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]

    evaluation = client.post(f"/drafts/{draft['id']}/evaluate").json()
    assert evaluation["draft"]["has_url"] is True
    assert evaluation["draft"]["requires_approval"] is True
    assert evaluation["can_auto_schedule"] is False


def test_approved_draft_schedules_dry_run_without_postiz(client, mock_postiz):
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
    assert post["dry_run"] is True
    assert post["postiz_post_id"] is None
    assert post["has_url"] is False
    assert len(mock_postiz.calls) == 0


def test_live_schedule_calls_postiz_only_when_dry_run_disabled(client, mock_postiz, monkeypatch):
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "false")
    get_settings.cache_clear()
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
    assert post["dry_run"] is False
    assert post["postiz_post_id"] == "postiz-1"
    assert len(mock_postiz.calls) == 1
    assert mock_postiz.calls[0]["has_url"] is False


def test_live_schedule_can_follow_dry_run_for_same_content(
    client, mock_postiz, monkeypatch
):
    payload = {
        "title": "Workflow lesson",
        "description": "A simple repeatable process helped the team learn faster.",
        "audience": "builders",
    }
    dry_run_idea = client.post("/ideas/ingest", json=payload).json()
    dry_run_draft = client.post(
        "/drafts/generate", json={"idea_id": dry_run_idea["id"], "count": 1}
    ).json()[0]
    dry_run = client.post(f"/drafts/{dry_run_draft['id']}/schedule", json={})
    assert dry_run.status_code == 201
    assert dry_run.json()["dry_run"] is True

    monkeypatch.setenv("SCHEDULING_DRY_RUN", "false")
    get_settings.cache_clear()
    live_idea = client.post("/ideas/ingest", json=payload).json()
    live_draft = client.post(
        "/drafts/generate", json={"idea_id": live_idea["id"], "count": 1}
    ).json()[0]

    evaluation = client.post(f"/drafts/{live_draft['id']}/evaluate").json()
    assert evaluation["draft"]["duplicate_of_draft_id"] is None

    scheduled_for = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    live = client.post(
        f"/drafts/{live_draft['id']}/schedule",
        json={"scheduled_for": scheduled_for},
    )

    assert live.status_code == 201
    assert live.json()["dry_run"] is False
    assert live.json()["postiz_post_id"] == "postiz-1"
    assert len(mock_postiz.calls) == 1


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


def test_same_draft_cannot_be_scheduled_twice(client):
    idea = client.post(
        "/ideas/ingest",
        json={"title": "One schedule", "description": "A precise launch workflow."},
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]

    first = client.post(f"/drafts/{draft['id']}/schedule", json={})
    second = client.post(f"/drafts/{draft['id']}/schedule", json={})

    assert first.status_code == 201
    assert second.status_code == 409
    assert "schedule record" in second.json()["detail"]


def test_postiz_failure_records_local_schedule_attempt(
    client, failing_postiz, monkeypatch
):
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "false")
    get_settings.cache_clear()
    app.dependency_overrides[get_postiz_client] = lambda: failing_postiz
    idea = client.post(
        "/ideas/ingest",
        json={"title": "Remote failure", "description": "A safe note for later scheduling."},
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]

    first = client.post(f"/drafts/{draft['id']}/schedule", json={})
    retry = client.post(f"/drafts/{draft['id']}/schedule", json={})
    posts = client.get("/posts").json()

    assert first.status_code == 502
    assert retry.status_code == 409
    assert failing_postiz.calls == 1
    assert posts[0]["status"] == "schedule_failed"
    assert posts[0]["dry_run"] is False


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


def test_reconcile_metrics_feedback_and_weekly_report(client, mock_x, monkeypatch):
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
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    get_settings.cache_clear()
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


def test_automatic_reconcile_uses_owned_x_lookup(client, mock_x, monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("X_USER_ID", "12345")
    get_settings.cache_clear()
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


def test_metrics_collect_skips_when_x_credentials_missing(client):
    idea = client.post(
        "/ideas/ingest",
        json={"title": "Metrics skip", "description": "A post that can be reconciled later."},
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]
    post = client.post(f"/drafts/{draft['id']}/schedule", json={}).json()
    client.post(
        "/posts/reconcile-x-ids",
        json={"mappings": [{"post_id": post["id"], "x_post_id": "missing-token"}]},
    )

    collect = client.post("/metrics/collect", json={})
    assert collect.status_code == 200
    assert collect.json() == {"collected": 0, "skipped": 1}


def test_metrics_collect_skips_posts_older_than_private_metrics_window(
    client, mock_x, monkeypatch
):
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    get_settings.cache_clear()
    idea = client.post(
        "/ideas/ingest",
        json={"title": "Old metrics", "description": "A post outside the private window."},
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]
    scheduled_for = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    post = client.post(
        f"/drafts/{draft['id']}/schedule",
        json={"scheduled_for": scheduled_for},
    ).json()
    client.post(
        "/posts/reconcile-x-ids",
        json={"mappings": [{"post_id": post["id"], "x_post_id": "old-post"}]},
    )

    collect = client.post("/metrics/collect", json={})
    assert collect.status_code == 200
    assert collect.json() == {"collected": 0, "skipped": 1}
    assert mock_x.metrics_calls == []


def test_postiz_client_uses_public_base_url_without_adding_prefix(monkeypatch):
    requested: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "postiz-id"}

    class FakeClient:
        def __init__(self, timeout: float) -> None:
            requested["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def request(self, method: str, url: str, headers: dict[str, str], **kwargs):
            requested["method"] = method
            requested["url"] = url
            requested["headers"] = headers
            requested["kwargs"] = kwargs
            return FakeResponse()

    monkeypatch.setattr("growth_agent.clients.postiz.httpx.Client", FakeClient)
    client = PostizClient(
        Settings(
            postiz_base_url="https://api.postiz.com/public/v1",
            postiz_api_key="test-postiz-key",
            postiz_x_integration_id="integration-x",
            request_timeout_seconds=7,
            max_external_retries=0,
        )
    )

    result = client.schedule_x_post("hello", datetime.now(UTC), has_url=False)

    assert result.postiz_post_id == "postiz-id"
    assert requested["url"] == "https://api.postiz.com/public/v1/posts"
    assert requested["timeout"] == 7
