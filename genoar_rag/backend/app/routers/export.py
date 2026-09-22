"""Bulk export of curated core metadata.

Mirrors the /search filters so a result set seen in the UI can be exported as it
stands. CSV streams; JSON is materialized because it has to be one document.
"""

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.dependencies import get_db, get_dataset, get_vector_service
from app.models.common import SortOrder
from app.models.search import SearchFilters, SearchMode
from app.services import accession as accession_svc
from app.services import export_service
from app.services.search_service import SemanticSearchStrategy
from app.services.vector_router import VectorRouter

router = APIRouter(prefix="/api/v1", tags=["export"])

# Above the corpus with room to grow. The point of this endpoint is to hand over
# the filtered set as it stands, and a ceiling under the corpus size means the
# one export nobody can do is the whole of it.
MAX_ROWS_CEILING = 250_000


@router.get("/export")
def export_metadata(
    organism: list[str] = Query(default=[]),
    tissue: list[str] = Query(default=[]),
    cell_type: list[str] = Query(default=[]),
    assay_type: list[str] = Query(default=[]),
    library_source: list[str] = Query(default=[]),
    disease: list[str] = Query(default=[]),
    platform: list[str] = Query(default=[]),
    series: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    accession: str | None = Query(
        default=None,
        description="Export a single sample by SRA run id or GEO sample id. Other filters are ignored.",
    ),
    fmt: str = Query(default="csv", pattern="^(csv|json)$", alias="format"),
    # Everything the filters match, unless the caller asks for less. It defaulted
    # to 10,000, which is a fifth of the corpus and arrived as a complete-looking
    # CSV with no indication that anything had been left out — the export card in
    # the UI passed no max_rows at all, so that was the export everyone got.
    max_rows: int = Query(default=MAX_ROWS_CEILING, ge=1, le=MAX_ROWS_CEILING),
    # None means the search's own order: run id for SQL, relevance for AI.
    sort_by: str | None = Query(default=None),
    sort_order: SortOrder = Query(default=SortOrder.asc),
    search_mode: SearchMode = Query(default=SearchMode.sql),
    # le=500 is SEMANTIC_POOL_SIZE in services/search_service.py, as in
    # routers/search.py.
    top_k: int | None = Query(default=None, ge=1, le=500),
    dataset: str = Depends(get_dataset),
    conn: sqlite3.Connection = Depends(get_db),
    vector_service: VectorRouter | None = Depends(get_vector_service),
):
    """Export the filtered samples' core metadata. No credential is required."""
    # A single accession addresses one sample directly, so the filter set is not
    # applied on top of it.
    run_ids = [accession_svc.resolve(conn, accession)] if accession else None

    # An AI search has to be exported by the searcher that produced it. The
    # keyword is a question in English, and this endpoint's own path matches
    # keywords with SQL LIKE — so a semantic search for "idiopathic parkinson
    # disease" exported as a LIKE and came back empty while the page showed
    # twenty samples.
    #
    # Whenever the caller says the search was semantic, not only when a keyword
    # came with it. Requiring one meant a semantic request without a keyword fell
    # through to the unfiltered SQL export, so a page showing zero results linked
    # to a download of all 6,199 samples. `candidate_run_ids` answers empty for an
    # empty query, which is what the page does.
    if run_ids is None and search_mode != SearchMode.sql:
        if vector_service is None:
            raise HTTPException(status_code=503, detail="Semantic search is not available")
        semantic = SemanticSearchStrategy(vector_service, conn)
        # Possibly empty, and left that way. An empty restriction means nothing
        # matched, which the query builder renders as `1 = 0`; falling back to
        # None here would export the whole corpus instead.
        run_ids = semantic.candidate_run_ids(
            SearchFilters(
                organism=organism,
                tissue=tissue,
                cell_type=cell_type,
                assay_type=assay_type,
                library_source=library_source,
                disease=disease,
                platform=platform,
                series=series,
                keyword=keyword,
                top_k=top_k,
                sort_by=sort_by,
                sort_order=sort_order,
            )
        )

    # `is not None`, not truthiness: once a run set has been decided — by an
    # accession or by the AI search above — it is the answer, including when it
    # is empty. The filters that produced it must not be applied a second time,
    # and a keyword that is a question must not reach the SQL matcher.
    fixed = run_ids is not None
    filters = SearchFilters(
        organism=[] if fixed else organism,
        tissue=[] if fixed else tissue,
        cell_type=[] if fixed else cell_type,
        assay_type=[] if fixed else assay_type,
        library_source=[] if fixed else library_source,
        disease=[] if fixed else disease,
        platform=[] if fixed else platform,
        series=None if fixed else series,
        keyword=None if fixed else keyword,
        offset=0,
        # No limit: the model's paging does not apply to an export, which asks
        # for the whole set in one query and streams it. `max_rows` is the only
        # bound.
        sort_by=sort_by,
        sort_order=sort_order,
    )

    # Name the file after the sample when one was asked for, so several
    # single-sample exports do not all land as genoar_metadata.csv.
    stem = f"genoar_{run_ids[0]}" if run_ids else "genoar_metadata"

    if fmt == "json":
        rows = list(export_service.iter_rows(conn, filters, max_rows, run_ids))
        body = json.dumps({"count": len(rows), "columns": list(export_service.HEADERS), "items": rows})
        return StreamingResponse(
            iter([body]),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{stem}.json"'},
        )

    return StreamingResponse(
        export_service.iter_csv(conn, filters, max_rows, run_ids),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'},
    )
