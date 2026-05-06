import json

import httpx
import pytest

from growth_agent.clients.openai_client import (
    OpenAIClient,
    OpenAICredentialsMissingError,
    OpenAIResponseFormatError,
)
from growth_agent.clients.postiz import ExternalClientError
from growth_agent.config import Settings


def test_openai_client_sends_structured_outputs_payload():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["Authorization"] == "Bearer test-openai-key"
        return httpx.Response(
            200,
            json={
                "id": "resp_123",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"drafts": [], "hypotheses": []}',
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 2},
            },
        )

    client = OpenAIClient(
        Settings(openai_api_key="test-openai-key"),
        transport=httpx.MockTransport(handler),
    )

    result = client.create_structured_response(
        system_prompt="Return JSON.",
        user_payload={"idea": {"title": "Test"}},
        schema_name="test_schema",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
    )

    assert result.response_id == "resp_123"
    assert result.output == {"drafts": [], "hypotheses": []}
    assert result.usage == {"input_tokens": 1, "output_tokens": 2}
    assert requests[0]["text"]["format"]["type"] == "json_schema"
    assert requests[0]["text"]["format"]["strict"] is True


def test_openai_client_missing_api_key_never_calls_api():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    client = OpenAIClient(Settings(openai_api_key=""), transport=httpx.MockTransport(handler))

    with pytest.raises(OpenAICredentialsMissingError):
        client.create_structured_response(
            system_prompt="Return JSON.",
            user_payload={},
            schema_name="test_schema",
            schema={"type": "object", "properties": {}, "additionalProperties": False},
        )

    assert called is False


def test_openai_client_retries_transient_errors():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, text="temporary")
        return httpx.Response(200, json={"output_text": '{"drafts": [], "hypotheses": []}'})

    client = OpenAIClient(
        Settings(openai_api_key="test-openai-key", max_external_retries=1),
        transport=httpx.MockTransport(handler),
    )

    result = client.create_structured_response(
        system_prompt="Return JSON.",
        user_payload={},
        schema_name="test_schema",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
    )

    assert result.output == {"drafts": [], "hypotheses": []}
    assert calls == 2


def test_openai_client_redacts_secret_in_errors():
    secret = "test-openai-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"bad key {secret}")

    client = OpenAIClient(Settings(openai_api_key=secret), transport=httpx.MockTransport(handler))

    with pytest.raises(ExternalClientError) as exc_info:
        client.create_structured_response(
            system_prompt="Return JSON.",
            user_payload={},
            schema_name="test_schema",
            schema={"type": "object", "properties": {}, "additionalProperties": False},
        )

    assert secret not in str(exc_info.value)
    assert "****" in str(exc_info.value)


def test_openai_client_rejects_invalid_output_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output_text": "not json"})

    client = OpenAIClient(
        Settings(openai_api_key="test-openai-key"),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(OpenAIResponseFormatError):
        client.create_structured_response(
            system_prompt="Return JSON.",
            user_payload={},
            schema_name="test_schema",
            schema={"type": "object", "properties": {}, "additionalProperties": False},
        )
