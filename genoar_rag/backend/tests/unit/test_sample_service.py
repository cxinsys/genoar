"""Unit tests for SampleService (using real test DB, not mocks)."""

import pytest

from app.exceptions import SampleNotFoundError
from app.services.sample_service import SampleService


class TestSampleService:
    def test_get_detail(self, db_conn):
        svc = SampleService(db_conn)
        detail = svc.get_sample_detail("SRR001")
        assert detail.run_id == "SRR001"
        assert detail.tissue == "Bone Marrow"
        assert detail.organism == "Homo sapiens"
        assert detail.genotype == "WT"
        assert len(detail.extended_fields) > 0

    def test_get_detail_not_found(self, db_conn):
        svc = SampleService(db_conn)
        with pytest.raises(SampleNotFoundError):
            svc.get_sample_detail("NONEXISTENT")

    def test_list_samples(self, db_conn):
        svc = SampleService(db_conn)
        samples, total = svc.list_samples(limit=5, offset=0)
        assert len(samples) == 5
        assert total == 10
        # Should have organism enriched
        assert any(s.organism is not None for s in samples)

    def test_get_series_samples(self, db_conn):
        svc = SampleService(db_conn)
        samples, total = svc.get_series_samples("GSE001")
        assert len(samples) == 2
        assert total == 2
