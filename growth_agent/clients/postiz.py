from dataclasses import dataclass
from datetime import UTC, datetime
from time import sleep
from typing import Any

import httpx

from growth_agent.config import Settings


class ExternalClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScheduledPostResult:
    postiz_post_id: str
    integration_id: str
    raw: dict[str, Any]


class PostizClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def schedule_x_post(
        self,
        content: str,
        scheduled_for: datetime,
        has_url: bool,
    ) -> ScheduledPostResult:
        if not self.settings.postiz_api_key or not self.settings.postiz_x_integration_id:
            raise ExternalClientError("Postiz API key and X integration ID must be configured.")

        payload = self._schedule_payload(content, scheduled_for, has_url)
        response_json = self._request("POST", "/posts", json=payload)
        post_id = self._extract_post_id(response_json)
        return ScheduledPostResult(
            postiz_post_id=post_id,
            integration_id=self.settings.postiz_x_integration_id,
            raw=response_json,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.settings.postiz_base_url.rstrip('/')}{path}"
        headers = {"Authorization": self.settings.postiz_api_key}
        last_error: Exception | None = None

        for attempt in range(self.settings.http_max_retries + 1):
            try:
                with httpx.Client(timeout=self.settings.http_timeout_seconds) as client:
                    response = client.request(method, url, headers=headers, **kwargs)
                if response.status_code < 400:
                    data = response.json()
                    return data if isinstance(data, dict) else {"data": data}
                if response.status_code not in {429, 500, 502, 503, 504}:
                    raise ExternalClientError(
                        f"Postiz request failed with status {response.status_code}."
                    )
                last_error = ExternalClientError(
                    f"Postiz transient failure with status {response.status_code}."
                )
            except httpx.HTTPError as exc:
                last_error = exc
            if attempt < self.settings.http_max_retries:
                sleep(0.2 * (attempt + 1))

        raise ExternalClientError("Postiz request failed after bounded retries.") from last_error

    def _schedule_payload(
        self,
        content: str,
        scheduled_for: datetime,
        has_url: bool,
    ) -> dict[str, Any]:
        scheduled_utc = scheduled_for.astimezone(UTC)
        tags = [{"value": "has_url=true", "label": "has_url=true"}] if has_url else []
        return {
            "type": "schedule",
            "date": scheduled_utc.isoformat().replace("+00:00", "Z"),
            "shortLink": False,
            "tags": tags,
            "posts": [
                {
                    "integration": {"id": self.settings.postiz_x_integration_id},
                    "value": [{"content": content, "image": []}],
                    "settings": {"__type": "x", "who_can_reply_post": "everyone"},
                }
            ],
        }

    @staticmethod
    def _extract_post_id(response_json: dict[str, Any]) -> str:
        candidates = [
            response_json.get("id"),
            response_json.get("_id"),
            response_json.get("postId"),
            response_json.get("post_id"),
        ]
        data = response_json.get("data")
        if isinstance(data, dict):
            candidates.extend([data.get("id"), data.get("_id"), data.get("postId")])
        if isinstance(data, list) and data and isinstance(data[0], dict):
            candidates.extend([data[0].get("id"), data[0].get("_id"), data[0].get("postId")])
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return "unknown"
