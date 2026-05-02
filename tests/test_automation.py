from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.clients.x_api import OwnedPost, XMetrics
from growth_agent.config import get_settings
from growth_agent.models import AutomationRun, Draft, Idea, Post
from growth_agent.scripts import run_cycle as run_cycle_script


def test_run_cycle_auto_posting_disabled_never_calls_live_postiz(
    client,
    db_session: Session,
    mock_postiz,
    monkeypatch,
):
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "false")
    monkeypatch.setenv("AUTO_POSTING_ENABLED", "false")
    get_settings.cache_clear()
    _seed_safe_draft(db_session, content="Safe automation candidate one.")

    response = client.post("/automation/run-cycle")

    assert response.status_code == 200
    body = response.json()
    assert body["auto_scheduled_count"] == 1
    assert body["auto_schedule_candidates_count"] == 1
    assert body["dry_run_scheduled_count"] == 1
    assert body["live_scheduled_count"] == 0
    assert body["dry_run"] is True
    assert mock_postiz.calls == []
    post = db_session.scalar(select(Post))
    assert post is not None
    assert post.dry_run is True
    assert post.postiz_post_id is None
    run = db_session.get(AutomationRun, body["cycle_id"])
    assert run is not None
    assert run.auto_scheduled_count == 1
    assert run.dry_run_scheduled_count == 1
    assert run.live_scheduled_count == 0
    assert run.auto_posting_enabled is False


def test_run_cycle_scheduling_dry_run_prevents_postiz_when_auto_enabled(
    client,
    db_session: Session,
    mock_postiz,
    monkeypatch,
):
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "true")
    monkeypatch.setenv("AUTO_POSTING_ENABLED", "true")
    get_settings.cache_clear()
    _seed_safe_draft(db_session, content="Safe automation candidate two.")

    response = client.post("/automation/run-cycle")

    assert response.status_code == 200
    assert response.json()["auto_scheduled_count"] == 1
    assert response.json()["dry_run_scheduled_count"] == 1
    assert response.json()["live_scheduled_count"] == 0
    assert mock_postiz.calls == []
    assert db_session.scalar(select(Post)).dry_run is True


def test_run_cycle_live_conditions_call_postiz_once(
    client,
    db_session: Session,
    mock_postiz,
    monkeypatch,
):
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "false")
    monkeypatch.setenv("AUTO_POSTING_ENABLED", "true")
    get_settings.cache_clear()
    _seed_safe_draft(db_session, content="Safe live automation candidate.")

    response = client.post("/automation/run-cycle")

    assert response.status_code == 200
    body = response.json()
    assert body["auto_scheduled_count"] == 1
    assert body["dry_run_scheduled_count"] == 0
    assert body["live_scheduled_count"] == 1
    assert body["dry_run"] is False
    assert len(mock_postiz.calls) == 1
    post = db_session.scalar(select(Post))
    assert post.postiz_post_id == "postiz-1"
    run = db_session.get(AutomationRun, body["cycle_id"])
    assert run.live_scheduled_count == 1
    assert run.auto_posting_enabled is True


def test_run_cycle_kill_switch_blocks_schedule(
    client,
    db_session: Session,
    mock_postiz,
    monkeypatch,
):
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "false")
    monkeypatch.setenv("AUTO_POSTING_ENABLED", "true")
    monkeypatch.setenv("AUTOMATION_KILL_SWITCH", "true")
    get_settings.cache_clear()
    _seed_safe_draft(db_session, content="Safe automation candidate three.")

    response = client.post("/automation/run-cycle")

    assert response.status_code == 200
    body = response.json()
    assert body["kill_switch_active"] is True
    assert body["auto_scheduled_count"] == 0
    assert body["dry_run_scheduled_count"] == 0
    assert body["live_scheduled_count"] == 0
    assert body["skipped_count"] >= 1
    assert mock_postiz.calls == []
    assert db_session.scalars(select(Post)).all() == []


def test_run_cycle_routes_approval_required_draft_without_scheduling(
    client,
    db_session: Session,
    mock_postiz,
    monkeypatch,
):
    monkeypatch.setenv("OWNED_DOMAINS", "example.com")
    get_settings.cache_clear()
    client.post(
        "/ideas/ingest",
        json={
            "title": "External URL review",
            "description": "Read this setup note at https://external.example/post.",
            "audience": "builders",
        },
    )

    response = client.post("/automation/run-cycle")

    assert response.status_code == 200
    body = response.json()
    assert body["created_drafts_count"] == 1
    assert body["evaluated_drafts_count"] == 1
    assert body["approval_required_count"] == 1
    assert body["auto_scheduled_count"] == 0
    assert body["dry_run_scheduled_count"] == 0
    assert body["live_scheduled_count"] == 0
    assert mock_postiz.calls == []
    assert db_session.scalars(select(Post)).all() == []
    draft = db_session.scalar(select(Draft))
    assert draft is not None
    assert draft.status == "approval_required"


def test_run_cycle_schedules_only_up_to_cycle_limit(
    client,
    db_session: Session,
    mock_postiz,
    monkeypatch,
):
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "false")
    monkeypatch.setenv("AUTO_POSTING_ENABLED", "true")
    monkeypatch.setenv("MAX_AUTO_SCHEDULE_PER_CYCLE", "1")
    get_settings.cache_clear()
    for index in range(3):
        _seed_safe_draft(db_session, content=f"Safe cycle limit candidate {index}.")

    response = client.post("/automation/run-cycle")

    assert response.status_code == 200
    body = response.json()
    assert body["auto_scheduled_count"] == 1
    assert body["auto_schedule_candidates_count"] == 3
    assert body["live_scheduled_count"] == 1
    assert body["frequency_limited_count"] == 2
    assert body["skipped_count"] >= 2
    assert len(mock_postiz.calls) == 1
    assert len(db_session.scalars(select(Post)).all()) == 1


def test_run_cycle_respects_daily_limit(
    client,
    db_session: Session,
    mock_postiz,
    monkeypatch,
):
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "false")
    monkeypatch.setenv("AUTO_POSTING_ENABLED", "true")
    monkeypatch.setenv("MAX_AUTO_SCHEDULE_PER_DAY", "1")
    get_settings.cache_clear()
    _seed_automation_run(db_session, auto_scheduled_count=1)
    _seed_safe_draft(db_session, content="Safe daily limit candidate.")

    response = client.post("/automation/run-cycle")

    assert response.status_code == 200
    assert response.json()["auto_scheduled_count"] == 0
    assert response.json()["frequency_limited_count"] == 1
    assert response.json()["skipped_count"] >= 1
    assert mock_postiz.calls == []
    assert db_session.scalars(select(Post)).all() == []


def test_run_cycle_respects_minimum_hours_between_auto_posts(
    client,
    db_session: Session,
    mock_postiz,
    monkeypatch,
):
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "false")
    monkeypatch.setenv("AUTO_POSTING_ENABLED", "true")
    monkeypatch.setenv("MIN_HOURS_BETWEEN_AUTO_POSTS", "4")
    monkeypatch.setenv("DEFAULT_SCHEDULE_DELAY_MINUTES", "30")
    get_settings.cache_clear()
    previous_scheduled_for = datetime.now(UTC) + timedelta(hours=2)
    _seed_automation_run(
        db_session,
        auto_scheduled_count=1,
        scheduled_for=previous_scheduled_for,
    )
    _seed_safe_draft(db_session, content="Safe interval candidate.")

    response = client.post("/automation/run-cycle")

    assert response.status_code == 200
    assert response.json()["auto_scheduled_count"] == 1
    assert response.json()["live_scheduled_count"] == 1
    assert len(mock_postiz.calls) == 1
    scheduled_for = mock_postiz.calls[0]["scheduled_for"]
    assert scheduled_for >= previous_scheduled_for + timedelta(hours=4, seconds=-1)


def test_automation_status_reports_counts_and_warnings(
    client,
    db_session: Session,
    monkeypatch,
):
    monkeypatch.setenv("AUTO_POSTING_ENABLED", "false")
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "true")
    get_settings.cache_clear()
    _seed_automation_run(db_session, auto_scheduled_count=2)
    _seed_safe_draft(
        db_session,
        content="Needs approval status candidate.",
        status="approval_required",
        requires_approval=True,
        risk_level="medium",
        score=70,
    )
    _seed_post(db_session, content="Needs reconcile.", x_post_id=None)
    _seed_post(db_session, content="Needs metrics.", x_post_id="1234567890")

    response = client.get("/automation/status")

    assert response.status_code == 200
    body = response.json()
    assert body["auto_posting_enabled"] is False
    assert body["scheduling_dry_run"] is True
    assert body["kill_switch_active"] is False
    assert body["max_auto_schedule_per_day"] == 3
    assert body["max_auto_schedule_per_cycle"] == 1
    assert body["min_hours_between_auto_posts"] == 4
    assert body["today_auto_scheduled_count"] == 2
    assert body["approval_waiting_draft_count"] == 1
    assert body["unreconciled_post_count"] == 1
    assert body["metrics_missing_post_count"] == 1
    assert body["last_automation_run"]["auto_scheduled_count"] == 2
    assert body["warnings"] == body["system_warnings"]
    assert any("AUTO_POSTING_ENABLED=false" in warning for warning in body["system_warnings"])


def test_automation_status_never_returns_secret_values(client, monkeypatch):
    monkeypatch.setenv("GROWTH_AGENT_API_KEY", "ga_redaction_sentinel_status")
    monkeypatch.setenv("POSTIZ_API_KEY", "postiz_redaction_sentinel_status")
    monkeypatch.setenv("X_BEARER_TOKEN", "x_redaction_sentinel_status")
    monkeypatch.setenv("X_USER_ID", "12345")
    get_settings.cache_clear()

    response = client.get("/automation/status")

    assert response.status_code == 200
    body_text = response.text
    assert "ga_redaction_sentinel_status" not in body_text
    assert "postiz_redaction_sentinel_status" not in body_text
    assert "x_redaction_sentinel_status" not in body_text


def test_duplicate_draft_is_counted_and_not_scheduled(
    client,
    db_session: Session,
    mock_postiz,
):
    original = _seed_safe_draft(db_session, content="Original duplicate baseline.")
    _seed_safe_draft(
        db_session,
        content="Duplicate schedule blocker.",
        duplicate_of_draft_id=original.id,
        duplicate_reason="Near-duplicate of draft 1.",
    )

    response = client.post("/automation/run-cycle")

    assert response.status_code == 200
    body = response.json()
    assert body["auto_schedule_candidates_count"] == 1
    assert body["duplicate_skipped_count"] == 1
    assert body["auto_scheduled_count"] == 1
    assert mock_postiz.calls == []


def test_run_cycle_calls_reconcile_and_metrics_collect(
    client,
    db_session: Session,
    mock_x,
    monkeypatch,
):
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("X_USER_ID", "12345")
    get_settings.cache_clear()
    post = _seed_post(db_session, content="A published post ready for matching.", x_post_id=None)
    mock_x.owned_posts = [
        OwnedPost(
            x_post_id="9876543210",
            text=post.content,
            created_at=datetime.now(UTC),
            metrics={},
        )
    ]
    mock_x.metrics["9876543210"] = XMetrics(impressions=500, likes=30, replies=2)

    response = client.post("/automation/run-cycle")

    assert response.status_code == 200
    body = response.json()
    assert body["reconciled_count"] == 1
    assert body["metrics_collected_count"] == 1
    assert body["metrics_skipped_count"] == 0
    assert mock_x.list_calls == 1
    assert mock_x.metrics_calls == ["9876543210"]


def test_run_cycle_skips_reconcile_and_metrics_safely_without_x_credentials(
    client,
    db_session: Session,
    mock_x,
    monkeypatch,
):
    monkeypatch.setenv("X_BEARER_TOKEN", "")
    monkeypatch.setenv("X_USER_ID", "")
    get_settings.cache_clear()
    _seed_post(db_session, content="Needs X reconcile credentials.", x_post_id=None)
    _seed_post(db_session, content="Needs X metrics credentials.", x_post_id="1234567890")

    response = client.post("/automation/run-cycle")

    assert response.status_code == 200
    body = response.json()
    assert body["reconciled_count"] == 0
    assert body["metrics_collected_count"] == 0
    assert body["metrics_skipped_count"] == 1
    assert mock_x.list_calls == 0
    assert mock_x.metrics_calls == []


def test_run_cycle_script_does_not_print_api_key(monkeypatch, capsys):
    api_key = "ga_redaction_sentinel_for_test"
    monkeypatch.setenv("GROWTH_AGENT_API_KEY", api_key)
    monkeypatch.setenv("GROWTH_AGENT_BASE_URL", "http://growth-agent.test")

    def fake_post(url, headers, timeout):
        assert url == "http://growth-agent.test/automation/run-cycle"
        assert headers["X-API-Key"] == api_key
        assert timeout == 10
        return httpx.Response(200, json={"cycle_id": 1, "errors": []})

    monkeypatch.setattr(run_cycle_script.httpx, "post", fake_post)

    assert run_cycle_script.main() == 0
    captured = capsys.readouterr()
    assert api_key not in captured.out
    assert api_key not in captured.err
    assert '"cycle_id": 1' in captured.out


def _seed_safe_draft(
    db: Session,
    *,
    content: str,
    status: str = "evaluated",
    requires_approval: bool = False,
    risk_level: str = "low",
    score: int = 95,
    duplicate_of_draft_id: int | None = None,
    duplicate_reason: str | None = None,
) -> Draft:
    idea = Idea(
        source="test",
        title=content[:80],
        description=content,
        status="processed",
        metadata_json={},
    )
    draft = Draft(
        idea=idea,
        content=content,
        status=status,
        risk_level=risk_level,
        score=score,
        has_url=False,
        requires_approval=requires_approval,
        duplicate_of_draft_id=duplicate_of_draft_id,
        duplicate_reason=duplicate_reason,
        evaluation_notes=["Seeded by test."],
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def _seed_post(db: Session, *, content: str, x_post_id: str | None) -> Post:
    draft = _seed_safe_draft(db, content=f"Draft for {content}")
    post = Post(
        draft_id=draft.id,
        content=content,
        status="scheduled" if x_post_id is None else "published",
        postiz_post_id="postiz-seeded",
        postiz_integration_id="integration-x",
        x_post_id=x_post_id,
        scheduled_for=datetime.now(UTC) - timedelta(minutes=5),
        has_url=False,
        dry_run=False,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def _seed_automation_run(
    db: Session,
    *,
    auto_scheduled_count: int,
    scheduled_for: datetime | None = None,
) -> AutomationRun:
    started_at = datetime.now(UTC) - timedelta(minutes=10)
    scheduled_posts = []
    if scheduled_for is not None:
        scheduled_posts.append(
            {
                "post_id": 100,
                "draft_id": 100,
                "scheduled_for": scheduled_for.isoformat().replace("+00:00", "Z"),
                "dry_run": False,
                "live": True,
            }
        )
    run = AutomationRun(
        started_at=started_at,
        finished_at=started_at + timedelta(minutes=1),
        status="completed",
        dry_run=False,
        auto_posting_enabled=True,
        kill_switch_active=False,
        auto_scheduled_count=auto_scheduled_count,
        dry_run_scheduled_count=0,
        live_scheduled_count=auto_scheduled_count,
        error_json=[],
        errors_json=[],
        summary_json={"scheduled_posts": scheduled_posts},
        metadata_json={},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
