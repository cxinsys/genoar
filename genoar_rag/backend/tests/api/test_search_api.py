"""API tests for /api/v1/search endpoint."""

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_vector_service
from app.services.vector_service import VectorSearchResult
from tests.api.conftest import _create_test_app


class MockVectorServiceForAPI:
    """Returns fixed results matching test DB run IDs."""

    def __init__(self):
        self._results = [
            VectorSearchResult("SRR001", 0.95),
            VectorSearchResult("SRR003", 0.88),
            VectorSearchResult("SRR005", 0.72),
            VectorSearchResult("SRR007", 0.60),
            VectorSearchResult("SRR009", 0.45),
        ]

    @property
    def total_vectors(self) -> int:
        return 100

    def search_by_text(
        self, query: str, top_k: int = 10, organisms: list[str] | None = None
    ) -> list[VectorSearchResult]:
        if not query or not query.strip():
            return []
        return self._results[:top_k]


@pytest.fixture
def semantic_client(api_pool):
    """TestClient with MockVectorService injected."""
    app = _create_test_app(api_pool)
    mock_vs = MockVectorServiceForAPI()
    app.dependency_overrides[get_vector_service] = lambda: mock_vs
    with TestClient(app) as c:
        yield c


class TestSearchAPI:
    def test_search_no_filters(self, client):
        resp = client.get("/api/v1/search")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 10
        assert len(data["items"]) == 10

    def test_search_by_organism(self, client):
        resp = client.get("/api/v1/search?organism=Homo+sapiens")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 8

    def test_search_by_tissue(self, client):
        resp = client.get("/api/v1/search?tissue=Bone+Marrow")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    def test_search_multiple_filters(self, client):
        resp = client.get("/api/v1/search?tissue=Blood&organism=Homo+sapiens")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    def test_search_by_keyword(self, client):
        resp = client.get("/api/v1/search?keyword=bone")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2

    def test_search_by_assay_type(self, client):
        resp = client.get("/api/v1/search?assay_type=ATAC-seq")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    def test_search_by_disease(self, client):
        resp = client.get("/api/v1/search?disease=leukemia")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["run_id"] == "SRR001"

    def test_search_pagination(self, client):
        resp = client.get("/api/v1/search?limit=3&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 3
        assert data["total"] == 10
        assert data["offset"] == 0
        assert data["limit"] == 3

    def test_search_sort_desc(self, client):
        resp = client.get("/api/v1/search?sort_order=desc")
        assert resp.status_code == 200
        data = resp.json()
        run_ids = [item["run_id"] for item in data["items"]]
        assert run_ids == sorted(run_ids, reverse=True)

    def test_search_filters_in_response(self, client):
        resp = client.get("/api/v1/search?tissue=Blood")
        data = resp.json()
        assert data["filters_applied"]["tissue"] == ["Blood"]

    def test_search_multiple_organisms(self, client):
        resp = client.get("/api/v1/search?organism=Homo+sapiens&organism=Mus+musculus")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 10  # all samples


class TestSearchBackwardCompatibility:
    def test_search_default_mode_is_sql(self, client):
        resp = client.get("/api/v1/search")
        assert resp.status_code == 200
        data = resp.json()
        assert data["search_mode"] == "sql"

    def test_search_explicit_sql_mode(self, client):
        resp = client.get("/api/v1/search?search_mode=sql")
        assert resp.status_code == 200
        data = resp.json()
        assert data["search_mode"] == "sql"
        assert data["total"] == 10

    def test_sql_mode_no_similarity_score(self, client):
        resp = client.get("/api/v1/search")
        data = resp.json()
        for item in data["items"]:
            assert item["similarity_score"] is None


class TestSemanticSearchAPI:
    def test_semantic_returns_scored_results(self, semantic_client):
        resp = semantic_client.get(
            "/api/v1/search?keyword=lung+cancer&search_mode=semantic"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) > 0
        for item in data["items"]:
            assert item["similarity_score"] is not None

    def test_semantic_no_keyword_empty_result(self, semantic_client):
        resp = semantic_client.get("/api/v1/search?search_mode=semantic")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_semantic_unavailable_503(self, client):
        """Default client has vector_service=None → 503."""
        resp = client.get("/api/v1/search?keyword=test&search_mode=semantic")
        assert resp.status_code == 503

    def test_semantic_response_has_search_mode(self, semantic_client):
        resp = semantic_client.get(
            "/api/v1/search?keyword=test&search_mode=semantic"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["search_mode"] == "semantic"


class TestHybridSearchAPI:
    def test_hybrid_with_keyword_and_filters(self, semantic_client):
        resp = semantic_client.get(
            "/api/v1/search?keyword=test&tissue=Blood&search_mode=hybrid"
        )
        assert resp.status_code == 200
        data = resp.json()
        # Intersection of semantic (SRR001,003,005,007,009) and Blood (SRR003,004)
        run_ids = [item["run_id"] for item in data["items"]]
        assert "SRR003" in run_ids
        for item in data["items"]:
            assert item["similarity_score"] is not None

    def test_hybrid_no_keyword_acts_as_sql(self, semantic_client):
        resp = semantic_client.get(
            "/api/v1/search?tissue=Blood&search_mode=hybrid"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["search_mode"] == "hybrid"

    def test_hybrid_unavailable_503(self, client):
        resp = client.get("/api/v1/search?keyword=test&search_mode=hybrid")
        assert resp.status_code == 503


class TestSearchEdgeCases:
    def test_invalid_search_mode_422(self, client):
        resp = client.get("/api/v1/search?search_mode=invalid")
        assert resp.status_code == 422
