"""Sample endpoints."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.dependencies import get_db, get_dataset, get_vector_service
from app.models.common import PaginatedResponse
from app.models.download import SampleDownloadResponse
from app.models.sample import SampleDetail, SampleSummary
from app.models.semantic import SimilarSampleItem, SimilarSamplesResponse
from app.services import accession as accession_svc
from app.services import download_service
from app.services.sample_service import SampleService
from app.services.vector_router import VectorRouter

router = APIRouter(prefix="/api/v1/samples", tags=["samples"])

# Every {accession} path segment takes either an SRA run id or a GEO sample id,
# so a caller holding only a GSM can reach the same resources.


@router.get("/resolve/{accession}", response_model=PaginatedResponse[SampleSummary])
def resolve_accession(
    accession: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Every sample an accession names, rather than the one it resolves to.

    A GSM is a biological sample and a run is a sequencing run of it, so the two
    are not one-to-one: 1,268 of the 3,919 GSMs in the corpus name more than one
    run, up to twelve. The single-sample routes pick the first by run id so their
    answer is stable, which means eleven of those twelve were unreachable from an
    accession — the lookup opened one and said nothing about the rest.

    Ordered by run id, the same order the single-sample routes pick from, so the
    first row here is the sample those routes would have opened.
    """
    accession = (accession or "").strip()
    # The same shape check the other accession routes apply through resolve():
    # a value that is not a run id or a GSM is not looked up, it is refused.
    if not accession_svc.is_well_formed(accession):
        raise HTTPException(status_code=404, detail=f"No sample for {accession}")
    svc = SampleService(conn)

    if accession_svc.looks_like_geo_sample(accession):
        run_ids = accession_svc.runs_for_geo_sample(conn, accession)
    else:
        # A run id names itself. Resolving it through the lookup would be a
        # round trip to learn what the caller already typed.
        run_ids = [accession] if accession else []

    items = svc.get_summaries_for_run_ids(run_ids)
    by_run = {item.run_id: item for item in items}
    ordered = [by_run[r] for r in run_ids if r in by_run]
    if not ordered:
        raise HTTPException(status_code=404, detail=f"No sample for {accession}")
    return PaginatedResponse(
        items=ordered, total=len(ordered), offset=0, limit=len(ordered)
    )


@router.get("", response_model=PaginatedResponse[SampleSummary])
def list_samples(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    dataset: str = Depends(get_dataset),
    conn: sqlite3.Connection = Depends(get_db),
):
    svc = SampleService(conn)
    items, total = svc.list_samples(limit=limit, offset=offset)
    return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/{accession}", response_model=SampleDetail)
def get_sample(
    accession: str,
    dataset: str = Depends(get_dataset),
    conn: sqlite3.Connection = Depends(get_db),
):
    svc = SampleService(conn)
    return svc.get_sample_detail(accession_svc.resolve(conn, accession))


@router.get("/{accession}/download", response_model=SampleDownloadResponse)
def get_sample_download(
    accession: str,
    dataset: str = Depends(get_dataset),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Where to obtain this sample's data. No credential is required."""
    from app.dependencies import get_registry

    run_id = accession_svc.resolve(conn, accession)
    # Resolving a GSM proves the run exists; a run id given directly does not, so
    # confirm it before advertising URLs for a sample GENOAR does not hold.
    SampleService(conn).get_sample_detail(run_id)
    # The file URLs in the manifest must keep naming this dataset — the file
    # route answers for whichever the URL says. A single-dataset deployment
    # passes None and keeps its unqualified URLs.
    registry = get_registry()
    return download_service.build_response(
        conn,
        run_id,
        requested_accession=accession,
        dataset=dataset if registry.is_split else None,
    )


@router.get("/{accession}/files/{filename}")
def get_sample_file(
    accession: str, filename: str, conn: sqlite3.Connection = Depends(get_db)
):
    """Stream one of the sample's Cell Ranger outputs from the mounted results dir.

    Only the pipeline's known output filenames resolve, so the path cannot be
    steered outside the results directory.
    """
    run_id = accession_svc.resolve(conn, accession)
    # The results directory on disk is global, so the run must be this
    # dataset's before its files are served under this dataset's name —
    # resolving an SRR checks nothing by itself.
    SampleService(conn).get_sample_detail(run_id)
    path = download_service.processed_file_path(run_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No such file for {run_id}: {filename}")
    return FileResponse(path, media_type="application/octet-stream", filename=f"{run_id}_{filename}")


@router.get("/{accession}/series", response_model=PaginatedResponse[SampleSummary])
def get_series_samples(
    accession: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    dataset: str = Depends(get_dataset),
    conn: sqlite3.Connection = Depends(get_db),
):
    svc = SampleService(conn)
    # Across every study the run belongs to. Reading one series off the sample
    # and asking for its members answered for one of the two when there were two,
    # so half the study's samples were missing from the panel with nothing to say
    # so.
    run_id = accession_svc.resolve(conn, accession)
    items, total = svc.get_sibling_samples(run_id, limit=limit, offset=offset)
    return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/{accession}/similar", response_model=SimilarSamplesResponse)
def get_similar_samples(
    accession: str,
    limit: int = Query(default=10, ge=1, le=100),
    dataset: str = Depends(get_dataset),
    conn: sqlite3.Connection = Depends(get_db),
    vector_service: VectorRouter | None = Depends(get_vector_service),
):
    if vector_service is None:
        raise HTTPException(status_code=503, detail="Semantic search is not available")

    run_id = accession_svc.resolve(conn, accession)
    svc = SampleService(conn)

    # The sample has to be here before its neighbours can be asked for. A run
    # this dataset does not hold answered 200 with a list of neighbours, so a
    # page that should not exist rendered.
    svc.get_sample_detail(run_id)

    # No over-fetching and nothing to drop afterwards: this dataset's index was
    # built over this dataset, so every neighbour it returns is one this
    # dataset holds.
    results = vector_service.search_similar_by_id(run_id, top_k=limit)

    summaries = svc.get_summaries_for_run_ids([r.run_id for r in results])
    summary_map = {s.run_id: s for s in summaries}

    # A neighbour with no row is dropped rather than returned with every field
    # but its id null, which is how an index that has drifted from its database
    # would otherwise show up: a run id and a score, and nothing to say what it
    # is.
    items = [
        SimilarSampleItem(
            run_id=r.run_id,
            similarity_score=r.score,
            tissue=s.tissue,
            cell_type=s.cell_type,
            disease=s.disease,
            organism=s.organism,
            assay_type=s.assay_type,
        )
        for r in results
        if (s := summary_map.get(r.run_id)) is not None
    ]

    return SimilarSamplesResponse(
        query_run_id=run_id,
        items=items,
        total=len(items),
    )
