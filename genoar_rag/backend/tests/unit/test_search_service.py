"""Unit tests for SearchService."""

from app.models.common import SortOrder
from app.models.search import SearchFilters, SearchMode
from app.services.search_service import (
    HybridSearchStrategy,
    SQLSearchStrategy,
    SearchService,
    SemanticSearchStrategy,
)
from app.services.vector_service import VectorSearchResult


class MockVectorService:
    """Returns a fixed set of results from test DB run IDs."""

    def __init__(self):
        self._results = [
            VectorSearchResult("SRR001", 0.95),
            VectorSearchResult("SRR003", 0.88),
            VectorSearchResult("SRR005", 0.72),
            VectorSearchResult("SRR007", 0.60),
            VectorSearchResult("SRR009", 0.45),
        ]

    @property
    def total_vectors(self) -> int:
        return 100

    def search_by_text(
        self, query: str, top_k: int = 10, organisms: list[str] | None = None
    ) -> list[VectorSearchResult]:
        if not query or not query.strip():
            return []
        return self._results[:top_k]


class TestSearchService:
    def test_search_no_filters(self, db_conn):
        strategy = SQLSearchStrategy(db_conn)
        svc = SearchService(strategy)
        result = svc.search(SearchFilters())
        assert result.total == 10
        assert len(result.items) == 10

    def test_search_with_organism(self, db_conn):
        strategy = SQLSearchStrategy(db_conn)
        svc = SearchService(strategy)
        result = svc.search(SearchFilters(organism=["Mus musculus"]))
        assert result.total == 2
        assert all(s.organism == "Mus musculus" for s in result.items)

    def test_search_response_includes_filters(self, db_conn):
        strategy = SQLSearchStrategy(db_conn)
        svc = SearchService(strategy)
        filters = SearchFilters(tissue=["Blood"])
        result = svc.search(filters)
        assert result.filters_applied.tissue == ["Blood"]

    def test_search_pagination(self, db_conn):
        strategy = SQLSearchStrategy(db_conn)
        svc = SearchService(strategy)
        result = svc.search(SearchFilters(limit=3, offset=0))
        assert len(result.items) == 3
        assert result.total == 10
        assert result.offset == 0
        assert result.limit == 3


class TestSearchModeDefault:
    def test_search_mode_default_is_sql(self):
        filters = SearchFilters()
        assert filters.search_mode == SearchMode.sql


class TestSemanticSearchStrategy:
    def test_semantic_empty_keyword_returns_empty(self, db_conn):
        vs = MockVectorService()
        strategy = SemanticSearchStrategy(vs, db_conn)
        result = strategy.search(SearchFilters(search_mode=SearchMode.semantic))
        assert result.items == []
        assert result.total == 0
        assert result.search_mode == SearchMode.semantic

    def test_semantic_returns_results_with_scores(self, db_conn):
        vs = MockVectorService()
        strategy = SemanticSearchStrategy(vs, db_conn)
        result = strategy.search(
            SearchFilters(keyword="lung cancer", search_mode=SearchMode.semantic)
        )
        assert len(result.items) > 0
        assert all(item.similarity_score is not None for item in result.items)
        assert result.search_mode == SearchMode.semantic

    def test_semantic_applies_structured_filters(self, db_conn):
        """A filter set alongside the query narrows the result, not just the label.

        Handing only the query and the organism to the index, while echoing the
        rest back in `filters_applied`, answers a request for bone marrow with
        lung and blood under a response claiming bone marrow was applied. The
        mock returns
        SRR001 (Bone Marrow), SRR003 (Blood), SRR005 (Liver), SRR007 (Brain) and
        SRR009 (Lung), so a tissue filter has something to remove.
        """
        vs = MockVectorService()
        strategy = SemanticSearchStrategy(vs, db_conn)
        result = strategy.search(
            SearchFilters(
                keyword="lung cancer",
                tissue=["Bone Marrow"],
                search_mode=SearchMode.semantic,
            )
        )
        assert [item.run_id for item in result.items] == ["SRR001"]
        assert result.total == 1

    def test_semantic_filter_can_empty_the_result(self, db_conn):
        """No candidate matching the filter returns nothing rather than the pool."""
        vs = MockVectorService()
        strategy = SemanticSearchStrategy(vs, db_conn)
        result = strategy.search(
            SearchFilters(
                keyword="lung cancer",
                tissue=["Pancreas"],
                search_mode=SearchMode.semantic,
            )
        )
        assert result.items == []
        assert result.total == 0

    def test_semantic_default_order_is_relevance(self, db_conn):
        """No sort asked for means the order the search produced.

        A router-level default of "run_id" would throw away the ranking that is
        the answer for an AI search.
        """
        vs = MockVectorService()
        strategy = SemanticSearchStrategy(vs, db_conn)
        result = strategy.search(
            SearchFilters(keyword="lung cancer", search_mode=SearchMode.semantic)
        )
        assert [i.run_id for i in result.items] == [
            "SRR001",
            "SRR003",
            "SRR005",
            "SRR007",
            "SRR009",
        ]

    def test_semantic_sorts_by_run_id_when_asked(self, db_conn):
        """asc and desc must differ: unsorted, both are FAISS order."""
        vs = MockVectorService()
        strategy = SemanticSearchStrategy(vs, db_conn)
        asc = strategy.search(
            SearchFilters(
                keyword="lung cancer",
                search_mode=SearchMode.semantic,
                sort_by="run_id",
                sort_order=SortOrder.asc,
            )
        )
        desc = strategy.search(
            SearchFilters(
                keyword="lung cancer",
                search_mode=SearchMode.semantic,
                sort_by="run_id",
                sort_order=SortOrder.desc,
            )
        )
        asc_ids = [i.run_id for i in asc.items]
        desc_ids = [i.run_id for i in desc.items]
        assert asc_ids == sorted(asc_ids)
        assert desc_ids == list(reversed(asc_ids))

    def test_candidate_run_ids_is_empty_without_a_keyword(self, db_conn):
        """The same answer `search` gives, so export cannot widen past it.

        Running the SQL path when no keyword comes with a semantic request
        turns a page showing nothing into a download of the whole corpus.
        """
        vs = MockVectorService()
        strategy = SemanticSearchStrategy(vs, db_conn)
        assert strategy.candidate_run_ids(SearchFilters()) == []
        assert strategy.candidate_run_ids(SearchFilters(keyword="   ")) == []

    def test_candidate_run_ids_ignores_the_page_size(self, db_conn):
        """Not bounded by `limit`, which caps at 100.

        Requesting the pool through `limit` fails model validation for a
        500-candidate export and answers HTTP 500 — as does omitting `top_k`,
        which falls back to that same 500.
        """
        vs = MockVectorService()
        strategy = SemanticSearchStrategy(vs, db_conn)
        ids = strategy.candidate_run_ids(
            SearchFilters(keyword="lung cancer", top_k=500, limit=1)
        )
        assert ids == ["SRR001", "SRR003", "SRR005", "SRR007", "SRR009"]

    def test_candidate_run_ids_keeps_relevance_order(self, db_conn):
        vs = MockVectorService()
        strategy = SemanticSearchStrategy(vs, db_conn)
        page = strategy.search(SearchFilters(keyword="lung cancer", limit=100))
        assert strategy.candidate_run_ids(SearchFilters(keyword="lung cancer")) == [
            i.run_id for i in page.items
        ]

    def test_semantic_without_filters_keeps_the_whole_pool(self, db_conn):
        """The narrowing only happens when something was asked for."""
        vs = MockVectorService()
        strategy = SemanticSearchStrategy(vs, db_conn)
        result = strategy.search(
            SearchFilters(keyword="lung cancer", search_mode=SearchMode.semantic)
        )
        assert result.total == 5

    def test_semantic_results_have_metadata(self, db_conn):
        vs = MockVectorService()
        strategy = SemanticSearchStrategy(vs, db_conn)
        result = strategy.search(
            SearchFilters(keyword="lung cancer", search_mode=SearchMode.semantic)
        )
        for item in result.items:
            assert item.run_id is not None
            # SRR001 is Homo sapiens, Bone Marrow
            if item.run_id == "SRR001":
                assert item.organism == "Homo sapiens"
                assert item.tissue == "Bone Marrow"

    def test_semantic_pagination(self, db_conn):
        vs = MockVectorService()
        strategy = SemanticSearchStrategy(vs, db_conn)
        # Get first 2
        result1 = strategy.search(
            SearchFilters(
                keyword="test", search_mode=SearchMode.semantic, limit=2, offset=0
            )
        )
        assert len(result1.items) == 2
        # Get next 2
        result2 = strategy.search(
            SearchFilters(
                keyword="test", search_mode=SearchMode.semantic, limit=2, offset=2
            )
        )
        assert len(result2.items) == 2
        # No overlap
        ids1 = {item.run_id for item in result1.items}
        ids2 = {item.run_id for item in result2.items}
        assert ids1.isdisjoint(ids2)

    def test_semantic_scores_descending(self, db_conn):
        vs = MockVectorService()
        strategy = SemanticSearchStrategy(vs, db_conn)
        result = strategy.search(
            SearchFilters(keyword="test", search_mode=SearchMode.semantic, limit=10)
        )
        scores = [item.similarity_score for item in result.items]
        assert scores == sorted(scores, reverse=True)


class TestHybridSearchStrategy:
    def test_hybrid_no_keyword_delegates_to_sql(self, db_conn):
        vs = MockVectorService()
        strategy = HybridSearchStrategy(vs, db_conn)
        result = strategy.search(
            SearchFilters(tissue=["Blood"], search_mode=SearchMode.hybrid)
        )
        # Should behave like SQL: Blood tissue has 2 samples (SRR003, SRR004)
        assert result.total == 2
        assert result.search_mode == SearchMode.hybrid
        # SQL results don't have similarity scores
        assert all(item.similarity_score is None for item in result.items)

    def test_hybrid_keyword_only_delegates_to_semantic(self, db_conn):
        vs = MockVectorService()
        strategy = HybridSearchStrategy(vs, db_conn)
        result = strategy.search(
            SearchFilters(keyword="lung cancer", search_mode=SearchMode.hybrid)
        )
        # Should behave like semantic: has similarity scores
        assert len(result.items) > 0
        assert result.search_mode == SearchMode.hybrid
        assert all(item.similarity_score is not None for item in result.items)

    def test_hybrid_intersection(self, db_conn):
        vs = MockVectorService()
        strategy = HybridSearchStrategy(vs, db_conn)
        # Mock returns SRR001,003,005,007,009 ; tissue=Blood matches SRR003,004
        # Intersection: SRR003 only
        result = strategy.search(
            SearchFilters(
                keyword="test", tissue=["Blood"], search_mode=SearchMode.hybrid
            )
        )
        assert result.search_mode == SearchMode.hybrid
        run_ids = [item.run_id for item in result.items]
        assert "SRR003" in run_ids
        assert "SRR004" not in run_ids  # not in semantic results

    def test_hybrid_intersection_has_scores(self, db_conn):
        vs = MockVectorService()
        strategy = HybridSearchStrategy(vs, db_conn)
        result = strategy.search(
            SearchFilters(
                keyword="test", tissue=["Blood"], search_mode=SearchMode.hybrid
            )
        )
        for item in result.items:
            assert item.similarity_score is not None

    def test_hybrid_empty_intersection(self, db_conn):
        """SQL filters match only samples NOT in semantic results → empty."""
        vs = MockVectorService()
        strategy = HybridSearchStrategy(vs, db_conn)
        # Mock returns odd-numbered SRRs; platform=DNBSEQ matches SRR005,006
        # SRR005 IS in semantic results → not empty. Use a different filter.
        # cell_type=Astrocytes matches only SRR008 which is NOT in mock results
        result = strategy.search(
            SearchFilters(
                keyword="test",
                cell_type=["Astrocytes"],
                search_mode=SearchMode.hybrid,
            )
        )
        assert result.total == 0
        assert result.items == []

    def test_hybrid_pagination(self, db_conn):
        vs = MockVectorService()
        strategy = HybridSearchStrategy(vs, db_conn)
        # Use organism=Homo sapiens (8 samples) ∩ semantic (5 samples)
        # Intersection: SRR001,003,005,009 (SRR007 is Homo sapiens too → 5 total)
        result_all = strategy.search(
            SearchFilters(
                keyword="test",
                organism=["Homo sapiens"],
                search_mode=SearchMode.hybrid,
                limit=100,
            )
        )
        total = result_all.total

        result_page = strategy.search(
            SearchFilters(
                keyword="test",
                organism=["Homo sapiens"],
                search_mode=SearchMode.hybrid,
                limit=2,
                offset=0,
            )
        )
        assert len(result_page.items) == 2
        assert result_page.total == total
