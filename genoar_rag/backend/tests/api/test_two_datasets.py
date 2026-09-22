"""A deployment serving two bodies of data at once.

Everything else in this suite runs against a service with one, which is the
ordinary case and the one a deployment gets by configuring nothing. This file
is the other one: two databases, each with its own rows and its own index, and
the questions that only have an answer when there is more than one — which
dataset a request that names none is about, what happens when it names one that
is not here, and whether a page's numbers can come from the wrong body of data.

Two databases rather than one with a column saying which rows belong to which.
That is the design, and it is what makes most of these questions cheap: a query
cannot reach the other dataset because it is not connected to it.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db.connection import SQLiteConnectionPool
from app.services.vector_service import VectorSearchResult

MAIN_RUNS = ["SRR001", "SRR002", "SRR003"]
ATLAS_RUNS = ["SRR009", "SRR010"]


class StubIndex:
    """One dataset's vector search, over that dataset's runs and no others.

    Which is the point being made: an index is built over one body of data, so
    there is nothing for the service to filter out afterwards.
    """

    def __init__(self, run_ids):
        self._runs = list(run_ids)

    @property
    def is_initialized(self) -> bool:
        return True

    @property
    def total_vectors(self) -> int:
        return len(self._runs)

    def available_species(self):
        return ["human"]

    def run_ids_by_species(self):
        return {"human": list(self._runs)}

    def search_similar_by_id(self, run_id: str, top_k: int = 10):
        if run_id not in self._runs:
            return []
        others = [r for r in self._runs if r != run_id][:top_k]
        return [
            VectorSearchResult(run_id=r, score=0.9 - i / 100)
            for i, r in enumerate(others)
        ]


def _db(path, runs):
    """A database holding only these runs, seeded like the shared fixture."""
    from tests.conftest import _seed_db

    conn = sqlite3.connect(str(path))
    _seed_db(conn)
    placeholders = ", ".join("?" for _ in runs)
    for table in ("sra_extended", "sra_series", "sra_core"):
        conn.execute(f"DELETE FROM {table} WHERE Run NOT IN ({placeholders})", runs)
    conn.commit()
    conn.close()
    pool = SQLiteConnectionPool(str(path), pool_size=2)
    pool.initialize()
    return pool


@pytest.fixture
def two(tmp_path):
    from tests.api.conftest import _create_test_app

    pools = {
        "main": _db(tmp_path / "main.db", MAIN_RUNS),
        "atlas": _db(tmp_path / "atlas.db", ATLAS_RUNS),
    }
    app = _create_test_app(
        pools,
        vectors={"main": StubIndex(MAIN_RUNS), "atlas": StubIndex(ATLAS_RUNS)},
        labels={"main": "Curated tables", "atlas": "Paper Table 1 dataset"},
    )
    with TestClient(app) as client:
        yield client
    for pool in pools.values():
        pool.close_all()


class TestWhichDatasetAnswers:
    def test_naming_none_means_the_first(self, two):
        """So a deployment that adds a second does not change its addresses."""
        assert two.get("/api/v1/stats").json()["total_samples"] == len(MAIN_RUNS)

    def test_naming_one_means_that_one(self, two):
        assert (
            two.get("/api/v1/stats?dataset=atlas").json()["total_samples"]
            == len(ATLAS_RUNS)
        )

    def test_naming_one_that_is_not_here_is_refused(self, two):
        """Rather than fallen back from.

        A request that names a dataset means it, and answering from another
        would be a wrong answer wearing the right shape.
        """
        resp = two.get("/api/v1/stats?dataset=nope")
        assert resp.status_code == 404
        assert "main, atlas" in resp.json()["detail"]


class TestOneDatasetCannotSeeTheOther:
    def test_a_sample_is_only_where_it_lives(self, two):
        assert two.get("/api/v1/samples/SRR001?dataset=main").status_code == 200
        assert two.get("/api/v1/samples/SRR001?dataset=atlas").status_code == 404

    def test_a_search_returns_only_its_own(self, two):
        got = two.get("/api/v1/search?dataset=atlas&limit=100").json()
        assert sorted(s["run_id"] for s in got["items"]) == ATLAS_RUNS

    def test_an_export_carries_only_its_own(self, two):
        body = two.get("/api/v1/export?dataset=atlas&format=json").json()
        assert sorted(r["run_id"] for r in body["items"]) == ATLAS_RUNS

    def test_similar_samples_come_from_the_same_dataset(self, two):
        """No filtering does this — the index only holds this dataset's runs."""
        items = two.get("/api/v1/samples/SRR009/similar?dataset=atlas").json()["items"]
        assert [i["run_id"] for i in items] == ["SRR010"]

    def test_filters_offer_only_their_own_values(self, two):
        main = two.get("/api/v1/filters/tissue?limit=100").json()
        atlas = two.get("/api/v1/filters/tissue?dataset=atlas&limit=100").json()
        main_values = {v["value"].lower() for v in main["values"]}
        atlas_values = {v["value"].lower() for v in atlas["values"]}
        assert main_values and atlas_values
        assert main_values != atlas_values


class TestTheCachesAreNotShared:
    def test_stats_asked_of_one_are_not_served_to_the_other(self, two):
        """Both services memoise, and each had one slot before datasets."""
        first = two.get("/api/v1/stats").json()["total_samples"]
        second = two.get("/api/v1/stats?dataset=atlas").json()["total_samples"]
        again = two.get("/api/v1/stats").json()["total_samples"]
        assert (first, second, again) == (len(MAIN_RUNS), len(ATLAS_RUNS), len(MAIN_RUNS))

    def test_nor_are_filter_values(self, two):
        two.get("/api/v1/filters")
        atlas = two.get("/api/v1/filters?dataset=atlas").json()
        tissue = next(c for c in atlas["categories"] if c["name"] == "tissue")
        assert tissue["values"]


class TestFilesStayInTheirDataset:
    """The download trail may not cross datasets at its last step.

    Detail and manifest already answer from the chosen dataset; the file URLs
    they hand out, and the file route those URLs reach, must stay in it too —
    the results directory on disk is global, so without the check any run's
    files are servable under any dataset that resolves the accession.
    """

    @pytest.fixture
    def results_dir(self, tmp_path, monkeypatch):
        from app.config import settings

        for run in ("SRR001", "SRR009"):
            outs = tmp_path / "results" / run / "cellranger_output" / "outs"
            outs.mkdir(parents=True)
            (outs / "filtered_feature_bc_matrix.h5").write_bytes(b"h5")
        monkeypatch.setattr(
            settings, "processed_results_dir", str(tmp_path / "results")
        )
        return tmp_path

    def test_manifest_urls_carry_the_dataset(self, two, results_dir):
        body = two.get("/api/v1/samples/SRR009/download?dataset=atlas").json()
        urls = [s["url"] for s in body["sources"] if not s["kind"].endswith("_record")]
        assert urls == [
            "/api/v1/samples/SRR009/files/filtered_feature_bc_matrix.h5"
            "?dataset=atlas"
        ]

    def test_the_file_route_refuses_a_run_the_dataset_does_not_hold(
        self, two, results_dir
    ):
        path = "/api/v1/samples/SRR009/files/filtered_feature_bc_matrix.h5"
        assert two.get(f"{path}?dataset=atlas").status_code == 200
        # The file exists on disk; the run is simply not main's to serve.
        assert two.get(f"{path}?dataset=main").status_code == 404

    def test_a_single_dataset_deployment_keeps_its_urls(
        self, client, tmp_path, monkeypatch
    ):
        from app.config import settings

        outs = tmp_path / "SRR001" / "cellranger_output" / "outs"
        outs.mkdir(parents=True)
        (outs / "filtered_feature_bc_matrix.h5").write_bytes(b"h5")
        monkeypatch.setattr(settings, "processed_results_dir", str(tmp_path))

        body = client.get("/api/v1/samples/SRR001/download").json()
        urls = [s["url"] for s in body["sources"] if not s["kind"].endswith("_record")]
        assert urls == [
            "/api/v1/samples/SRR001/files/filtered_feature_bc_matrix.h5"
        ]


class TestWhatTheClientIsTold:
    def test_the_datasets_are_listed_with_their_labels(self, two):
        body = two.get("/api/v1/datasets").json()
        assert [d["name"] for d in body["items"]] == ["main", "atlas"]
        assert body["items"][1]["label"] == "Paper Table 1 dataset"
        assert body["default"] == "main"

    def test_and_that_this_deployment_serves_more_than_one(self, two):
        """What a page uses to decide whether naming its corpus says anything."""
        assert two.get("/api/v1/datasets").json()["is_split"] is True

    def test_a_deployment_with_one_says_so(self, client):
        """The ordinary case, from the shared single-dataset fixture."""
        body = client.get("/api/v1/datasets").json()
        assert body["is_split"] is False
        assert len(body["items"]) == 1
