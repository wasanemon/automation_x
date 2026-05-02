from dataclasses import dataclass
from datetime import datetime
from time import sleep
from typing import Any

import httpx

from growth_agent.clients.postiz import ExternalClientError
from growth_agent.config import Settings


@dataclass(frozen=True)
class OwnedPost:
    x_post_id: str
    text: str
    created_at: datetime | None
    metrics: dict[str, int]


@dataclass(frozen=True)
class XMetrics:
    impressions: int = 0
    likes: int = 0
    replies: int = 0
    reposts: int = 0
    quotes: int = 0
    bookmarks: int = 0


class XApiClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def list_owned_posts(
        self, start_time: datetime | None = None, end_time: datetime | None = None
    ) -> list[OwnedPost]:
        if not self.settings.x_bearer_token or not self.settings.x_user_id:
            raise ExternalClientError("X bearer token and user ID must be configured.")

        params: dict[str, Any] = {
            "tweet.fields": "created_at,public_metrics,organic_metrics,non_public_metrics",
            "max_results": 100,
            "exclude": "retweets,replies",
        }
        if start_time:
            params["start_time"] = start_time.isoformat().replace("+00:00", "Z")
        if end_time:
            params["end_time"] = end_time.isoformat().replace("+00:00", "Z")

        data = self._request("GET", f"/2/users/{self.settings.x_user_id}/tweets", params=params)
        posts: list[OwnedPost] = []
        for item in data.get("data", []):
            metrics = self._metrics_from_payload(item)
            created_at = item.get("created_at")
            posts.append(
                OwnedPost(
                    x_post_id=str(item["id"]),
                    text=item.get("text", ""),
                    created_at=datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    if created_at
                    else None,
                    metrics=metrics,
                )
            )
        return posts

    def get_post_metrics(self, x_post_id: str) -> XMetrics:
        if not self.settings.x_bearer_token:
            raise ExternalClientError("X bearer token must be configured.")

        data = self._request(
            "GET",
            f"/2/tweets/{x_post_id}",
            params={"tweet.fields": "public_metrics,organic_metrics,non_public_metrics"},
        )
        item = data.get("data", {})
        metrics = self._metrics_from_payload(item)
        return XMetrics(**metrics)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.settings.x_api_base_url.rstrip('/')}{path}"
        headers = {"Authorization": f"Bearer {self.settings.x_bearer_token}"}
        last_error: Exception | None = None

        for attempt in range(self.settings.max_external_retries + 1):
            try:
                with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                    response = client.request(method, url, headers=headers, **kwargs)
                if response.status_code < 400:
                    data = response.json()
                    return data if isinstance(data, dict) else {"data": data}
                if response.status_code not in {429, 500, 502, 503, 504}:
                    raise ExternalClientError(
                        f"X API request failed with status {response.status_code}."
                    )
                last_error = ExternalClientError(
                    f"X API transient failure with status {response.status_code}."
                )
            except httpx.HTTPError as exc:
                last_error = exc
            if attempt < self.settings.max_external_retries:
                sleep(0.2 * (attempt + 1))

        raise ExternalClientError("X API request failed after bounded retries.") from last_error

    @staticmethod
    def _metrics_from_payload(item: dict[str, Any]) -> dict[str, int]:
        merged: dict[str, int] = {}
        for key in ("public_metrics", "organic_metrics", "non_public_metrics"):
            value = item.get(key, {})
            if isinstance(value, dict):
                merged.update({metric: int(count or 0) for metric, count in value.items()})
        return {
            "impressions": merged.get("impression_count", 0),
            "likes": merged.get("like_count", 0),
            "replies": merged.get("reply_count", 0),
            "reposts": merged.get("retweet_count", 0),
            "quotes": merged.get("quote_count", 0),
            "bookmarks": merged.get("bookmark_count", 0),
        }
