import json
from dataclasses import dataclass
from time import sleep
from typing import Any

import httpx

from growth_agent.clients.postiz import ExternalClientError
from growth_agent.config import Settings

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


class OpenAICredentialsMissingError(ExternalClientError):
    pass


class OpenAIResponseFormatError(ExternalClientError):
    pass


@dataclass(frozen=True)
class StructuredResponseResult:
    response_id: str | None
    output: dict[str, Any]
    usage: dict[str, Any]


class OpenAIClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    @property
    def credentials_ready(self) -> bool:
        return bool(self.settings.openai_api_key)

    def create_structured_response(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
    ) -> StructuredResponseResult:
        if not self.settings.openai_api_key:
            raise OpenAICredentialsMissingError("OPENAI_API_KEY must be configured.")

        payload = {
            "model": self.settings.openai_model,
            "input": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=True, sort_keys=True),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        response_json = self._request("POST", "/v1/responses", json=payload)
        output = self._extract_output_json(response_json)
        usage = response_json.get("usage")
        return StructuredResponseResult(
            response_id=str(response_json.get("id")) if response_json.get("id") else None,
            output=output,
            usage=usage if isinstance(usage, dict) else {},
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.settings.openai_api_base_url.rstrip('/')}{path}"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None

        for attempt in range(self.settings.max_external_retries + 1):
            try:
                with httpx.Client(
                    timeout=self.settings.request_timeout_seconds,
                    transport=self.transport,
                ) as client:
                    response = client.request(method, url, headers=headers, **kwargs)
                if response.status_code < 400:
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise OpenAIResponseFormatError(
                            "OpenAI response body was not valid JSON."
                        ) from exc
                    if not isinstance(data, dict):
                        raise OpenAIResponseFormatError("OpenAI response was not a JSON object.")
                    return data
                if response.status_code not in TRANSIENT_STATUS_CODES:
                    raise self._status_error(response)
                last_error = self._status_error(response)
            except httpx.HTTPError as exc:
                last_error = exc
            if attempt < self.settings.max_external_retries:
                sleep(0.2 * (attempt + 1))

        message = "OpenAI request failed after bounded retries."
        if last_error is not None:
            message = f"{message} Last error: {self._redact(str(last_error))}"
        raise ExternalClientError(message) from last_error

    def _status_error(self, response: httpx.Response) -> ExternalClientError:
        return ExternalClientError(
            "OpenAI request failed with status "
            f"{response.status_code}: {self._safe_response_excerpt(response)}"
        )

    def _safe_response_excerpt(self, response: httpx.Response) -> str:
        text = response.text.strip() or "<empty response body>"
        return self._redact(text)[:500]

    def _redact(self, text: str) -> str:
        if self.settings.openai_api_key:
            text = text.replace(self.settings.openai_api_key, "****")
        return text

    def _extract_output_json(self, response_json: dict[str, Any]) -> dict[str, Any]:
        output_text = response_json.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return self._parse_output_text(output_text)

        for item in response_json.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    return self._parse_output_text(text)

        raise OpenAIResponseFormatError("OpenAI response did not include output JSON text.")

    @staticmethod
    def _parse_output_text(output_text: str) -> dict[str, Any]:
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise OpenAIResponseFormatError("OpenAI output text was not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise OpenAIResponseFormatError("OpenAI output JSON was not an object.")
        return parsed
