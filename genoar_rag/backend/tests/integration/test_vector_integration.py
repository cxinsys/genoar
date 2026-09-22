"""Integration tests using real FAISS index — skipped if files not present."""

import os

import pytest

from app.services.vector_service import VectorService

FAISS_INDEX = os.environ.get("FAISS_INDEX_PATH", "/data/genoar/faiss_index.bin")
RUN_MAPPING = os.environ.get("RUN_MAPPING_PATH", "/data/genoar/run_mapping.json")
MODEL_NAME = "microsoft/BiomedNLP-PubMedBERT-base-uncased-abstract-fulltext"

skip_no_faiss = pytest.mark.skipif(
    not (os.path.exists(FAISS_INDEX) and os.path.exists(RUN_MAPPING)),
    reason="Real FAISS index files not available",
)


@skip_no_faiss
class TestRealFAISS:
    @pytest.fixture(scope="class")
    def vector_service(self):
        svc = VectorService(
            faiss_index_path=FAISS_INDEX,
            run_mapping_path=RUN_MAPPING,
            embedding_model_name=MODEL_NAME,
        )
        svc.initialize()
        yield svc
        svc.close()

    def test_real_index_loads(self, vector_service):
        assert vector_service.is_initialized
        assert vector_service.total_vectors > 0

    def test_real_similar_search(self, vector_service):
        # Use a known run_id from the mapping
        results = vector_service.search_similar_by_id("SRR22351028", top_k=5)
        assert len(results) > 0
        assert all(r.run_id != "SRR22351028" for r in results)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.slow
    def test_real_text_search(self, vector_service):
        results = vector_service.search_by_text("lung cancer RNA-seq", top_k=5)
        assert len(results) > 0
        assert all(r.score > 0 for r in results)
