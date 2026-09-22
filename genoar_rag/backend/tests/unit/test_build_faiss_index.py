"""Unit tests for scripts/build_faiss_index.py (no model download).

Covers document loading from sra_vectors (with per-organism carve-out and original
vector_index order), the fake encoder, and the write_index -> sidecar -> VectorService
round trip including the build-model guard.
"""

import importlib.util
import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from app.services.vector_service import VectorService

_BUILD_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_faiss_index.py"
_spec = importlib.util.spec_from_file_location("build_faiss_index", _BUILD_PATH)
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE sra_vectors (
            id INTEGER PRIMARY KEY, Run TEXT, embedding_text TEXT, vector_index INTEGER
        );
        CREATE TABLE sra_extended (
            id INTEGER PRIMARY KEY, Run TEXT, field_name TEXT, field_value TEXT
        );
        """
    )
    # vector_index deliberately interleaves species to test ordering
    conn.executemany(
        "INSERT INTO sra_vectors (Run, embedding_text, vector_index) VALUES (?, ?, ?)",
        [
            ("H1", "human one", 0),
            ("M1", "mouse one", 1),
            ("H2", "human two", 2),
            ("M2", "mouse two", 3),
            ("H3", "human three", 4),
        ],
    )
    conn.executemany(
        "INSERT INTO sra_extended (Run, field_name, field_value) VALUES (?, 'Organism', ?)",
        [
            ("H1", "Homo sapiens"),
            ("H2", "Homo sapiens"),
            ("H3", "Homo sapiens"),
            ("M1", "Mus musculus"),
            ("M2", "Mus musculus"),
        ],
    )
    conn.commit()
    return conn


class TestLoadDocuments:
    def test_all_documents_in_vector_index_order(self, db):
        run_ids, texts = build.load_documents(db)
        assert run_ids == ["H1", "M1", "H2", "M2", "H3"]
        assert texts[0] == "human one"

    def test_organism_filter_carves_out_species(self, db):
        run_ids, texts = build.load_documents(db, organisms=["Homo sapiens"])
        assert run_ids == ["H1", "H2", "H3"]  # mouse excluded, order preserved
        assert texts == ["human one", "human two", "human three"]

    def test_mouse_filter(self, db):
        run_ids, _ = build.load_documents(db, organisms=["Mus musculus"])
        assert run_ids == ["M1", "M2"]

    def test_limit(self, db):
        run_ids, _ = build.load_documents(db, limit=2)
        assert run_ids == ["H1", "M1"]


class TestFakeEncode:
    def test_shape_and_normalization(self):
        vecs = build.fake_encode(["a", "b", "c"], dim=16)
        assert vecs.shape == (3, 16)
        assert vecs.dtype == np.float32
        norms = np.linalg.norm(vecs, axis=1)
        assert np.allclose(norms, 1.0, atol=1e-5)

    def test_deterministic(self):
        a = build.fake_encode(["same text"], dim=8)
        b = build.fake_encode(["same text"], dim=8)
        assert np.array_equal(a, b)


class TestWriteIndexRoundTrip:
    def test_writes_three_files_with_sidecar(self, tmp_path):
        run_ids = ["H1", "H2", "H3"]
        vecs = build.fake_encode(["t1", "t2", "t3"], dim=8)
        out_index = str(tmp_path / "idx.bin")
        out_mapping = str(tmp_path / "map.json")

        build.write_index(vecs, run_ids, out_index, out_mapping, "my-model", ["Homo sapiens"])

        assert Path(out_index).exists()
        assert json.load(open(out_mapping)) == run_ids
        meta = json.load(open(out_index + ".meta.json"))
        assert meta["model_name"] == "my-model"
        assert meta["dim"] == 8
        assert meta["count"] == 3
        assert meta["organisms"] == ["Homo sapiens"]

    def test_vector_service_loads_matching_model(self, tmp_path):
        run_ids = ["H1", "H2", "H3"]
        vecs = build.fake_encode(["t1", "t2", "t3"], dim=8)
        out_index = str(tmp_path / "idx.bin")
        out_mapping = str(tmp_path / "map.json")
        build.write_index(vecs, run_ids, out_index, out_mapping, "my-model", None)

        svc = VectorService(out_index, out_mapping, "my-model", require_model_match=True)
        svc.initialize()
        assert svc.total_vectors == 3
        assert svc.contains("H1")

    def test_vector_service_rejects_model_mismatch(self, tmp_path):
        run_ids = ["H1", "H2"]
        vecs = build.fake_encode(["t1", "t2"], dim=8)
        out_index = str(tmp_path / "idx.bin")
        out_mapping = str(tmp_path / "map.json")
        build.write_index(vecs, run_ids, out_index, out_mapping, "built-model", None)

        svc = VectorService(out_index, out_mapping, "different-model", require_model_match=True)
        with pytest.raises(ValueError, match="built with"):
            svc.initialize()

    def test_count_mismatch_refuses_to_write(self, tmp_path):
        vecs = build.fake_encode(["t1", "t2", "t3"], dim=8)
        with pytest.raises(ValueError, match="refusing to write"):
            build.write_index(
                vecs, ["only", "two"], str(tmp_path / "i.bin"),
                str(tmp_path / "m.json"), "m", None
            )
