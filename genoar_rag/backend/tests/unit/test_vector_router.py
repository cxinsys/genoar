"""Unit tests for VectorRouter species routing (no model download required).

Text search would need the real embedding model, so these tests cover the parts that
don't: routing decisions (_resolve_species), id-based routing (search_similar_by_id
picks the backend that holds the run_id), graceful per-backend degradation, and the
build-model guard dropping a mismatched backend.
"""

import json
from pathlib import Path

import faiss
import numpy as np

from app.services.vector_router import VectorRouter
from app.services.vector_service import VectorSearchResult, VectorService

ORGANISM_MAP = {"Homo sapiens": "human", "Mus musculus": "mouse"}


def _make_backend(
    tmp_path: Path,
    name: str,
    run_ids: list[str],
    configured_model: str,
    sidecar_model: str | None = None,
    dim: int = 8,
) -> VectorService:
    """Create a tiny FAISS index + mapping + build-model sidecar, return a VectorService.

    sidecar_model defaults to configured_model (guard passes); pass a different value to
    simulate an index built with the wrong model.
    """
    rng = np.random.RandomState(len(name) + len(run_ids))
    vecs = rng.randn(len(run_ids), dim).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)

    index = faiss.IndexFlatIP(dim)
    index.add(vecs)
    index_path = str(tmp_path / f"{name}.bin")
    faiss.write_index(index, index_path)

    mapping_path = str(tmp_path / f"{name}_map.json")
    with open(mapping_path, "w") as f:
        json.dump(run_ids, f)
    with open(index_path + ".meta.json", "w") as f:
        json.dump({"model_name": sidecar_model or configured_model, "dim": dim}, f)

    return VectorService(
        index_path, mapping_path, configured_model, require_model_match=True
    )


def _router(tmp_path, human_ids=("H1", "H2", "H3"), mouse_ids=("M1", "M2"),
            mouse_sidecar_model=None) -> VectorRouter:
    backends = {
        "human": _make_backend(tmp_path, "human", list(human_ids), "human-model"),
        "mouse": _make_backend(
            tmp_path, "mouse", list(mouse_ids), "mouse-model",
            sidecar_model=mouse_sidecar_model,
        ),
    }
    router = VectorRouter(backends, ORGANISM_MAP, default_species="human")
    router.initialize()
    return router


class TestInitialize:
    def test_both_backends_ready(self, tmp_path):
        r = _router(tmp_path)
        assert r.is_initialized is True
        assert r.available_species() == ["human", "mouse"]
        assert r.total_vectors == 5

    def test_mismatched_sidecar_drops_backend(self, tmp_path):
        # mouse index claims it was built with a different model -> guard drops mouse
        r = _router(tmp_path, mouse_sidecar_model="SOME-OTHER-MODEL")
        assert r.is_initialized is True  # human still up
        assert r.available_species() == ["human"]
        assert r.total_vectors == 3

    def test_all_backends_fail_reports_uninitialized(self, tmp_path):
        backends = {
            "human": _make_backend(tmp_path, "human", ["H1"], "human-model",
                                   sidecar_model="WRONG"),
        }
        r = VectorRouter(backends, ORGANISM_MAP, "human")
        r.initialize()
        assert r.is_initialized is False
        assert r.available_species() == []


class TestResolveSpecies:
    def test_no_organism_searches_every_species(self, tmp_path):
        """No organism means all of them — that is what the page's "All" sends.

        Reading it as the default species would make All search human only,
        leaving a mouse-only answer unreachable from the option that promises
        everything.
        """
        r = _router(tmp_path)
        assert r._resolve_species(None) == ["human", "mouse"]
        assert r._resolve_species([]) == ["human", "mouse"]

    def test_human_organism_routes_to_human(self, tmp_path):
        r = _router(tmp_path)
        assert r._resolve_species(["Homo sapiens"]) == ["human"]

    def test_mouse_organism_routes_to_mouse(self, tmp_path):
        r = _router(tmp_path)
        assert r._resolve_species(["Mus musculus"]) == ["mouse"]

    def test_both_organisms_route_to_both(self, tmp_path):
        r = _router(tmp_path)
        assert sorted(r._resolve_species(["Homo sapiens", "Mus musculus"])) == [
            "human",
            "mouse",
        ]

    def test_unknown_organism_narrows_to_nothing(self, tmp_path):
        """An organism this corpus does not hold returns no species, not all of them.

        The caller asked for something specific. Widening back to every species
        would answer a question nobody put, and with samples of the wrong
        organism; an empty result says the corpus has none.
        """
        r = _router(tmp_path)
        assert r._resolve_species(["Danio rerio"]) == []

    def test_unavailable_species_dropped_from_resolution(self, tmp_path):
        # mouse backend dropped -> selecting mouse resolves to nothing
        r = _router(tmp_path, mouse_sidecar_model="WRONG")
        assert r._resolve_species(["Mus musculus"]) == []


class TestSearchByTextRouting:
    def test_empty_query_returns_empty_without_model(self, tmp_path):
        r = _router(tmp_path)
        assert r.search_by_text("", organisms=["Homo sapiens"]) == []

    def test_unavailable_species_returns_empty_without_model(self, tmp_path):
        # mouse dropped; a mouse-only query must not touch any model, just return []
        r = _router(tmp_path, mouse_sidecar_model="WRONG")
        assert r.search_by_text("some query", organisms=["Mus musculus"]) == []

    def test_multiple_species_merge_is_sorted_by_score(self, tmp_path):
        """The combined pool is ordered by raw score, descending.

        So the similarity bar reads as sorted, which is what a "Similarity" order
        promises. The trade, asserted here, is that a species whose model scores
        on a lower scale is crowded down or out: human (SapBERT) and mouse
        (MiniLM) cosines are not on one scale. A per-rank interleave would keep
        both represented; sorting by score does not.
        """
        r = _router(tmp_path)

        class Backend:
            def __init__(self, hits):
                self._hits = hits

            def search_by_text(self, query, top_k=10):
                return self._hits[:top_k]

        # Mouse scores well below human across the board, so a score sort keeps
        # only its single best within the top_k.
        r._backends = {
            "human": Backend([VectorSearchResult(f"H{i}", 0.9 - i / 100) for i in range(5)]),
            "mouse": Backend([VectorSearchResult(f"M{i}", 0.2 - i / 100) for i in range(5)]),
        }
        got = r.search_by_text("q", top_k=6, organisms=["Homo sapiens", "Mus musculus"])
        assert [h.run_id for h in got] == ["H0", "H1", "H2", "H3", "H4", "M0"]
        scores = [h.score for h in got]
        assert scores == sorted(scores, reverse=True)

    def test_stronger_match_comes_first_across_species(self, tmp_path):
        r = _router(tmp_path)

        class Backend:
            def __init__(self, hits):
                self._hits = hits

            def search_by_text(self, query, top_k=10):
                return self._hits[:top_k]

        r._backends = {
            "human": Backend([VectorSearchResult("H0", 0.4)]),
            "mouse": Backend([VectorSearchResult("M0", 0.8)]),
        }
        got = r.search_by_text("q", top_k=2, organisms=["Homo sapiens", "Mus musculus"])
        assert [h.run_id for h in got] == ["M0", "H0"]


class TestSearchSimilarByIdRouting:
    def test_routes_to_backend_holding_run_id(self, tmp_path):
        r = _router(tmp_path)
        human_hits = r.search_similar_by_id("H1", top_k=10)
        assert human_hits  # non-empty
        assert all(res.run_id in {"H2", "H3"} for res in human_hits)

        mouse_hits = r.search_similar_by_id("M1", top_k=10)
        assert all(res.run_id in {"M2"} for res in mouse_hits)

    def test_unknown_run_id_returns_empty(self, tmp_path):
        r = _router(tmp_path)
        assert r.search_similar_by_id("NOPE", top_k=5) == []


class TestLifecycle:
    def test_close_releases_backends(self, tmp_path):
        r = _router(tmp_path)
        r.close()
        assert r.is_initialized is False
        assert r.total_vectors == 0
