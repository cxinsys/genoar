"""Unit tests for VectorService using synthetic FAISS fixtures."""

import json
import time
from pathlib import Path

import faiss
import numpy as np
import pytest

from app.services.vector_service import VectorService


@pytest.fixture
def small_faiss_fixture(tmp_path: Path):
    """Create a small FAISS index (5 vectors, dim=8) with mapping file."""
    dim = 8
    n_vectors = 5
    rng = np.random.RandomState(42)
    vectors = rng.randn(n_vectors, dim).astype(np.float32)
    # L2 normalize for inner product similarity
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors / norms

    index = faiss.IndexFlatIP(dim)
    index.add(vectors)

    index_path = str(tmp_path / "test_index.bin")
    faiss.write_index(index, index_path)

    run_ids = ["SRR001", "SRR002", "SRR003", "SRR004", "SRR005"]
    mapping_path = str(tmp_path / "test_mapping.json")
    with open(mapping_path, "w") as f:
        json.dump(run_ids, f)

    return index_path, mapping_path, run_ids


def _make_service(index_path: str, mapping_path: str) -> VectorService:
    return VectorService(
        faiss_index_path=index_path,
        run_mapping_path=mapping_path,
        embedding_model_name="unused-in-unit-tests",
    )


class TestInitialize:
    def test_initialize_success(self, small_faiss_fixture):
        index_path, mapping_path, run_ids = small_faiss_fixture
        svc = _make_service(index_path, mapping_path)
        svc.initialize()

        assert svc.is_initialized is True
        assert svc.total_vectors == len(run_ids)

    def test_initialize_missing_index(self, tmp_path):
        mapping_path = str(tmp_path / "mapping.json")
        with open(mapping_path, "w") as f:
            json.dump(["SRR001"], f)

        svc = _make_service(str(tmp_path / "nonexistent.bin"), mapping_path)
        with pytest.raises(FileNotFoundError):
            svc.initialize()

    def test_initialize_missing_mapping(self, tmp_path):
        # Create a valid index but no mapping
        dim = 8
        index = faiss.IndexFlatIP(dim)
        index.add(np.zeros((1, dim), dtype=np.float32))
        index_path = str(tmp_path / "index.bin")
        faiss.write_index(index, index_path)

        svc = _make_service(index_path, str(tmp_path / "nonexistent.json"))
        with pytest.raises(FileNotFoundError):
            svc.initialize()

    def test_initialize_size_mismatch(self, tmp_path):
        dim = 8
        index = faiss.IndexFlatIP(dim)
        index.add(np.zeros((3, dim), dtype=np.float32))
        index_path = str(tmp_path / "index.bin")
        faiss.write_index(index, index_path)

        mapping_path = str(tmp_path / "mapping.json")
        with open(mapping_path, "w") as f:
            json.dump(["SRR001", "SRR002"], f)  # 2 != 3

        svc = _make_service(index_path, mapping_path)
        with pytest.raises(ValueError, match="Index size"):
            svc.initialize()


class TestSearchSimilarById:
    def test_search_similar_known_id(self, small_faiss_fixture):
        index_path, mapping_path, _ = small_faiss_fixture
        svc = _make_service(index_path, mapping_path)
        svc.initialize()

        results = svc.search_similar_by_id("SRR001", top_k=3)
        assert len(results) > 0
        assert all(r.run_id != "SRR001" for r in results)

    def test_search_similar_unknown_id(self, small_faiss_fixture):
        index_path, mapping_path, _ = small_faiss_fixture
        svc = _make_service(index_path, mapping_path)
        svc.initialize()

        results = svc.search_similar_by_id("NONEXISTENT", top_k=3)
        assert results == []

    def test_search_similar_self_exclusion(self, small_faiss_fixture):
        index_path, mapping_path, run_ids = small_faiss_fixture
        svc = _make_service(index_path, mapping_path)
        svc.initialize()

        for rid in run_ids:
            results = svc.search_similar_by_id(rid, top_k=10)
            result_ids = [r.run_id for r in results]
            assert rid not in result_ids

    def test_search_similar_top_k_larger_than_total(self, small_faiss_fixture):
        index_path, mapping_path, run_ids = small_faiss_fixture
        svc = _make_service(index_path, mapping_path)
        svc.initialize()

        results = svc.search_similar_by_id("SRR001", top_k=100)
        # Should return at most ntotal-1 (exclude self)
        assert len(results) <= len(run_ids) - 1

    def test_search_similar_top_k_zero(self, small_faiss_fixture):
        index_path, mapping_path, _ = small_faiss_fixture
        svc = _make_service(index_path, mapping_path)
        svc.initialize()

        results = svc.search_similar_by_id("SRR001", top_k=0)
        assert len(results) == 1  # Clamped to 1

    def test_search_similar_top_k_negative(self, small_faiss_fixture):
        index_path, mapping_path, _ = small_faiss_fixture
        svc = _make_service(index_path, mapping_path)
        svc.initialize()

        results = svc.search_similar_by_id("SRR001", top_k=-5)
        assert len(results) == 1  # Clamped to 1

    def test_search_similar_scores_descending(self, small_faiss_fixture):
        index_path, mapping_path, _ = small_faiss_fixture
        svc = _make_service(index_path, mapping_path)
        svc.initialize()

        results = svc.search_similar_by_id("SRR001", top_k=4)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)


class TestSearchByText:
    def test_search_by_text_empty_query(self, small_faiss_fixture):
        index_path, mapping_path, _ = small_faiss_fixture
        svc = _make_service(index_path, mapping_path)
        svc.initialize()

        assert svc.search_by_text("", top_k=5) == []

    def test_search_by_text_whitespace_query(self, small_faiss_fixture):
        index_path, mapping_path, _ = small_faiss_fixture
        svc = _make_service(index_path, mapping_path)
        svc.initialize()

        assert svc.search_by_text("   \t\n  ", top_k=5) == []


class TestLifecycle:
    def test_close_releases_resources(self, small_faiss_fixture):
        index_path, mapping_path, _ = small_faiss_fixture
        svc = _make_service(index_path, mapping_path)
        svc.initialize()
        assert svc.is_initialized is True

        svc.close()
        assert svc.is_initialized is False
        assert svc.total_vectors == 0

    def test_search_before_initialize(self, small_faiss_fixture):
        index_path, mapping_path, _ = small_faiss_fixture
        svc = _make_service(index_path, mapping_path)

        with pytest.raises(RuntimeError, match="not initialized"):
            svc.search_similar_by_id("SRR001")

    def test_duplicate_run_ids_in_mapping(self, tmp_path):
        dim = 8
        n = 4
        vectors = np.random.randn(n, dim).astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / norms

        index = faiss.IndexFlatIP(dim)
        index.add(vectors)
        index_path = str(tmp_path / "index.bin")
        faiss.write_index(index, index_path)

        # Duplicate: SRR001 appears at positions 0 and 2
        run_ids = ["SRR001", "SRR002", "SRR001", "SRR003"]
        mapping_path = str(tmp_path / "mapping.json")
        with open(mapping_path, "w") as f:
            json.dump(run_ids, f)

        svc = _make_service(index_path, mapping_path)
        svc.initialize()

        # First occurrence (position 0) should be used for reverse lookup
        results = svc.search_similar_by_id("SRR001", top_k=3)
        # Should still work — uses position 0's vector
        assert isinstance(results, list)


class TestTheEncoderIsSharedAcrossServices:
    @pytest.fixture
    def fake_transformers(self, monkeypatch):
        """A stand-in SentenceTransformer that records constructions."""
        import sys
        import types

        from app.services import vector_service

        created = []

        class FakeEncoder:
            def get_sentence_embedding_dimension(self):
                return 3

        def construct(name, cache_folder=None):
            created.append(name)
            time.sleep(0.02)  # wide enough for a second thread to race in
            return FakeEncoder()

        fake = types.SimpleNamespace(SentenceTransformer=construct)
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
        monkeypatch.setattr(vector_service, "_shared_models", {})
        return created

    def test_one_model_name_loads_one_object(self, fake_transformers):
        """Two datasets embedding with the same model share its weights.

        Each dataset has its own index and so its own service, but the
        encoder is identical for the same model name — loading it per
        dataset would put the same hundreds of megabytes in memory twice.
        """
        from app.services import vector_service

        first = vector_service._shared_model("sapbert", None)
        second = vector_service._shared_model("sapbert", None)
        other = vector_service._shared_model("minilm", None)

        assert first.model is second.model
        assert other.model is not first.model
        assert fake_transformers == ["sapbert", "minilm"]

    def test_concurrent_first_calls_still_load_one_object(
        self, fake_transformers
    ):
        """The first request from each dataset can arrive at the same time.

        An unlocked check-then-set built one encoder per caller and each
        service kept its own — the duplication the cache exists to prevent,
        arriving exactly when both datasets warm up at once.
        """
        import threading

        from app.services import vector_service

        barrier = threading.Barrier(2)
        entries = []

        def load():
            barrier.wait()
            entries.append(vector_service._shared_model("sapbert", None))

        threads = [threading.Thread(target=load) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert fake_transformers == ["sapbert"]
        assert entries[0] is entries[1]

    def test_services_sharing_a_model_share_its_encode_lock(
        self, fake_transformers
    ):
        """Serialized encoding must hold for the object actually shared.

        A lock owned per service serializes nothing once the encoder itself is
        shared.
        """
        from types import SimpleNamespace

        def service():
            svc = _make_service("index.bin", "mapping.json")
            svc._index = SimpleNamespace(d=3)
            svc._ensure_model()
            return svc

        a, b = service(), service()

        assert a._model is b._model
        assert a._encode_lock is b._encode_lock
