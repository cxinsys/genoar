"""Paging and search over a filter category's values.

The fixture corpus has five tissues, each on two samples, which is the awkward
case for paging: every count ties, so the order only holds if something breaks
the tie.
"""

from app.services.filter_catalog import get_category_spec
from app.services.filter_service import FilterService


class TestCategoryPaging:
    def setup_method(self):
        FilterService.clear_cache()

    def test_total_counts_every_value_not_the_page(self, db_conn):
        svc = FilterService(db_conn)
        page = svc.get_category_page(get_category_spec("tissue"), limit=2, offset=0)
        assert len(page.values) == 2
        # Bone Marrow, Blood, Liver, Brain, Lung — the point of the field is to
        # tell a client how much it is not being shown.
        assert page.total_distinct == 5

    def test_paging_covers_the_category_exactly_once(self, db_conn):
        svc = FilterService(db_conn)
        spec = get_category_spec("tissue")

        whole = [v.value for v in svc.get_category_page(spec, limit=50).values]
        paged: list[str] = []
        for offset in range(0, 6, 2):
            paged += [v.value for v in svc.get_category_page(spec, limit=2, offset=offset).values]

        assert paged == whole
        assert len(paged) == len(set(paged)) == 5

    def test_page_beyond_the_end_is_empty(self, db_conn):
        svc = FilterService(db_conn)
        page = svc.get_category_page(get_category_spec("tissue"), limit=10, offset=99)
        assert page.values == []
        assert page.total_distinct == 5

    def test_values_keep_the_spelling_they_were_written_with(self, db_conn):
        """Case-insensitive grouping folds "Blood" and "blood" into one entry,
        but the entry is still labelled the way a submitter wrote it."""
        svc = FilterService(db_conn)
        page = svc.get_category_page(get_category_spec("tissue"), limit=50)
        blood = next(v for v in page.values if v.value.lower() == "blood")
        assert blood.value == "Blood"
        assert blood.count == 2

    def test_page_echoes_the_request(self, db_conn):
        svc = FilterService(db_conn)
        page = svc.get_category_page(
            get_category_spec("tissue"), limit=3, offset=1, query="l"
        )
        assert (page.offset, page.limit, page.query) == (1, 3, "l")


class TestCategorySearch:
    def setup_method(self):
        FilterService.clear_cache()

    def test_search_narrows_values_and_total(self, db_conn):
        svc = FilterService(db_conn)
        page = svc.get_category_page(get_category_spec("tissue"), query="bl")
        assert [v.value for v in page.values] == ["Blood"]
        # The total has to follow the search, or a client pages through a count
        # that does not describe the narrowed set.
        assert page.total_distinct == 1

    def test_search_ignores_case(self, db_conn):
        svc = FilterService(db_conn)
        upper = svc.get_category_page(get_category_spec("tissue"), query="LIVER")
        lower = svc.get_category_page(get_category_spec("tissue"), query="liver")
        assert [v.value for v in upper.values] == [v.value for v in lower.values] == ["Liver"]

    def test_search_matches_inside_the_value(self, db_conn):
        svc = FilterService(db_conn)
        page = svc.get_category_page(get_category_spec("library_source"), query="single")
        assert [v.value for v in page.values] == ["TRANSCRIPTOMIC SINGLE CELL"]

    def test_wildcards_in_the_query_are_literal(self, db_conn):
        """A submitted % must not turn into "match everything"."""
        svc = FilterService(db_conn)
        page = svc.get_category_page(get_category_spec("tissue"), query="%")
        assert page.values == []
        assert page.total_distinct == 0

    def test_blank_search_is_no_search(self, db_conn):
        svc = FilterService(db_conn)
        blank = svc.get_category_page(get_category_spec("tissue"), query="   ")
        assert blank.total_distinct == 5


class TestExtendedCategories:
    def setup_method(self):
        FilterService.clear_cache()

    def test_disease_offers_the_resolved_value(self, db_conn):
        """One entry per run, and the concept wins where there is one.

        SRR003 arrives as `disease: healthy` and carries the nomenclaturist's
        "Control Groups"; it is offered under the concept. That is the point of
        resolving: a reader ticking one box gets every spelling of it.
        """
        svc = FilterService(db_conn)
        page = svc.get_category_page(get_category_spec("disease"), limit=50)
        assert page.total_distinct == 5
        assert {v.value.lower() for v in page.values} == {
            "leukemia", "control groups", "hepatitis", "alzheimer", "lung cancer",
        }

    def test_controlled_vocabulary_is_not_folded(self, db_conn):
        svc = FilterService(db_conn)
        page = svc.get_category_page(get_category_spec("organism"), limit=50)
        assert [(v.value, v.count) for v in page.values] == [
            ("Homo sapiens", 8),
            ("Mus musculus", 2),
        ]


class TestTotalsCache:
    def setup_method(self):
        FilterService.clear_cache()

    def test_totals_survive_across_service_instances(self, db_conn):
        spec = get_category_spec("tissue")
        first = FilterService(db_conn).get_category_page(spec, limit=1)
        second = FilterService(db_conn).get_category_page(spec, limit=1)
        assert first.total_distinct == second.total_distinct == 5

    def test_clear_cache_drops_totals(self, db_conn):
        FilterService(db_conn).get_category_page(get_category_spec("tissue"), limit=1)
        assert FilterService._total_cache
        FilterService.clear_cache()
        assert FilterService._total_cache == {}


class TestCombinedResponse:
    def setup_method(self):
        FilterService.clear_cache()

    def test_combined_response_reports_true_totals(self, db_conn):
        """/filters returns a page per category, so its totals are the ones a
        client uses to decide whether there is more to fetch."""
        svc = FilterService(db_conn)
        result = svc.get_filter_options()
        totals = {c.name: c.total_distinct for c in result.categories}
        assert totals["tissue"] == 5
        assert totals["organism"] == 2
        assert totals["disease"] == 5
        assert totals["platform"] == 2
