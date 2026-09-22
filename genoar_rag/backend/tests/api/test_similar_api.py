"""API tests for the /similar endpoint using mock VectorService."""

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_vector_service
from app.services.vector_service import VectorSearchResult


class MockVectorService:
    """Lightweight mock that returns canned results."""

    def __init__(self, results: list[VectorSearchResult] | None = None):
        self._results = results or []
        self._initialized = True

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def total_vectors(self) -> int:
        return 100

    # Which runs the index was built over. Not every run in the database is in
    # it — the real router returns nothing for a run no backend contains — and
    # that case is the one a population the index predates will be full of.
    INDEXED = {"SRR001", "SRR002", "SRR003", "SRR005"}

    def search_similar_by_id(self, run_id: str, top_k: int = 10):
        if run_id not in self.INDEXED:
            return []
        return self._results[:top_k]


# Fixtures for mock data — uses SRR001-010 from test DB
MOCK_RESULTS = [
    VectorSearchResult(run_id="SRR002", score=0.95),
    VectorSearchResult(run_id="SRR003", score=0.88),
    VectorSearchResult(run_id="SRR005", score=0.72),
]


@pytest.fixture
def similar_client(api_pool):
    """TestClient with a MockVectorService injected."""
    from tests.api.conftest import _create_test_app

    app = _create_test_app(api_pool)
    mock_vs = MockVectorService(MOCK_RESULTS)
    app.dependency_overrides[get_vector_service] = lambda: mock_vs
    with TestClient(app) as c:
        yield c


@pytest.fixture
def disabled_client(api_pool):
    """TestClient with semantic search disabled (vector_service=None)."""
    from tests.api.conftest import _create_test_app

    app = _create_test_app(api_pool)
    app.dependency_overrides[get_vector_service] = lambda: None
    with TestClient(app) as c:
        yield c


class TestSimilarEndpoint:
    def test_similar_returns_results(self, similar_client):
        resp = similar_client.get("/api/v1/samples/SRR001/similar")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 3
        # Check metadata enrichment from test DB
        item = data["items"][0]
        assert item["run_id"] == "SRR002"
        assert item["similarity_score"] == 0.95

    def test_similar_to_a_sample_that_is_not_here_is_404(self, similar_client):
        """Not an empty list — an empty list answers the question.

        "What is this sample like" has no empty answer when there is no such
        sample; it has no answer. This matters now that a run can be absent by
        being in the other corpus rather than by not existing: 200 with nothing
        in it would render a panel on a page that should not exist, and say the
        sample simply had no neighbours.
        """
        resp = similar_client.get("/api/v1/samples/UNKNOWN/similar")
        assert resp.status_code == 404

    def test_a_sample_the_index_has_never_seen_is_empty_not_404(
        self, similar_client
    ):
        """The other way round: present here, absent from the index.

        A run the paper's table names and the vector index was never built over
        is in the corpus and has no neighbours to offer. That is an empty
        answer, and the panel showing nothing is correct.
        """
        resp = similar_client.get("/api/v1/samples/SRR010/similar")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_similar_semantic_disabled(self, disabled_client):
        resp = disabled_client.get("/api/v1/samples/SRR001/similar")
        assert resp.status_code == 503
        assert "not available" in resp.json()["detail"]

    def test_similar_limit_param(self, similar_client):
        resp = similar_client.get("/api/v1/samples/SRR001/similar?limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2

    def test_similar_limit_validation_zero(self, similar_client):
        resp = similar_client.get("/api/v1/samples/SRR001/similar?limit=0")
        assert resp.status_code == 422

    def test_similar_limit_validation_too_large(self, similar_client):
        resp = similar_client.get("/api/v1/samples/SRR001/similar?limit=200")
        assert resp.status_code == 422

    def test_similar_response_shape(self, similar_client):
        resp = similar_client.get("/api/v1/samples/SRR001/similar")
        data = resp.json()
        assert "query_run_id" in data
        assert "items" in data
        assert "total" in data
        assert data["query_run_id"] == "SRR001"
        assert data["total"] == len(data["items"])
