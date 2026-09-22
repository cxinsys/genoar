"""Search-related models."""

from enum import Enum

from pydantic import BaseModel, Field

from app.models.common import PaginationParams, SortOrder, SortParams
from app.models.sample import SampleSummary


class SearchMode(str, Enum):
    sql = "sql"
    semantic = "semantic"
    hybrid = "hybrid"


class SearchFilters(BaseModel):
    organism: list[str] = Field(default_factory=list)
    tissue: list[str] = Field(default_factory=list)
    cell_type: list[str] = Field(default_factory=list)
    assay_type: list[str] = Field(default_factory=list)
    library_source: list[str] = Field(default_factory=list)
    disease: list[str] = Field(default_factory=list)
    platform: list[str] = Field(default_factory=list)
    series: str | None = None
    keyword: str | None = None
    search_mode: SearchMode = Field(default=SearchMode.sql)
    # How many candidates the vector search should return before paging. Only
    # semantic and hybrid read it — a SQL count is exact and needs no cutoff.
    # None leaves the server's default pool size in place.
    #
    # The ceiling is SEMANTIC_POOL_SIZE in services/search_service.py, repeated
    # here and in the two routers because pydantic wants a literal. Asking for
    # more than the service will build pages off the end of the candidates.
    top_k: int | None = Field(default=None, ge=1, le=500)
    # Pagination & sorting
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
    # None means "whatever this search's own order is". A structured search has
    # no inherent order and falls back to run id; a semantic one is already in
    # relevance order and keeps it. Defaulting to "run_id" here made a request
    # that said nothing about sorting look like a request to sort by run id,
    # which for an AI search would throw away the ranking that is the answer.
    sort_by: str | None = Field(default=None)
    sort_order: SortOrder = Field(default=SortOrder.asc)

    def to_pagination(self) -> PaginationParams:
        return PaginationParams(offset=self.offset, limit=self.limit)

    def to_sort(self) -> SortParams:
        return SortParams(sort_by=self.sort_by or "run_id", sort_order=self.sort_order)

    def has_filters(self) -> bool:
        return bool(
            self.organism
            or self.tissue
            or self.cell_type
            or self.assay_type
            or self.library_source
            or self.disease
            or self.platform
            or self.series
            or self.keyword
        )

    def has_sql_filters(self) -> bool:
        """Check if any SQL-relevant filters (excluding keyword) are set."""
        return bool(
            self.organism
            or self.tissue
            or self.cell_type
            or self.assay_type
            or self.library_source
            or self.disease
            or self.platform
            or self.series
        )


class SearchResponse(BaseModel):
    items: list[SampleSummary]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    search_mode: SearchMode = Field(default=SearchMode.sql)
    filters_applied: SearchFilters
