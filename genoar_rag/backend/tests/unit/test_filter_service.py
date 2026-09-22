"""Unit tests for FilterService."""

from app.services.filter_service import FilterService


class TestFilterService:
    def setup_method(self):
        FilterService.clear_cache()

    def test_get_filter_options(self, db_conn):
        svc = FilterService(db_conn)
        result = svc.get_filter_options()
        names = {c.name for c in result.categories}
        assert "tissue" in names
        assert "organism" in names
        assert "assay_type" in names
        assert "library_source" in names
        assert "disease" in names
        assert "platform" in names
        assert "cell_type" in names

    def test_tissue_values(self, db_conn):
        svc = FilterService(db_conn)
        result = svc.get_filter_options()
        tissue = next(c for c in result.categories if c.name == "tissue")
        assert len(tissue.values) > 0
        # Should have distinct tissues from test data
        tissue_vals = {v.value.lower() for v in tissue.values}
        assert "bone marrow" in tissue_vals or "Bone Marrow".lower() in tissue_vals

    def test_caching(self, db_conn):
        svc = FilterService(db_conn)
        result1 = svc.get_filter_options()
        result2 = svc.get_filter_options()
        # Same object from cache
        assert result1 is result2
