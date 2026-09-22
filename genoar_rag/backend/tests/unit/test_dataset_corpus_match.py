"""A dataset's index must describe runs its own database holds.

Index and mapping sizes are already cross-checked at load; what nothing
checked was whether those runs exist in the dataset's database. An index
built over the primary corpus loads cleanly next to a secondary database,
and every semantic hit then dies in the DB lookup — or worse, the counts
disagree quietly. The registry refuses that pairing at startup.
"""

import sqlite3
from types import SimpleNamespace

import pytest

from app.config import DatasetSpec
from app.services.datasets import DatasetRegistry


def corpus_db(tmp_path, runs):
    path = tmp_path / "corpus.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sra_core (Run TEXT UNIQUE NOT NULL)")
    conn.execute("CREATE TABLE sra_extended (Run TEXT, field_name TEXT)")
    conn.execute("CREATE TABLE sra_series (Run TEXT, Series TEXT)")
    conn.executemany("INSERT INTO sra_core VALUES (?)", [(r,) for r in runs])
    conn.commit()
    conn.close()
    return str(path)


def make_spec(db_path):
    return DatasetSpec(
        name="atlas", label="atlas", db_path=db_path, db_name="", species={}
    )


settings = SimpleNamespace(db_backend="sqlite", db_pool_size=1)


class FakeRouter:
    def __init__(self, run_ids):
        self._run_ids = run_ids

    def available_species(self):
        return ["human"]

    def run_ids_by_species(self):
        return {"human": list(self._run_ids)}

    def close(self):
        pass


def initialize(spec, index_runs):
    registry = DatasetRegistry([spec])
    registry.initialize(
        settings, vector_factory=lambda s, sp: FakeRouter(index_runs)
    )
    return registry


class TestIndexMustDescribeTheDatasetCorpus:
    def test_index_over_another_corpus_refuses_to_start(self, tmp_path):
        spec = make_spec(corpus_db(tmp_path, ["SRR1", "SRR2"]))

        with pytest.raises(ValueError, match="atlas"):
            initialize(spec, ["SRR900", "SRR901", "SRR902"])

    def test_index_over_this_corpus_starts(self, tmp_path):
        spec = make_spec(corpus_db(tmp_path, ["SRR1", "SRR2", "SRR3"]))

        registry = initialize(spec, ["SRR1", "SRR3"])
        try:
            assert registry.vectors("atlas") is not None
        finally:
            registry.close_all()

    def test_an_overlapping_but_different_corpus_is_still_refused(
        self, tmp_path
    ):
        """One shared run must not vouch for an index built elsewhere.

        The realistic wrong pairing is not a disjoint corpus but a heavily
        overlapping one — a curated database and a paper subset share most
        of their runs. Any index run the database does not hold means
        semantic hits would name rows this dataset cannot return.
        """
        spec = make_spec(corpus_db(tmp_path, ["SHARED"]))

        with pytest.raises(ValueError, match="atlas"):
            initialize(spec, ["SHARED", "WRONG1", "WRONG2"])

    def test_dataset_without_semantic_search_starts(self, tmp_path):
        spec = make_spec(corpus_db(tmp_path, ["SRR1"]))

        registry = DatasetRegistry([spec])
        registry.initialize(settings, vector_factory=lambda s, sp: None)
        try:
            assert registry.vectors("atlas") is None
        finally:
            registry.close_all()


class TestConfiguredBackendsMustLoad:
    def test_a_configured_index_that_fails_to_load_refuses_to_start(
        self, tmp_path
    ):
        """Configured semantic search is served or refused, never dropped.

        Dropping a backend that fails to load and starting without it means,
        for a mounted deployment, that a missing or broken index file boots a
        server whose configured feature is silently gone — visible nowhere but
        a log line.
        """
        from app.config import Settings

        spec = DatasetSpec(
            name="atlas",
            label="atlas",
            db_path=corpus_db(tmp_path, ["SRR1"]),
            db_name="",
            species={
                "human": {
                    "model_name": "some-model",
                    "index_path": str(tmp_path / "missing.faiss"),
                    "mapping_path": str(tmp_path / "missing.json"),
                }
            },
        )
        real_settings = Settings(
            _env_file=None, db_backend="sqlite", db_pool_size=1
        )

        registry = DatasetRegistry([spec])
        with pytest.raises(ValueError, match="atlas.*human"):
            registry.initialize(real_settings)


class TestAFailedDatasetTakesTheStartupDown:
    def test_pools_opened_before_the_failure_are_closed(self, tmp_path):
        good = DatasetSpec(
            name="main",
            label="main",
            db_path=corpus_db(tmp_path, ["SRR1"]),
            db_name="",
            species={},
        )
        bad = DatasetSpec(
            name="atlas",
            label="atlas",
            db_path=str(tmp_path / "missing.db"),
            db_name="",
            species={},
        )
        opened = []

        def pool_factory(s, spec):
            from app.db.connection import create_pool_for

            pool = create_pool_for(s, spec)
            opened.append(pool)
            return pool

        registry = DatasetRegistry([good, bad])
        with pytest.raises(FileNotFoundError):
            registry.initialize(
                settings,
                pool_factory=pool_factory,
                vector_factory=lambda s, sp: None,
            )

        assert len(opened) == 1
        assert not opened[0]._initialized
