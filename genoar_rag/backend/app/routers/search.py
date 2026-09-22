"""Search endpoint."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_db, get_dataset, get_vector_service
from app.models.common import SortOrder
from app.models.search import SearchFilters, SearchMode, SearchResponse
from app.services.search_service import (
    HybridSearchStrategy,
    SQLSearchStrategy,
    SearchService,
    SemanticSearchStrategy,
)
from app.services.vector_router import VectorRouter

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.get("/search", response_model=SearchResponse)
def search(
    organism: list[str] = Query(default=[]),
    tissue: list[str] = Query(default=[]),
    cell_type: list[str] = Query(default=[]),
    assay_type: list[str] = Query(default=[]),
    library_source: list[str] = Query(default=[]),
    disease: list[str] = Query(default=[]),
    platform: list[str] = Query(default=[]),
    series: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    search_mode: SearchMode = Query(default=SearchMode.sql),
    # le=500 is SEMANTIC_POOL_SIZE in services/search_service.py — the deepest
    # pool the service builds. See the note there before changing either.
    top_k: int | None = Query(
        default=None,
        ge=1,
        le=500,
        description="How many candidates semantic/hybrid search returns before paging.",
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    # None means the search's own order: run id for SQL, relevance for AI.
    sort_by: str | None = Query(default=None),
    sort_order: SortOrder = Query(default=SortOrder.asc),
    dataset: str = Depends(get_dataset),
    conn: sqlite3.Connection = Depends(get_db),
    vector_service: VectorRouter | None = Depends(get_vector_service),
):
    filters = SearchFilters(
        organism=organism,
        tissue=tissue,
        cell_type=cell_type,
        assay_type=assay_type,
        library_source=library_source,
        disease=disease,
        platform=platform,
        series=series,
        keyword=keyword,
        search_mode=search_mode,
        top_k=top_k,
        offset=offset,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    if search_mode == SearchMode.sql:
        strategy = SQLSearchStrategy(conn)
    elif search_mode == SearchMode.semantic:
        if vector_service is None:
            raise HTTPException(
                status_code=503, detail="Semantic search is not available"
            )
        strategy = SemanticSearchStrategy(vector_service, conn)
    elif search_mode == SearchMode.hybrid:
        if vector_service is None:
            raise HTTPException(
                status_code=503, detail="Semantic search is not available"
            )
        strategy = HybridSearchStrategy(vector_service, conn)

    svc = SearchService(strategy)
    return svc.search(filters)
