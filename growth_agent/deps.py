from growth_agent.clients.postiz import PostizClient
from growth_agent.clients.x_api import XApiClient
from growth_agent.config import get_settings


def get_postiz_client() -> PostizClient:
    return PostizClient(get_settings())


def get_x_client() -> XApiClient:
    return XApiClient(get_settings())
