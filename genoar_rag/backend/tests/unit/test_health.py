"""The health payload tells clients which species semantic search actually works for.

The UI uses this to decide which species to offer, so "configured" is not enough:
a species whose index failed to load must not appear.
"""

import json
from pathlib import Path

import faiss
import numpy as np

from app.config import DEFAULT_DATASET, DatasetSpec, settings
from app.dependencies import set_registry
from app.services.datasets import DatasetRegistry


def _install(router):
    """Make `router` the default dataset's vector search."""
    registry = DatasetRegistry(
        [DatasetSpec(DEFAULT_DATASET, "", "", "", {})]
    )
    registry.initialize(
        settings,
        pool_factory=lambda _s, _spec: None,
        vector_factory=lambda _s, _spec: router,
    )
    set_registry(registry)
from app.main import health
from app.services.vector_router import VectorRouter
from app.services.vector_service import VectorService

ORGANISM_MAP = {"Homo sapiens": "human", "Mus musculus": "mouse"}


def _backend(tmp_path: Path, name: str, model: str, dim: int = 8) -> VectorService:
    run_ids = [f"{name.upper()}{i}" for i in range(3)]
    rng = np.random.RandomState(len(name))
    vecs = rng.randn(len(run_ids), dim).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)

    index_path = str(tmp_path / f"{name}.bin")
    index = faiss.IndexFlatIP(dim)
    index.add(vecs)
    faiss.write_index(index, index_path)

    mapping_path = str(tmp_path / f"{name}_map.json")
    Path(mapping_path).write_text(json.dumps(run_ids))
    Path(index_path + ".meta.json").write_text(
        json.dumps({"model_name": model, "dim": dim, "count": len(run_ids)})
    )
    return VectorService(index_path, mapping_path, model, require_model_match=True)


class TestHealth:
    def test_reports_no_species_when_semantic_search_is_off(self):
        _install(None)
        payload = health()
        assert payload["status"] == "ok"
        assert payload["semantic_search"] is False
        assert payload["semantic_species"] == []

    def test_lists_every_loaded_species(self, tmp_path):
        router = VectorRouter(
            {
                "human": _backend(tmp_path, "human", "sapbert"),
                "mouse": _backend(tmp_path, "mouse", "minilm"),
            },
            ORGANISM_MAP,
            "human",
        )
        router.initialize()
        _install(router)
        try:
            payload = health()
            assert payload["semantic_search"] is True
            assert payload["semantic_species"] == ["human", "mouse"]
        finally:
            _install(None)

    def test_omits_a_species_whose_index_failed_to_load(self, tmp_path):
        # The mouse index file is absent, so that backend is dropped at startup
        # and must not be advertised.
        missing = VectorService(
            str(tmp_path / "absent.bin"), str(tmp_path / "absent.json"), "minilm"
        )
        router = VectorRouter(
            {"human": _backend(tmp_path, "human", "sapbert"), "mouse": missing},
            ORGANISM_MAP,
            "human",
        )
        router.initialize()
        _install(router)
        try:
            payload = health()
            assert payload["semantic_search"] is True
            assert payload["semantic_species"] == ["human"]
        finally:
            _install(None)
