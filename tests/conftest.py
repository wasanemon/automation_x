import os
from collections.abc import Generator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("TESTING", "true")

from growth_agent.clients.openai_client import StructuredResponseResult
from growth_agent.clients.postiz import ScheduledPostResult
from growth_agent.clients.x_api import OwnedPost, XMetrics
from growth_agent.config import get_settings
from growth_agent.database import get_db
from growth_agent.deps import get_openai_client, get_postiz_client, get_x_client
from growth_agent.main import app
from growth_agent.models import Base


class MockPostizClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def schedule_x_post(
        self, content: str, scheduled_for: datetime, has_url: bool
    ) -> ScheduledPostResult:
        self.calls.append(
            {"content": content, "scheduled_for": scheduled_for, "has_url": has_url}
        )
        return ScheduledPostResult(
            postiz_post_id=f"postiz-{len(self.calls)}",
            integration_id="integration-x",
            raw={"id": f"postiz-{len(self.calls)}"},
        )


class FailingPostizClient:
    def __init__(self) -> None:
        self.calls = 0

    def schedule_x_post(self, content: str, scheduled_for: datetime, has_url: bool):
        from growth_agent.clients.postiz import ExternalClientError

        self.calls += 1
        raise ExternalClientError("Postiz unavailable in test.")


class MockXClient:
    def __init__(self) -> None:
        self.owned_posts: list[OwnedPost] = []
        self.metrics: dict[str, XMetrics] = {}
        self.metric_errors: dict[str, Exception] = {}
        self.list_calls = 0
        self.metrics_calls: list[str] = []

    def list_owned_posts(self, start_time=None, end_time=None):
        self.list_calls += 1
        return self.owned_posts

    def get_post_metrics(self, x_post_id: str) -> XMetrics:
        self.metrics_calls.append(x_post_id)
        if x_post_id in self.metric_errors:
            raise self.metric_errors[x_post_id]
        return self.metrics.get(
            x_post_id,
            XMetrics(impressions=100, likes=5, replies=1, reposts=2, quotes=0, bookmarks=1),
        )


class MockOpenAIClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.output: dict[str, object] = {
            "drafts": [
                {
                    "content": (
                        "A practical operating loop gets easier to trust "
                        "when every step is observable."
                    ),
                    "hypothesis": "Concrete workflow posts should earn more bookmarks.",
                    "target_audience": "builders",
                    "expected_metric": "bookmarks",
                    "confidence": 0.92,
                    "risk_notes": [],
                    "contains_url": False,
                    "contains_claim": False,
                    "requires_human_review_by_model": False,
                }
            ],
            "hypotheses": [
                {
                    "title": "Concrete workflow posts improve saves",
                    "rationale": "Recent posts reward specificity.",
                    "target_metric": "bookmarks",
                    "expected_effect": "Higher save rate.",
                    "confidence": 0.76,
                }
            ],
        }

    @property
    def credentials_ready(self) -> bool:
        return bool(get_settings().openai_api_key)

    def create_structured_response(self, **kwargs) -> StructuredResponseResult:
        self.calls.append(kwargs)
        return StructuredResponseResult(
            response_id=f"resp-{len(self.calls)}",
            output=dict(self.output),
            usage={"input_tokens": 100, "output_tokens": 80},
        )


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    Base.metadata.create_all(engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture(autouse=True)
def reset_settings_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.setenv("SCHEDULING_DRY_RUN", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LLM_GENERATION_ENABLED", "false")
    monkeypatch.setenv("LLM_ANALYSIS_ENABLED", "false")
    monkeypatch.setenv("LLM_FULL_AUTO_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def mock_postiz() -> MockPostizClient:
    return MockPostizClient()


@pytest.fixture
def failing_postiz() -> FailingPostizClient:
    return FailingPostizClient()


@pytest.fixture
def mock_x() -> MockXClient:
    return MockXClient()


@pytest.fixture
def mock_openai() -> MockOpenAIClient:
    return MockOpenAIClient()


@pytest.fixture
def client(
    db_session: Session,
    mock_postiz: MockPostizClient,
    mock_x: MockXClient,
    mock_openai: MockOpenAIClient,
):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_postiz_client] = lambda: mock_postiz
    app.dependency_overrides[get_x_client] = lambda: mock_x
    app.dependency_overrides[get_openai_client] = lambda: mock_openai
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
