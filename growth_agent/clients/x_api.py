from dataclasses import dataclass
from datetime import datetime
from time import sleep
from typing import Any

import httpx

from growth_agent.clients.postiz import ExternalClientError
from growth_agent.config import Settings

PUBLIC_TWEET_FIELDS = "created_at,public_metrics,author_id,conversation_id"
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class OwnedPost:
    x_post_id: str
    text: str
    created_at: datetime | None
    metrics: dict[str, int]
    author_id: str | None = None
    conversation_id: str | None = None


@dataclass(frozen=True)
class XMetrics:
    impressions: int = 0
    likes: int = 0
    replies: int = 0
    reposts: int = 0
    quotes: int = 0
    bookmarks: int = 0


class XCredentialsMissingError(ExternalClientError):
    pass


class XApiClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    @property
    def credentials_ready(self) -> bool:
        return bool(self.settings.x_bearer_token and self.settings.x_user_id)

    def list_owned_posts(
        self, start_time: datetime | None = None, end_time: datetime | None = None
    ) -> list[OwnedPost]:
        return self.list_user_tweets(start_time=start_time, end_time=end_time)

    def list_user_tweets(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        max_results: int = 100,
    ) -> list[OwnedPost]:
        if not self.settings.x_bearer_token or not self.settings.x_user_id:
            raise XCredentialsMissingError("X bearer token and user ID must be configured.")

        params: dict[str, Any] = {
            "tweet.fields": PUBLIC_TWEET_FIELDS,
            "max_results": max(5, min(max_results, 100)),
            "exclude": "retweets,replies",
        }
        if start_time:
            params["start_time"] = start_time.isoformat().replace("+00:00", "Z")
        if end_time:
            params["end_time"] = end_time.isoformat().replace("+00:00", "Z")

        data = self._request("GET", f"/2/users/{self.settings.x_user_id}/tweets", params=params)
        return [self._owned_post_from_payload(item) for item in data.get("data", [])]

    def get_tweet(self, x_post_id: str) -> OwnedPost:
        if not self.settings.x_bearer_token:
            raise XCredentialsMissingError("X bearer token must be configured.")

        data = self._request(
            "GET",
            f"/2/tweets/{x_post_id}",
            params={"tweet.fields": PUBLIC_TWEET_FIELDS},
        )
        item = data.get("data")
        if not isinstance(item, dict):
            raise ExternalClientError("X API response did not include tweet data.")
        return self._owned_post_from_payload(item)

    def get_post_metrics(self, x_post_id: str) -> XMetrics:
        if not self.settings.x_bearer_token:
            raise XCredentialsMissingError("X bearer token must be configured.")

        metrics = self.get_tweet(x_post_id).metrics
        return XMetrics(**metrics)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.settings.x_api_base_url.rstrip('/')}{path}"
        headers = {"Authorization": f"Bearer {self.settings.x_bearer_token}"}
        last_error: Exception | None = None

        for attempt in range(self.settings.max_external_retries + 1):
            try:
                with httpx.Client(
                    timeout=self.settings.request_timeout_seconds,
                    transport=self.transport,
                ) as client:
                    response = client.request(method, url, headers=headers, **kwargs)
                if response.status_code < 400:
                    data = response.json()
                    return data if isinstance(data, dict) else {"data": data}
                if response.status_code not in TRANSIENT_STATUS_CODES:
                    raise self._status_error(response)
                last_error = self._status_error(response)
            except httpx.HTTPError as exc:
                last_error = exc
            if attempt < self.settings.max_external_retries:
                sleep(0.2 * (attempt + 1))

        message = "X API request failed after bounded retries."
        if last_error is not None:
            message = f"{message} Last error: {last_error}"
        raise ExternalClientError(message) from last_error

    def _status_error(self, response: httpx.Response) -> ExternalClientError:
        status_code = response.status_code
        excerpt = self._safe_response_excerpt(response)
        if status_code == 401:
            message = "X API authentication failed with status 401. Check X_BEARER_TOKEN."
        elif status_code == 403:
            message = "X API authorization failed with status 403. Check token access."
        elif status_code == 404:
            message = "X API resource was not found with status 404."
        elif status_code == 429:
            message = "X API rate limit reached with status 429."
        elif 500 <= status_code <= 599:
            message = f"X API server error with status {status_code}."
        else:
            message = f"X API request failed with status {status_code}."
        if excerpt:
            message = f"{message} Response: {excerpt}"
        return ExternalClientError(message)

    def _safe_response_excerpt(self, response: httpx.Response) -> str:
        text = response.text.strip()
        if not text:
            return ""
        token = self.settings.x_bearer_token
        if token:
            text = text.replace(token, "****")
        return text[:500]

    @classmethod
    def _owned_post_from_payload(cls, item: dict[str, Any]) -> OwnedPost:
        created_at = item.get("created_at")
        return OwnedPost(
            x_post_id=str(item["id"]),
            text=item.get("text", ""),
            created_at=(
                datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if created_at
                else None
            ),
            metrics=cls._metrics_from_payload(item),
            author_id=str(item["author_id"]) if item.get("author_id") is not None else None,
            conversation_id=(
                str(item["conversation_id"]) if item.get("conversation_id") is not None else None
            ),
        )

    @staticmethod
    def _metrics_from_payload(item: dict[str, Any]) -> dict[str, int]:
        public_metrics = item.get("public_metrics", {})
        if not isinstance(public_metrics, dict):
            public_metrics = {}
        return {
            "impressions": int(public_metrics.get("impression_count") or 0),
            "likes": int(public_metrics.get("like_count") or 0),
            "replies": int(public_metrics.get("reply_count") or 0),
            "reposts": int(public_metrics.get("retweet_count") or 0),
            "quotes": int(public_metrics.get("quote_count") or 0),
            "bookmarks": int(public_metrics.get("bookmark_count") or 0),
        }
