"""Unit tests for StatsService."""

from app.services.stats_service import StatsService


class TestStatsService:
    def setup_method(self):
        StatsService.clear_cache()

    def test_dashboard_stats(self, db_conn):
        svc = StatsService(db_conn)
        stats = svc.get_dashboard_stats()
        assert stats.total_samples == 10
        assert stats.total_series == 6  # SRR001 is in two studies; see conftest
        assert len(stats.organism_distribution) > 0
        assert len(stats.assay_distribution) > 0
        assert len(stats.platform_distribution) > 0

    def test_percentages_sum(self, db_conn):
        svc = StatsService(db_conn)
        stats = svc.get_dashboard_stats()
        total_pct = sum(d.percentage for d in stats.platform_distribution)
        assert 99.0 <= total_pct <= 101.0  # rounding tolerance

    def test_completeness_partitions_each_species(self, db_conn):
        """The rungs sum to the species, which is what lets them be drawn as
        shares of it. A run matching two arms, or none, still produces a chart —
        one whose slices claim a whole they do not add up to."""
        svc = StatsService(db_conn)
        stats = svc.get_dashboard_stats()

        assert [c.species for c in stats.metadata_completeness] == ["human", "mouse"]
        for species in stats.metadata_completeness:
            assert sum(t.count for t in species.tiers) == species.total
            assert 99.0 <= sum(t.percentage for t in species.tiers) <= 101.0

    def test_completeness_reads_disease_from_any_of_its_field_names(self, db_conn):
        """Disease was submitted under nine different names, and the fixture
        spreads five of them over five runs. Counting only `disease_state` would
        put three of those runs a rung lower than they belong."""
        svc = StatsService(db_conn)
        stats = svc.get_dashboard_stats()
        human = next(c for c in stats.metadata_completeness if c.species == "human")
        counts = {t.key: t.count for t in human.tiers}

        assert human.total == 8
        assert counts["tissue_disease_cell_type"] == 4  # SRR001, 003, 005, 007
        assert counts["tissue_disease"] == 1  # SRR009, which has no cell type
        assert counts["tissue_cell_type"] == 2  # SRR002, SRR010
        assert counts["tissue_only"] == 1  # SRR004
        assert counts["no_tissue"] == 0

    def test_caching(self, db_conn):
        svc = StatsService(db_conn)
        stats1 = svc.get_dashboard_stats()
        stats2 = svc.get_dashboard_stats()
        assert stats1 is stats2


class TestCorpusLevels:
    """The corpus counted at each level it exists at, and the levels reconciled.

    A study with runs of two species is one study and is counted under each
    species. The fixture has two such studies (GSE003 and GSE004, each with a
    human and a mouse run), so the species figures sum to the whole plus two,
    and the service says so rather than leaving the reader to find a mistake.
    """

    def setup_method(self):
        StatsService.clear_cache()

    def test_species_runs_partition_the_corpus(self, db_conn):
        stats = StatsService(db_conn).get_dashboard_stats()
        by_species = {c.species: c for c in stats.corpus}
        assert by_species["human"].runs == 8
        assert by_species["mouse"].runs == 2
        assert sum(c.runs for c in stats.corpus) == stats.total_samples

    def test_shared_studies_reconcile_the_series_figures(self, db_conn):
        stats = StatsService(db_conn).get_dashboard_stats()
        assert stats.shared_series == 2
        assert (
            sum(c.series for c in stats.corpus)
            == stats.total_series + stats.shared_series
        )

    def test_runs_without_a_gsm_are_not_samples(self, db_conn):
        """The fixture names no GEO sample for any run, so there are none to
        count — and not ten, which is what counting rows would say."""
        stats = StatsService(db_conn).get_dashboard_stats()
        assert stats.total_geo_samples == 0
        assert all(c.geo_samples == 0 for c in stats.corpus)

    def test_no_census_means_no_cell_figure(self, db_conn, monkeypatch):
        """None, not zero: a zero is a claim about the corpus."""
        from app.services import stats_service

        monkeypatch.setattr(stats_service.settings, "cell_counts_path", "")
        monkeypatch.setattr(stats_service.settings, "processed_results_dir", "")
        stats = StatsService(db_conn).get_dashboard_stats()
        assert stats.total_cells is None
        assert all(c.cells is None for c in stats.corpus)

    def test_census_is_folded_into_species_totals(self, db_conn, monkeypatch, tmp_path):
        """A census names runs; the service sorts them by species through the
        organism field, and a run the census does not name is simply absent."""
        from app.services import stats_service

        census = tmp_path / "cell_counts.json"
        census.write_text('{"SRR001": 100, "SRR006": 30, "SRR008": 20}')
        monkeypatch.setattr(stats_service.settings, "cell_counts_path", str(census))
        stats = StatsService(db_conn).get_dashboard_stats()
        by_species = {c.species: c for c in stats.corpus}
        assert stats.total_cells == 150
        assert by_species["human"].cells == 100
        assert by_species["mouse"].cells == 50

    def test_census_runs_outside_the_corpus_are_not_counted(
        self, db_conn, monkeypatch, tmp_path
    ):
        """The processed directory can hold runs the database does not — on
        the deployment, 74 mouse runs. The total is summed over the same runs
        as the species figures, so the whole still equals its parts."""
        from app.services import stats_service

        census = tmp_path / "cell_counts.json"
        census.write_text('{"SRR001": 100, "SRR006": 30, "SRR999999": 5000}')
        monkeypatch.setattr(stats_service.settings, "cell_counts_path", str(census))
        stats = StatsService(db_conn).get_dashboard_stats()
        assert stats.total_cells == 130
        assert sum(c.cells or 0 for c in stats.corpus) == stats.total_cells

    def test_unreadable_census_is_ignored_not_fatal(self, db_conn, monkeypatch, tmp_path):
        from app.services import stats_service

        census = tmp_path / "cell_counts.json"
        census.write_text("not json")
        monkeypatch.setattr(stats_service.settings, "cell_counts_path", str(census))
        stats = StatsService(db_conn).get_dashboard_stats()
        assert stats.total_cells is None
