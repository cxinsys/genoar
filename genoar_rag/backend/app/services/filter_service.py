"""Filter options: the opening set for every category, and paging within one."""

import sqlite3
import time

from app.config import settings
from app.models.filters import FilterCategory, FilterCategoryPage, FilterOptionsResponse, FilterValue
from app.repositories.filter_repository import FilterRepository
from app.services.filter_catalog import FILTER_CATEGORIES, FilterCategorySpec

# How many values each category contributes to the combined /filters payload.
# A page of the per-category endpoint is the same size, so a client that has the
# combined response holds exactly page one.
DEFAULT_PAGE_SIZE = 50


class FilterService:
    # Keyed by dataset, not a single slot. Two datasets hold different values
    # as well as different counts, so one cache would hand whichever page asked
    # first its answer to the other.
    _cache: dict[str, tuple[float, FilterOptionsResponse]] = {}
    # CACHE_TTL_SECONDS, read once at import. Shared with StatsService, which
    # caches the same database's figures on the same terms; a deployment that
    # wants fresher filter values wants fresher totals too.
    _cache_ttl: float = settings.cache_ttl_seconds

    # Counting the distinct values in a category is the expensive part — it reads
    # the whole column or EAV slice — and the answer is the same for every client
    # until the data changes. Only unfiltered totals are kept: a search's total
    # depends on its text, which would make this unbounded.
    _total_cache: dict[tuple[str, str], tuple[float, int]] = {}

    def __init__(self, conn: sqlite3.Connection, dataset: str = "default"):
        self._conn = conn
        self._dataset = dataset
        self._repo = FilterRepository(conn)

    def get_filter_options(self) -> FilterOptionsResponse:
        now = time.time()
        cached = FilterService._cache.get(self._dataset)
        if cached and (now - cached[0]) < FilterService._cache_ttl:
            return cached[1]

        categories = [
            self._category(spec, limit=DEFAULT_PAGE_SIZE, offset=0, query=None)
            for spec in FILTER_CATEGORIES
        ]

        result = FilterOptionsResponse(categories=categories)
        FilterService._cache[self._dataset] = (now, result)
        return result

    def get_category_page(
        self,
        spec: FilterCategorySpec,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        query: str | None = None,
    ) -> FilterCategoryPage:
        category = self._category(spec, limit=limit, offset=offset, query=query)
        return FilterCategoryPage(
            **category.model_dump(), offset=offset, limit=limit, query=query or None
        )

    def _category(
        self, spec: FilterCategorySpec, limit: int, offset: int, query: str | None
    ) -> FilterCategory:
        rows = self._repo.values(spec, limit=limit, offset=offset, query=query)
        return FilterCategory(
            name=spec.name,
            values=[FilterValue(value=r["value"], count=r["count"]) for r in rows],
            total_distinct=self._total(spec, query),
        )

    def _total(self, spec: FilterCategorySpec, query: str | None) -> int:
        if query and query.strip():
            return self._repo.total(spec, query)

        now = time.time()
        key = (self._dataset, spec.name)
        cached = FilterService._total_cache.get(key)
        if cached is not None:
            cached_at, value = cached
            if now - cached_at < FilterService._cache_ttl:
                return int(value)

        total = self._repo.total(spec)
        FilterService._total_cache[key] = (now, total)
        return total

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache = {}
        cls._total_cache = {}
