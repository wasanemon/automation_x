from secrets import compare_digest
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from growth_agent.clients.openai_client import OpenAIClient
from growth_agent.clients.postiz import PostizClient
from growth_agent.clients.x_api import XApiClient
from growth_agent.config import get_settings


def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    settings = get_settings()
    if settings.testing:
        return
    if request.method == "GET" and settings.safe_public_reads:
        return

    expected = settings.growth_agent_api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GROWTH_AGENT_API_KEY is not configured.",
        )

    provided = x_api_key or _bearer_token(authorization)
    if not provided or not compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
        )


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def get_postiz_client() -> PostizClient:
    return PostizClient(get_settings())


def get_x_client() -> XApiClient:
    return XApiClient(get_settings())


def get_openai_client() -> OpenAIClient:
    return OpenAIClient(get_settings())
