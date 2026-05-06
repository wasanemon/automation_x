from datetime import UTC, datetime

import httpx
import pytest

from growth_agent.clients.postiz import ExternalClientError
from growth_agent.clients.x_api import XApiClient, XCredentialsMissingError
from growth_agent.config import Settings


def test_x_client_lists_user_tweets_with_public_fields(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "1234567890",
                        "text": "hello from X",
                        "created_at": "2026-05-02T00:00:00Z",
                        "author_id": "42",
                        "conversation_id": "1234567890",
                        "public_metrics": {
                            "impression_count": 10,
                            "like_count": 2,
                            "retweet_count": 1,
                            "reply_count": 0,
                            "quote_count": 0,
                            "bookmark_count": 3,
                        },
                    }
                ]
            },
        )

    client = XApiClient(_settings(), transport=httpx.MockTransport(handler))
    posts = client.list_owned_posts(
        start_time=datetime(2026, 5, 1, 0, 0, 0, 123456, tzinfo=UTC),
        end_time=datetime(2026, 5, 2, 0, 0, 0, 654321, tzinfo=UTC),
    )

    assert posts[0].x_post_id == "1234567890"
    assert posts[0].metrics["impressions"] == 10
    params = requests[0].url.params
    assert requests[0].url.path == "/2/users/42/tweets"
    assert params["tweet.fields"] == "created_at,public_metrics,author_id,conversation_id"
    assert params["exclude"] == "retweets,replies"
    assert params["start_time"] == "2026-05-01T00:00:00Z"
    assert params["end_time"] == "2026-05-02T00:00:00Z"
    assert "organic_metrics" not in str(requests[0].url)
    assert requests[0].headers["authorization"] == "Bearer token-secret"


def test_x_client_gets_tweet_public_metrics_with_missing_fields_as_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/2/tweets/1234567890"
        assert request.url.params["tweet.fields"] == (
            "created_at,public_metrics,author_id,conversation_id"
        )
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "1234567890",
                    "text": "metric post",
                    "public_metrics": {
                        "impression_count": 22,
                        "like_count": 4,
                    },
                }
            },
        )

    metrics = XApiClient(_settings(), transport=httpx.MockTransport(handler)).get_post_metrics(
        "1234567890"
    )

    assert metrics.impressions == 22
    assert metrics.likes == 4
    assert metrics.reposts == 0
    assert metrics.bookmarks == 0


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, "authentication failed"),
        (403, "authorization failed"),
        (404, "not found"),
    ],
)
def test_x_client_non_retryable_errors_are_clear(status_code, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=f"token-secret {status_code}")

    client = XApiClient(_settings(max_external_retries=2), transport=httpx.MockTransport(handler))

    with pytest.raises(ExternalClientError) as exc_info:
        client.get_post_metrics("1234567890")

    message = str(exc_info.value)
    assert expected in message
    assert "token-secret" not in message
    assert "****" in message


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (429, "rate limit"),
        (500, "server error"),
    ],
)
def test_x_client_transient_errors_use_bounded_retries(monkeypatch, status_code, expected):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, text=str(status_code))

    monkeypatch.setattr("growth_agent.clients.x_api.sleep", lambda seconds: None)
    client = XApiClient(_settings(max_external_retries=2), transport=httpx.MockTransport(handler))

    with pytest.raises(ExternalClientError) as exc_info:
        client.get_post_metrics("1234567890")

    assert calls == 3
    assert expected in str(exc_info.value)
    assert "bounded retries" in str(exc_info.value)


def test_x_client_credentials_missing_raises_safe_error():
    client = XApiClient(Settings(x_bearer_token="", x_user_id=""))

    with pytest.raises(XCredentialsMissingError) as exc_info:
        client.list_owned_posts()

    assert "must be configured" in str(exc_info.value)


def _settings(**overrides) -> Settings:
    values = {
        "x_api_base_url": "https://api.x.com",
        "x_bearer_token": "token-secret",
        "x_user_id": "42",
        "request_timeout_seconds": 3,
        "max_external_retries": 0,
    }
    values.update(overrides)
    return Settings(**values)
