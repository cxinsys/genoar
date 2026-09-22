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
