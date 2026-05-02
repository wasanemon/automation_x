from datetime import UTC, datetime, timedelta

from growth_agent.clients.postiz import ExternalClientError, PostizClient
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
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "false")
    get_settings.cache_clear()
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
    assert collect.json()["collected"] == 1
    assert collect.json()["skipped"] == 0
    assert collect.json()["errors"] == 0

    summary = client.get("/metrics/summary").json()
    assert summary["posts"] == 1
    assert summary["total_posts_with_metrics"] == 1
    assert summary["impressions"] == 200
    assert summary["total_impressions"] == 200
    assert summary["engagement_total"] == 32
    assert summary["average_engagement_rate"] == 0.16
    assert summary["top_posts"][0]["post_id"] == post["id"]

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
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "false")
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
            x_post_id="9876543210",
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
    assert posts[0]["x_post_id"] == "9876543210"
    assert posts[0]["x_post_created_at"] is not None
    assert posts[0]["x_reconciled_at"] is not None


def test_automatic_reconcile_skips_when_similarity_is_low(client, mock_x, monkeypatch):
    post = _create_live_post(
        client,
        monkeypatch,
        title="Similarity skip",
        description="Local content should not match a remote launch note.",
    )
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("X_USER_ID", "12345")
    get_settings.cache_clear()
    mock_x.owned_posts = [
        OwnedPost(
            x_post_id="1111111111",
            text="A totally unrelated update about a different topic.",
            created_at=datetime.now(UTC),
            metrics={},
        )
    ]

    response = client.post("/posts/reconcile-x-ids", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] == 0
    assert body["skipped"] == 1
    assert body["results"][0]["post_id"] == post["id"]
    assert client.get("/posts").json()[0]["x_post_id"] is None


def test_automatic_reconcile_handles_japanese_text_and_tco_url(client, mock_x, monkeypatch):
    monkeypatch.setenv("OWNED_DOMAINS", "example.com")
    post = _create_live_post(
        client,
        monkeypatch,
        title="日本語URL照合",
        description="日本語の本文と https://example.com/post を含む投稿です。",
    )
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("X_USER_ID", "12345")
    get_settings.cache_clear()
    mock_x.owned_posts = [
        OwnedPost(
            x_post_id="1919191919",
            text=post["content"].replace("https://example.com/post", "https://t.co/abc123"),
            created_at=datetime.now(UTC),
            metrics={},
        )
    ]

    response = client.post("/posts/reconcile-x-ids", json={})

    assert response.status_code == 200
    assert response.json()["matched"] == 1
    assert client.get("/posts").json()[0]["x_post_id"] == "1919191919"


def test_automatic_reconcile_selects_best_candidate(client, mock_x, monkeypatch):
    post = _create_live_post(
        client,
        monkeypatch,
        title="Candidate choice",
        description="The same text should prefer the closest X timestamp.",
    )
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("X_USER_ID", "12345")
    get_settings.cache_clear()
    mock_x.owned_posts = [
        OwnedPost(
            x_post_id="2222222222",
            text=post["content"],
            created_at=datetime.now(UTC) - timedelta(hours=24),
            metrics={},
        ),
        OwnedPost(
            x_post_id="3333333333",
            text=post["content"],
            created_at=datetime.now(UTC) + timedelta(minutes=30),
            metrics={},
        ),
    ]

    response = client.post("/posts/reconcile-x-ids", json={})

    assert response.status_code == 200
    assert response.json()["matched"] == 1
    assert client.get("/posts").json()[0]["x_post_id"] == "3333333333"


def test_automatic_reconcile_leaves_ambiguous_unmatched(client, mock_x, monkeypatch):
    post = _create_live_post(
        client,
        monkeypatch,
        title="Ambiguous candidate",
        description="Two identical remote candidates should be left for a human.",
    )
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("X_USER_ID", "12345")
    get_settings.cache_clear()
    created_at = datetime.now(UTC)
    mock_x.owned_posts = [
        OwnedPost(
            x_post_id="4444444444",
            text=post["content"],
            created_at=created_at,
            metrics={},
        ),
        OwnedPost(
            x_post_id="5555555555",
            text=post["content"],
            created_at=created_at,
            metrics={},
        ),
    ]

    response = client.post("/posts/reconcile-x-ids", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] == 0
    assert body["ambiguous"] == 1
    assert body["results"][0]["status"] == "ambiguous"
    assert client.get("/posts").json()[0]["x_post_id"] is None


def test_manual_mapping_validates_and_requires_force_for_overwrite(client):
    idea = client.post(
        "/ideas/ingest",
        json={"title": "Manual mapping", "description": "Manual X ID mapping path."},
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]
    post = client.post(f"/drafts/{draft['id']}/schedule", json={}).json()

    invalid = client.post(
        "/posts/reconcile-x-ids",
        json={"mappings": [{"post_id": post["id"], "x_post_id": "not-a-number"}]},
    )
    assert invalid.status_code == 422

    first = client.post(
        "/posts/reconcile-x-ids",
        json={"mappings": [{"post_id": post["id"], "x_post_id": "1212121212"}]},
    )
    assert first.status_code == 200
    assert first.json()["matched"] == 1
    assert first.json()["posts"][0]["x_reconciled_at"] is not None

    blocked = client.post(
        "/posts/reconcile-x-ids",
        json={"mappings": [{"post_id": post["id"], "x_post_id": "3434343434"}]},
    )
    assert blocked.status_code == 200
    assert blocked.json()["skipped"] == 1
    assert client.get("/posts").json()[0]["x_post_id"] == "1212121212"

    forced = client.post(
        "/posts/reconcile-x-ids",
        json={
            "force": True,
            "mappings": [{"post_id": post["id"], "x_post_id": "3434343434"}],
        },
    )
    assert forced.status_code == 200
    assert forced.json()["matched"] == 1
    assert client.get("/posts").json()[0]["x_post_id"] == "3434343434"


def test_manual_mapping_reports_missing_post(client):
    response = client.post(
        "/posts/reconcile-x-ids",
        json={"mappings": [{"post_id": 9999, "x_post_id": "9999999999"}]},
    )

    assert response.status_code == 200
    assert response.json()["errors"][0]["reason"] == "Post 9999 was not found."


def test_metrics_collect_skips_when_x_credentials_missing(client, monkeypatch):
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "false")
    monkeypatch.setenv("X_BEARER_TOKEN", "")
    get_settings.cache_clear()
    idea = client.post(
        "/ideas/ingest",
        json={"title": "Metrics skip", "description": "A post that can be reconciled later."},
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]
    post = client.post(f"/drafts/{draft['id']}/schedule", json={}).json()
    client.post(
        "/posts/reconcile-x-ids",
        json={"mappings": [{"post_id": post["id"], "x_post_id": "6666666666"}]},
    )

    collect = client.post("/metrics/collect", json={})
    assert collect.status_code == 200
    assert collect.json()["collected"] == 0
    assert collect.json()["skipped"] == 1


def test_metrics_collect_skips_post_without_x_post_id(client, monkeypatch):
    post = _create_live_post(
        client,
        monkeypatch,
        title="Missing X ID",
        description="Metrics should skip until the X post ID is known.",
    )
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    get_settings.cache_clear()

    collect = client.post("/metrics/collect", json={"post_id": post["id"]})

    assert collect.status_code == 200
    assert collect.json()["collected"] == 0
    assert collect.json()["skipped"] == 1
    assert collect.json()["results"][0]["reason"] == "Post has no x_post_id."


def test_metrics_collect_skips_dry_run_posts(client, mock_x, monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    get_settings.cache_clear()
    idea = client.post(
        "/ideas/ingest",
        json={"title": "Dry metrics", "description": "A dry-run post should not collect."},
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]
    post = client.post(f"/drafts/{draft['id']}/schedule", json={}).json()
    client.post(
        "/posts/reconcile-x-ids",
        json={"mappings": [{"post_id": post["id"], "x_post_id": "7777777777"}]},
    )

    collect = client.post("/metrics/collect", json={})
    assert collect.status_code == 200
    assert collect.json()["collected"] == 0
    assert collect.json()["skipped"] == 1
    assert mock_x.metrics_calls == []


def test_metrics_collect_handles_api_error_per_post(client, mock_x, monkeypatch):
    post = _create_live_post(
        client,
        monkeypatch,
        title="Metrics API error",
        description="One post error should not fail the whole collection request.",
    )
    client.post(
        "/posts/reconcile-x-ids",
        json={"mappings": [{"post_id": post["id"], "x_post_id": "8888888888"}]},
    )
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    get_settings.cache_clear()
    mock_x.metric_errors["8888888888"] = ExternalClientError("X API unavailable in test.")

    collect = client.post("/metrics/collect", json={})

    assert collect.status_code == 200
    assert collect.json()["collected"] == 0
    assert collect.json()["errors"] == 1
    assert collect.json()["results"][0]["status"] == "error"


def test_metrics_collect_saves_multiple_snapshots_and_summary_uses_latest(
    client, mock_x, monkeypatch
):
    post = _create_live_post(
        client,
        monkeypatch,
        title="Latest summary",
        description="Summary should use the latest snapshot for each post.",
    )
    client.post(
        "/posts/reconcile-x-ids",
        json={"mappings": [{"post_id": post["id"], "x_post_id": "9999999999"}]},
    )
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    get_settings.cache_clear()

    mock_x.metrics["9999999999"] = XMetrics(impressions=100, likes=5)
    first = client.post("/metrics/collect", json={"post_id": post["id"]})
    mock_x.metrics["9999999999"] = XMetrics(impressions=250, likes=10, bookmarks=3)
    second = client.post("/metrics/collect", json={"post_id": post["id"]})

    assert first.json()["collected"] == 1
    assert second.json()["collected"] == 1
    summary = client.get("/metrics/summary").json()
    assert summary["latest_snapshot_count"] == 1
    assert summary["total_impressions"] == 250
    assert summary["total_likes"] == 10
    assert summary["total_bookmarks"] == 3
    assert summary["top_posts"][0]["impressions"] == 250


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


def _create_live_post(client, monkeypatch, *, title: str, description: str):
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "false")
    get_settings.cache_clear()
    idea = client.post(
        "/ideas/ingest",
        json={"title": title, "description": description, "audience": "builders"},
    ).json()
    draft = client.post("/drafts/generate", json={"idea_id": idea["id"], "count": 1}).json()[0]
    scheduled_for = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    response = client.post(
        f"/drafts/{draft['id']}/schedule",
        json={"scheduled_for": scheduled_for},
    )
    assert response.status_code == 201
    return response.json()
