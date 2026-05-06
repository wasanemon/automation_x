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
