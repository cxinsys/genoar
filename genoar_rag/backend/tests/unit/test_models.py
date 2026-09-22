"""Unit tests for Pydantic models."""

import pytest
from pydantic import ValidationError

from app.models.common import PaginatedResponse, PaginationParams, SortOrder, SortParams
from app.models.filters import FilterCategory, FilterOptionsResponse, FilterValue
from app.models.sample import ExtendedField, SampleDetail, SampleSummary
from app.models.search import SearchFilters, SearchResponse
from app.models.stats import DashboardStats, DistributionItem


class TestPaginationParams:
    def test_defaults(self):
        p = PaginationParams()
        assert p.offset == 0
        assert p.limit == 20

    def test_custom_values(self):
        p = PaginationParams(offset=40, limit=50)
        assert p.offset == 40
        assert p.limit == 50

    def test_negative_offset_rejected(self):
        with pytest.raises(ValidationError):
            PaginationParams(offset=-1)

    def test_zero_limit_rejected(self):
        with pytest.raises(ValidationError):
            PaginationParams(limit=0)

    def test_limit_exceeds_max(self):
        with pytest.raises(ValidationError):
            PaginationParams(limit=101)

    def test_limit_boundary_100(self):
        p = PaginationParams(limit=100)
        assert p.limit == 100


class TestSortParams:
    def test_defaults(self):
        s = SortParams()
        assert s.sort_by == "run_id"
        assert s.sort_order == SortOrder.asc

    def test_desc_order(self):
        s = SortParams(sort_order=SortOrder.desc)
        assert s.sort_order == SortOrder.desc

    def test_invalid_order(self):
        with pytest.raises(ValidationError):
            SortParams(sort_order="invalid")


class TestPaginatedResponse:
    def test_basic(self):
        resp = PaginatedResponse[str](items=["a", "b"], total=10, offset=0, limit=20)
        assert len(resp.items) == 2
        assert resp.total == 10

    def test_negative_total_rejected(self):
        with pytest.raises(ValidationError):
            PaginatedResponse[str](items=[], total=-1, offset=0, limit=1)


class TestSampleSummary:
    def test_minimal(self):
        s = SampleSummary(run_id="SRR123")
        assert s.run_id == "SRR123"
        assert s.tissue is None
        assert s.organism is None

    def test_full(self):
        s = SampleSummary(
            run_id="SRR123",
            series="GSE123",
            tissue="bone marrow",
            organism="Homo sapiens",
            assay_type="RNA-Seq",
        )
        assert s.series == "GSE123"
        assert s.assay_type == "RNA-Seq"


class TestSampleDetail:
    def test_inherits_summary(self):
        d = SampleDetail(run_id="SRR123", treatment="Untreated")
        assert d.run_id == "SRR123"
        assert d.treatment == "Untreated"
        assert d.extended_fields == []

    def test_with_extended_fields(self):
        d = SampleDetail(
            run_id="SRR123",
            extended_fields=[
                ExtendedField(field_name="Organism", field_value="Homo sapiens"),
                ExtendedField(field_name="FMT", field_value="FASTQ"),
            ],
        )
        assert len(d.extended_fields) == 2
        assert d.extended_fields[0].field_name == "Organism"


class TestExtendedField:
    def test_minimal(self):
        f = ExtendedField(field_name="Organism")
        assert f.field_value is None
        assert f.data_type is None

    def test_full(self):
        f = ExtendedField(field_name="Organism", field_value="Homo sapiens", data_type="text")
        assert f.field_value == "Homo sapiens"


class TestSearchFilters:
    def test_defaults(self):
        f = SearchFilters()
        assert f.organism == []
        assert f.keyword is None
        assert f.offset == 0
        assert f.limit == 20

    def test_has_filters_empty(self):
        f = SearchFilters()
        assert f.has_filters() is False

    def test_has_filters_with_organism(self):
        f = SearchFilters(organism=["Homo sapiens"])
        assert f.has_filters() is True

    def test_has_filters_with_keyword(self):
        f = SearchFilters(keyword="bone marrow")
        assert f.has_filters() is True

    def test_to_pagination(self):
        f = SearchFilters(offset=20, limit=50)
        p = f.to_pagination()
        assert p.offset == 20
        assert p.limit == 50

    def test_to_sort(self):
        f = SearchFilters(sort_by="release_date", sort_order=SortOrder.desc)
        s = f.to_sort()
        assert s.sort_by == "release_date"
        assert s.sort_order == SortOrder.desc

    def test_limit_boundary(self):
        with pytest.raises(ValidationError):
            SearchFilters(limit=101)


class TestSearchResponse:
    def test_basic(self):
        resp = SearchResponse(
            items=[SampleSummary(run_id="SRR1")],
            total=1,
            offset=0,
            limit=20,
            filters_applied=SearchFilters(),
        )
        assert len(resp.items) == 1
        assert resp.total == 1


class TestFilterModels:
    def test_filter_value(self):
        fv = FilterValue(value="Homo sapiens", count=170661)
        assert fv.value == "Homo sapiens"
        assert fv.count == 170661

    def test_filter_value_negative_count(self):
        with pytest.raises(ValidationError):
            FilterValue(value="test", count=-1)

    def test_filter_category(self):
        fc = FilterCategory(
            name="organism",
            values=[FilterValue(value="Homo sapiens", count=100)],
            total_distinct=1,
        )
        assert fc.name == "organism"
        assert len(fc.values) == 1

    def test_filter_options_response(self):
        resp = FilterOptionsResponse(categories=[])
        assert resp.categories == []


class TestStatsModels:
    def test_distribution_item(self):
        di = DistributionItem(label="RNA-Seq", count=162445, percentage=82.1)
        assert di.label == "RNA-Seq"

    def test_distribution_percentage_bounds(self):
        with pytest.raises(ValidationError):
            DistributionItem(label="x", count=0, percentage=101.0)
        with pytest.raises(ValidationError):
            DistributionItem(label="x", count=0, percentage=-1.0)

    def test_dashboard_stats(self):
        stats = DashboardStats(
            total_samples=197757,
            total_series=5000,
            organism_distribution=[],
            assay_distribution=[],
            platform_distribution=[],
            library_source_distribution=[],
            metadata_completeness=[],
        )
        assert stats.total_samples == 197757
