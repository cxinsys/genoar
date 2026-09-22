"""Filter options endpoints."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_db, get_dataset
from app.models.filters import FilterCategoryPage, FilterOptionsResponse
from app.services.filter_catalog import category_names, get_category_spec
from app.services.filter_service import DEFAULT_PAGE_SIZE, FilterService

router = APIRouter(prefix="/api/v1", tags=["filters"])


@router.get("/filters", response_model=FilterOptionsResponse)
def get_filters(
    dataset: str = Depends(get_dataset),
    conn: sqlite3.Connection = Depends(get_db),
):
    """The opening page of every category, for building the filter UI."""
    svc = FilterService(conn, dataset)
    return svc.get_filter_options()


@router.get("/filters/{category}", response_model=FilterCategoryPage)
def get_filter_category(
    category: str,
    q: str | None = Query(default=None, description="Substring match on the value"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=200),
    dataset: str = Depends(get_dataset),
    conn: sqlite3.Connection = Depends(get_db),
):
    """A page of one category's values.

    Categories run to thousands of values — tissue alone has over two thousand —
    so `/filters` returns only the head of each. This is how a client reaches the
    rest: page through with offset, or narrow with `q` instead of paging at all.
    """
    spec = get_category_spec(category)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown filter category: {category}. "
            f"Valid categories: {', '.join(category_names())}",
        )
    svc = FilterService(conn, dataset)
    return svc.get_category_page(spec, limit=limit, offset=offset, query=q)
