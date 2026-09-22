"""Integration tests for SampleRepository."""

from app.repositories.sample_repository import SampleRepository


class TestSampleRepository:
    def test_get_by_run_id(self, db_conn):
        repo = SampleRepository(db_conn)
        row = repo.get_by_run_id("SRR001")
        assert row is not None
        assert row["Run"] == "SRR001"
        assert row["tissue"] == "Bone Marrow"

    def test_get_by_run_id_not_found(self, db_conn):
        repo = SampleRepository(db_conn)
        row = repo.get_by_run_id("NONEXISTENT")
        assert row is None

    def test_get_by_series(self, db_conn):
        repo = SampleRepository(db_conn)
        rows = repo.get_by_series("GSE001")
        assert len(rows) == 2
        run_ids = {r["Run"] for r in rows}
        assert run_ids == {"SRR001", "SRR002"}

    def test_count_by_series(self, db_conn):
        repo = SampleRepository(db_conn)
        assert repo.count_by_series("GSE001") == 2
        assert repo.count_by_series("NONEXISTENT") == 0

    def test_list_samples_pagination(self, db_conn):
        repo = SampleRepository(db_conn)
        page1 = repo.list_samples(limit=3, offset=0)
        page2 = repo.list_samples(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        # No overlap
        ids1 = {r["Run"] for r in page1}
        ids2 = {r["Run"] for r in page2}
        assert ids1.isdisjoint(ids2)

    def test_count_all(self, db_conn):
        repo = SampleRepository(db_conn)
        assert repo.count_all() == 10

    def test_count_series(self, db_conn):
        """Six, not five: SRR001 is in a second study that no core row names.

        The fixture mirrors the real shape — a run belonging to two GSEs, where
        `sra_core.Series` can only record one. Counting that column reported 760
        of the curation's 888 studies.
        """
        repo = SampleRepository(db_conn)
        assert repo.count_series() == 6

    def test_get_by_series_finds_a_shared_run(self, db_conn):
        """The second study of a shared run returns that run rather than nothing."""
        repo = SampleRepository(db_conn)
        rows = repo.get_by_series("GSE999999")
        assert [r["Run"] for r in rows] == ["SRR001"]
        assert repo.count_by_series("GSE999999") == 1

    def test_series_for_run_lists_every_study(self, db_conn):
        repo = SampleRepository(db_conn)
        assert "GSE999999" in repo.series_for_run("SRR001")
        assert len(repo.series_for_run("SRR001")) == 2

    def test_siblings_span_all_of_a_runs_studies(self, db_conn):
        """SRR001 is in GSE001 with SRR002, and alone in GSE999999.

        Reading one series off the core row shows, for a run in two studies,
        the members of whichever one happens to be stored.
        """
        repo = SampleRepository(db_conn)
        runs = [r["Run"] for r in repo.get_siblings_of_run("SRR001")]
        assert runs == ["SRR001", "SRR002"]
        assert repo.count_siblings_of_run("SRR001") == 2

    def test_series_for_runs_answers_a_page_in_one_query(self, db_conn):
        """The batch form, used to fill a page of result cards.

        Reading `sra_core.Series` alone makes filtering by one study list
        samples labelled with another.
        """
        repo = SampleRepository(db_conn)
        got = repo.series_for_runs(["SRR001", "SRR002", "SRR003"])
        assert got["SRR001"] == ["GSE001", "GSE999999"]
        assert got["SRR002"] == ["GSE001"]
        assert got["SRR003"] == ["GSE002"]

    def test_series_for_runs_handles_an_empty_request(self, db_conn):
        assert SampleRepository(db_conn).series_for_runs([]) == {}

    def test_siblings_do_not_double_count_a_shared_run(self, db_conn):
        """A run reachable through two of the studies is listed once."""
        repo = SampleRepository(db_conn)
        runs = [r["Run"] for r in repo.get_siblings_of_run("SRR002")]
        assert runs == sorted(set(runs))

    def test_platform_distribution(self, db_conn):
        repo = SampleRepository(db_conn)
        dist = repo.platform_distribution()
        assert len(dist) == 2  # ILLUMINA and DNBSEQ
        illumina = next(d for d in dist if d["label"] == "ILLUMINA")
        assert illumina["count"] == 8
