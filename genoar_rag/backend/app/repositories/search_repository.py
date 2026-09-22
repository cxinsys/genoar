"""Repository for complex search queries."""

from app.models.search import SearchFilters
from app.repositories.base import BaseRepository
from app.utils.query_builder import build_search_query


class SearchRepository(BaseRepository):
    def search(
        self,
        filters: SearchFilters,
        run_id_in: list[str] | None = None,
    ) -> list[dict]:
        sql, params = build_search_query(
            filters,
            count_only=False,
            run_id_in=run_id_in,
        )
        return self._fetchall(sql, params)

    def iter_search(
        self,
        filters: SearchFilters,
        limit: int,
        run_id_in: list[str] | None = None,
        chunk: int = 500,
    ):
        """The same search, run once and streamed in chunks.

        For an export, which wants the whole set rather than a page of it. The
        paged form asks the database again for every hundred rows and skips a
        little further each time, so a two-hundred-thousand-row export spent its
        time scanning past rows it had already emitted.
        """
        sql, params = build_search_query(
            filters,
            count_only=False,
            run_id_in=run_id_in,
            page=(limit, filters.offset),
        )
        yield from self._iterall(sql, params, chunk=chunk)

    def search_count(
        self,
        filters: SearchFilters,
        run_id_in: list[str] | None = None,
    ) -> int:
        sql, params = build_search_query(
            filters,
            count_only=True,
            run_id_in=run_id_in,
        )
        return self._fetchval(sql, params)
