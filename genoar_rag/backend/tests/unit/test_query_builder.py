"""Unit tests for query builder."""

from app.models.common import SortOrder
from app.models.search import SearchFilters
from app.utils.query_builder import build_search_query


class TestBuildSearchQuery:
    def test_no_filters(self):
        filters = SearchFilters()
        sql, params = build_search_query(filters)
        assert "WHERE 1=1" in sql
        assert "LIMIT ? OFFSET ?" in sql
        assert params == [20, 0]  # default limit/offset

    def test_tissue_filter(self):
        filters = SearchFilters(tissue=["bone marrow", "Liver"])
        sql, params = build_search_query(filters)
        assert "LOWER(c.tissue) IN (?, ?)" in sql
        assert params[0] == "bone marrow"
        assert params[1] == "liver"

    def test_organism_filter_uses_subquery(self):
        filters = SearchFilters(organism=["Homo sapiens"])
        sql, params = build_search_query(filters)
        assert "sra_extended" in sql
        assert "field_name = ?" in sql
        assert "Organism" in params

    def test_multiple_filters_combined(self):
        filters = SearchFilters(
            tissue=["blood"],
            organism=["Homo sapiens"],
        )
        sql, params = build_search_query(filters)
        assert sql.count("AND") >= 2  # tissue AND organism AND (limit/offset in params)

    def test_keyword_search(self):
        filters = SearchFilters(keyword="bone")
        sql, params = build_search_query(filters)
        assert "LIKE LOWER(?)" in sql
        assert "ESCAPE" in sql
        # 4 keyword fields searched
        assert params.count("%bone%") == 4

    def test_keyword_special_chars_escaped(self):
        filters = SearchFilters(keyword="50%")
        sql, params = build_search_query(filters)
        assert "%50\\%%" in params

    def test_disease_filter(self):
        filters = SearchFilters(disease=["cancer"])
        sql, params = build_search_query(filters)
        # One field name, not a union of ten: the loader has already chosen
        # each run's disease.
        assert "field_name = ?" in sql
        assert "disease_resolved" in params
        assert "cancer" in params

    def test_count_only(self):
        filters = SearchFilters(tissue=["blood"])
        sql, params = build_search_query(filters, count_only=True)
        assert sql.startswith("SELECT COUNT(*)")
        assert "LIMIT" not in sql

    def test_sort_desc(self):
        filters = SearchFilters(sort_order=SortOrder.desc)
        sql, _ = build_search_query(filters)
        assert "DESC" in sql

    def test_sort_by_release_date(self):
        filters = SearchFilters(sort_by="release_date")
        sql, _ = build_search_query(filters)
        assert "c.created_at" in sql

    def test_sort_by_tissue(self):
        filters = SearchFilters(sort_by="tissue")
        sql, _ = build_search_query(filters)
        assert "c.tissue IS NULL OR LOWER(TRIM(c.tissue))" in sql
        assert "c.tissue ASC" in sql

    def test_sort_by_series_uses_the_whole_mapping(self):
        """Ordered by a run's lowest study, not the one `sra_core` happens to keep.

        870 runs belong to two studies, so sorting on the core column ordered
        them by whichever of the two was stored — samples of one study could end
        up separated by a sample of another.
        """
        filters = SearchFilters(sort_by="series")
        sql, _ = build_search_query(filters)
        assert "s_sort.Series IS NULL OR LOWER(TRIM(s_sort.Series))" in sql
        assert "s_sort.Series ASC" in sql
        assert "c.Series ASC" not in sql

    def test_series_sort_joins_one_row_per_run(self):
        """Grouped, so a run in two studies is not returned twice."""
        sql, _ = build_search_query(SearchFilters(sort_by="series"))
        assert "MIN(Series)" in sql
        assert "GROUP BY Run" in sql

    def test_sort_by_assay_type_joins_the_eav_row(self):
        """assay_type is not a column, so the row holding it is joined in."""
        filters = SearchFilters(sort_by="assay_type")
        sql, _ = build_search_query(filters)
        assert "LEFT JOIN sra_extended e_sort" in sql
        assert "e_sort.field_name = 'Assay Type'" in sql
        assert "e_sort.field_value IS NULL OR LOWER(TRIM(e_sort.field_value))" in sql
        assert "e_sort.field_value ASC" in sql

    def test_other_sorts_do_not_join(self):
        """A join is the cost of the sort that needs it and no other pays it."""
        for field in ("run_id", "tissue", "release_date"):
            sql, _ = build_search_query(SearchFilters(sort_by=field))
            assert "LEFT JOIN" not in sql, field

    def test_count_never_joins(self):
        """Counting does not order, so it has no reason to reach for the row."""
        sql, _ = build_search_query(SearchFilters(sort_by="assay_type"), count_only=True)
        assert "LEFT JOIN" not in sql

    def test_unknown_sort_field_falls_back_to_run(self):
        filters = SearchFilters(sort_by="not_a_column")
        sql, _ = build_search_query(filters)
        assert "c.Run IS NULL OR LOWER(TRIM(c.Run))" in sql

    def test_sort_puts_missing_values_last(self):
        """A sample with no tissue belongs at the end, not on the first page."""
        filters = SearchFilters(sort_by="tissue")
        sql, _ = build_search_query(filters)
        assert sql.index("c.tissue IS NULL") < sql.index("c.tissue ASC")

    def test_sort_treats_placeholder_text_as_missing(self):
        """"-" is a submitter's way of writing nothing; it must not sort first."""
        sql, _ = build_search_query(SearchFilters(sort_by="tissue"))
        order_by = sql[sql.index("ORDER BY") : sql.index("LIMIT")]
        assert "'-'" in order_by
        assert "'--'" in order_by
        assert "'n/a'" in order_by
        # Compared case- and space-insensitively, so " N/A " counts too.
        assert "LOWER(TRIM(c.tissue))" in order_by

    def test_placeholder_rows_are_not_filtered_out(self):
        """They sort last; they are still part of the result set."""
        sql, _ = build_search_query(SearchFilters(sort_by="tissue"))
        where = sql[sql.index("WHERE") : sql.index("ORDER BY")]
        assert "n/a" not in where.lower()

    def test_sort_has_a_stable_tiebreaker(self):
        """Equal sort keys must keep a fixed order or paging repeats rows."""
        filters = SearchFilters(sort_by="tissue", sort_order=SortOrder.desc)
        sql, _ = build_search_query(filters)
        order_by = sql[sql.index("ORDER BY") : sql.index("LIMIT")]
        assert order_by.rstrip().endswith("c.Run ASC")
        assert "c.tissue DESC" in order_by

    def test_series_filter_reads_the_whole_mapping(self):
        """Matched through sra_series, not the one series sra_core happens to hold.

        A run shared by two studies carries one of them in `c.Series`, so matching
        that column answered for one study and hid the run from the other.
        """
        filters = SearchFilters(series="GSE218390")
        sql, params = build_search_query(filters)
        assert "c.Series = ?" not in sql
        assert "FROM sra_series s WHERE s.Run = c.Run AND s.Series = ?" in sql
        assert "GSE218390" in params

    def test_platform_filter(self):
        filters = SearchFilters(platform=["ILLUMINA", "DNBSEQ"])
        sql, params = build_search_query(filters)
        assert "c.Platform IN (?, ?)" in sql

    def test_pagination_in_params(self):
        filters = SearchFilters(offset=40, limit=50)
        sql, params = build_search_query(filters)
        assert params[-2:] == [50, 40]
