"""Search service with Strategy pattern for SQL, semantic, and hybrid search."""

import sqlite3
from typing import Protocol

from app.models.common import SortOrder
from app.models.search import SearchFilters, SearchMode, SearchResponse
from app.repositories.search_repository import SearchRepository
from app.services.sample_service import _enrich_summaries
from app.services.vector_router import VectorRouter

# How many semantic candidates to fetch: the pool a hybrid search intersects
# against, and the depth a semantic search pages within when the caller names no
# top_k of its own.
#
# The same 500 is the `top_k` ceiling the API enforces — `le=500` in
# models/search.py, routers/search.py and routers/export.py. The two are one
# number and have to move together: a request allowed to ask for a pool deeper
# than this service will build would page past the end of the candidates and be
# answered with fewer rows than it asked for and no error saying why.
SEMANTIC_POOL_SIZE = 500


class SearchStrategy(Protocol):
    def search(self, filters: SearchFilters) -> SearchResponse: ...


class SQLSearchStrategy:
    """SQL-based structured search."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._repo = SearchRepository(conn)

    def search(self, filters: SearchFilters) -> SearchResponse:
        rows = self._repo.search(filters)
        total = self._repo.search_count(filters)
        summaries = _enrich_summaries(rows, self._conn)

        return SearchResponse(
            items=summaries,
            total=total,
            offset=filters.offset,
            limit=filters.limit,
            search_mode=SearchMode.sql,
            filters_applied=filters,
        )


def has_structured_filters_beyond_organism(filters: SearchFilters) -> bool:
    """Whether anything but the species narrows this search.

    Organism is left out because it is not applied with SQL: it chooses which
    index to search, so the vector results are already restricted to it and a
    second pass over the database would only confirm what they are.
    """
    return bool(
        filters.tissue
        or filters.cell_type
        or filters.assay_type
        or filters.library_source
        or filters.disease
        or filters.platform
        or filters.series
    )


def restrict_to_structured_matches(
    conn: sqlite3.Connection, filters: SearchFilters, results: list
) -> list:
    """Keep the vector hits that also satisfy the structured filters.

    Nearest-neighbour search always returns neighbours, so the structured
    filters have nothing to act on unless they are applied here. Skip this and
    a request for mouse COVID samples in bone marrow comes back with lung,
    blood and spleen under a response that says Bone Marrow was applied — an
    answer that looks broken rather than empty.

    Order is preserved, so callers still see the vector ranking.
    """
    if not results or not has_structured_filters_beyond_organism(filters):
        return results

    repo = SearchRepository(conn)
    run_ids = [r.run_id for r in results]
    sql_filter = filters.model_copy(
        update={"keyword": None, "offset": 0, "limit": len(run_ids)}
    )
    matched = {row["Run"] for row in repo.search(sql_filter, run_id_in=run_ids)}
    return [r for r in results if r.run_id in matched]


def order_semantic_results(filters: SearchFilters, results: list) -> list:
    """Put the vector hits in the order the caller asked for.

    The index answers in FAISS order whatever `sort_by` says, and the page
    offers Similarity, Run ID A→Z and Run ID Z→A — without this step all three
    produce the same list. Only run id can be honoured here: the other sort
    keys name columns, and the rows have not been read yet.

    Similarity stays the default and the fallback — it is the order the search
    itself produces, and the one a request that says nothing should get.
    """
    if filters.sort_by != "run_id":
        return results
    return sorted(
        results,
        key=lambda r: r.run_id,
        reverse=filters.sort_order == SortOrder.desc,
    )


class SemanticSearchStrategy:
    """FAISS-based semantic search, narrowed by whatever structured filters are set."""

    def __init__(self, vector_service: VectorRouter, conn: sqlite3.Connection):
        self._vs = vector_service
        self._conn = conn

    def candidate_run_ids(self, filters: SearchFilters) -> list[str]:
        """Every run this search matches, in result order and without paging.

        The page a caller sees is a slice of this. Export needs the whole thing,
        and needed it without borrowing `limit` to say so: `limit` caps at 100,
        so asking for a 500-candidate export raised a validation error and the
        endpoint answered 500 — as did omitting `top_k` entirely, which fell back
        to the same 500-wide pool.

        An empty query returns an empty list, the same as `search`. Export used
        to skip this path when there was no keyword and fall through to an
        unfiltered SQL export, so a screen showing nothing offered a download of
        the whole corpus.
        """
        keyword = (filters.keyword or "").strip()
        if not keyword:
            return []
        pool = filters.top_k or SEMANTIC_POOL_SIZE
        top_k = min(pool, self._vs.total_vectors)
        results = self._vs.search_by_text(
            keyword, top_k=top_k, organisms=filters.organism
        )
        results = restrict_to_structured_matches(self._conn, filters, results)
        results = order_semantic_results(filters, results)
        return [r.run_id for r in results]

    def search(self, filters: SearchFilters) -> SearchResponse:
        keyword = (filters.keyword or "").strip()
        if not keyword:
            return SearchResponse(
                items=[],
                total=0,
                offset=filters.offset,
                limit=filters.limit,
                search_mode=SearchMode.semantic,
                filters_applied=filters,
            )

        # Fetch the whole candidate pool, not just enough to cover this page.
        # Sizing top_k to offset+limit made `total` grow with every page, so it
        # always equalled what had been fetched and a client could never tell
        # there was a page after this one. The pool is the same one hybrid
        # intersects against, which also makes `total` mean the same thing in
        # both modes: how many candidates the vector search offered, not how
        # many samples in the corpus "match" — every vector matches to a degree.
        pool = filters.top_k or SEMANTIC_POOL_SIZE
        top_k = min(pool, self._vs.total_vectors)
        results = self._vs.search_by_text(
            keyword, top_k=top_k, organisms=filters.organism
        )
        results = restrict_to_structured_matches(self._conn, filters, results)
        results = order_semantic_results(filters, results)

        total = len(results)
        page = results[filters.offset : filters.offset + filters.limit]

        if not page:
            return SearchResponse(
                items=[],
                total=total,
                offset=filters.offset,
                limit=filters.limit,
                search_mode=SearchMode.semantic,
                filters_applied=filters,
            )

        run_ids = [r.run_id for r in page]

        from app.services.sample_service import SampleService

        svc = SampleService(self._conn)
        summaries = svc.get_summaries_for_run_ids(run_ids)
        summary_map = {s.run_id: s for s in summaries}

        items = []
        for r in page:
            s = summary_map.get(r.run_id)
            if s:
                s.similarity_score = r.score
                items.append(s)

        return SearchResponse(
            items=items,
            total=total,
            offset=filters.offset,
            limit=filters.limit,
            search_mode=SearchMode.semantic,
            filters_applied=filters,
        )


class HybridSearchStrategy:
    """Hybrid search: intersection of SQL and semantic results."""

    def __init__(self, vector_service: VectorRouter, conn: sqlite3.Connection):
        self._vs = vector_service
        self._conn = conn
        self._sql_strategy = SQLSearchStrategy(conn)
        self._semantic_strategy = SemanticSearchStrategy(vector_service, conn)

    def search(self, filters: SearchFilters) -> SearchResponse:
        keyword = (filters.keyword or "").strip()

        # No keyword → SQL fallback
        if not keyword:
            result = self._sql_strategy.search(filters)
            result.search_mode = SearchMode.hybrid
            return result

        # Keyword present → semantic, which applies the structured filters itself.
        #
        # One path, not two. Semantic already intersects its vector hits with
        # the structured filters, so a second copy of that step here — for the
        # case where filters accompany the keyword — could only drift from it
        # and let the AI Librarian ignore the filters the page lets people set.
        # Hybrid is the same search under a different name.
        result = self._semantic_strategy.search(filters)
        result.search_mode = SearchMode.hybrid
        return result


class SearchService:
    def __init__(self, strategy: SearchStrategy):
        self._strategy = strategy

    def search(self, filters: SearchFilters) -> SearchResponse:
        return self._strategy.search(filters)
