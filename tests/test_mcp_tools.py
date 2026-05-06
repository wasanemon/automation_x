import json

import httpx
import pytest

from growth_agent.mcp_tools import (
    GrowthAgentMCPClient,
    GrowthAgentMCPConfig,
    GrowthAgentMCPError,
)


def test_mcp_run_dry_cycle_blocks_when_kill_switch_active():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"kill_switch_active": True, "scheduling_dry_run": True},
        )

    client = _client(handler)

    result = client.run_dry_cycle()

    assert result["blocked"] is True
    assert "AUTOMATION_KILL_SWITCH=true" in result["reason"]
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/automation/status")
    ]


def test_mcp_run_dry_cycle_blocks_when_growth_agent_is_live_mode():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"kill_switch_active": False, "scheduling_dry_run": False},
        )

    client = _client(handler)

    result = client.run_dry_cycle()

    assert result["blocked"] is True
    assert "SCHEDULING_DRY_RUN=false" in result["reason"]
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/automation/status")
    ]


def test_mcp_run_dry_cycle_calls_cycle_only_in_dry_run_mode():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/automation/status":
            return httpx.Response(
                200,
                json={"kill_switch_active": False, "scheduling_dry_run": True},
            )
        return httpx.Response(200, json={"cycle_id": 42, "dry_run": True})

    client = _client(handler)

    result = client.run_dry_cycle()

    assert result == {"ok": True, "cycle": {"cycle_id": 42, "dry_run": True}}
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/automation/status"),
        ("POST", "/automation/run-cycle"),
    ]
    assert all(request.headers["x-api-key"] == "ga_test_secret" for request in requests)


def test_mcp_memory_context_and_history_helpers_call_expected_paths():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/memory/context":
            return httpx.Response(200, json={"recent_hypotheses": []})
        if request.url.path == "/hypotheses":
            return httpx.Response(200, json=[{"id": 1}])
        if request.url.path == "/draft-import-runs":
            return httpx.Response(200, json=[{"id": 2}])
        if request.url.path == "/decision-logs":
            return httpx.Response(200, json=[{"id": 3}])
        return httpx.Response(404, json={"detail": "not found"})

    client = _client(handler)

    assert client.get_memory_context(limit=7) == {"recent_hypotheses": []}
    assert client.list_hypotheses(limit=8) == [{"id": 1}]
    assert client.list_draft_import_runs(limit=9) == [{"id": 2}]
    assert client.list_decision_logs(limit=10, draft_id=11, automation_run_id=12) == [{"id": 3}]

    request_summaries = [
        (request.method, request.url.path, request.url.query.decode()) for request in requests
    ]
    assert request_summaries == [
        ("GET", "/memory/context", "limit=7"),
        ("GET", "/hypotheses", "limit=8"),
        ("GET", "/draft-import-runs", "limit=9"),
        ("GET", "/decision-logs", "limit=10&draft_id=11&automation_run_id=12"),
    ]


def test_mcp_import_generated_drafts_sends_memory_payload():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content
        return httpx.Response(201, json={"imported_count": 1})

    client = _client(handler)

    result = client.import_generated_drafts(
        idea_id=4,
        source="codex_mcp",
        prompt_version="memory-v1",
        context_snapshot={"metrics": {"posts": 0}},
        hypotheses=[{"statement": "A stored hypothesis."}],
        drafts=[{"content": "A draft."}],
        metadata={"operator": "codex"},
    )

    assert result == {"imported_count": 1}
    payload = json.loads(captured["body"].decode())
    assert captured["path"] == "/drafts/import"
    assert payload["prompt_version"] == "memory-v1"
    assert payload["context_snapshot"] == {"metrics": {"posts": 0}}
    assert payload["hypotheses"] == [{"statement": "A stored hypothesis."}]


def test_mcp_client_redacts_api_key_from_error_response():
    api_key = "ga_test_secret"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"detail": f"Invalid API key {api_key}"},
        )

    client = _client(handler, api_key=api_key)

    with pytest.raises(GrowthAgentMCPError) as exc_info:
        client.get_automation_status()

    assert api_key not in str(exc_info.value)
    assert "****" in str(exc_info.value)


def test_mcp_client_requires_api_key_before_request():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    client = _client(handler, api_key="")

    with pytest.raises(GrowthAgentMCPError) as exc_info:
        client.get_automation_status()

    assert "GROWTH_AGENT_API_KEY" in str(exc_info.value)
    assert called is False


def _client(handler, *, api_key: str = "ga_test_secret") -> GrowthAgentMCPClient:
    return GrowthAgentMCPClient(
        GrowthAgentMCPConfig(
            base_url="http://growth-agent.test",
            api_key=api_key,
            timeout_seconds=5,
        ),
        transport=httpx.MockTransport(handler),
    )
