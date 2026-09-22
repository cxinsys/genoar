"""Export the curated core metadata for a filtered set of samples.

The GENOAR paper describes the download section as carrying "core metadata
including cell types, tissues and disease related fields", and states that the
metadata retrieved from GEO is excluded from it for copyright reasons. That makes
the column set an allowlist rather than a dump: identifiers, the archive's own
technical fields, and GENOAR's normalized annotations go out; the submitter's
free-text fields and the whole `sra_extended` table do not.

Rows are produced in pages so a large export never materializes the full result
set in memory, and the caller streams them out.
"""

from app.models.search import SearchFilters
from app.repositories.extended_repository import ExtendedRepository
from app.repositories.sample_repository import SampleRepository
from app.repositories.search_repository import SearchRepository
from app.services import download_service

# Enrichment fields sourced from sra_extended. These describe the run itself as
# recorded by the archive, not free text written by the GEO submitter.
_ENRICHED = ("Organism", "Assay Type", "LibrarySource")

# (output column, sra_core column or enriched field). Ordered for readability.
EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "Run"),
    ("series", "Series"),
    ("biosample", "BioSample"),
    ("organism", "Organism"),
    ("assay_type", "Assay Type"),
    ("library_source", "LibrarySource"),
    ("platform", "Platform"),
    ("instrument", "Instrument"),
    # GENOAR's normalized annotations
    ("tissue", "tissue"),
    ("cell_type", "cell_type"),
    ("disease", "disease_state_modified"),
    ("umls_tissue_term", "STR_tis"),
    ("umls_tissue_cui", "CUI_tis"),
    ("umls_cell_type_term", "STR_cell"),
    ("umls_cell_type_cui", "CUI_cell"),
    ("umls_disease_term", "STR_dis"),
    ("umls_disease_cui", "CUI_dis"),
)

# How the matrix was made, for the runs that have one. Last, because they are
# about the processed file rather than about the sample, and a reader scanning
# the sheet meets the sample's own fields first.
#
# Prefixed rather than named for the tool that happens to produce them today.
# `cellranger_version` would be a column that lies the moment anything else
# writes a matrix, and the value already says which software it was.
PROCESSING_COLUMNS = (
    "preprocessing_software",
    "preprocessing_chemistry",
    "preprocessing_reference",
)

HEADERS = tuple(name for name, _ in EXPORT_COLUMNS) + PROCESSING_COLUMNS

# Deliberately withheld: Sample_Name, treatment, sex, Age, strain, genotype and
# every sra_extended field beyond _ENRICHED. These carry the submitter's own
# description of the sample, which the paper excludes from the download section.

# How many rows are held at once to have their extended fields looked up. Large
# enough that the lookup is a handful of queries per thousand rows rather than
# one per row, small enough that the export never holds much.
#
# A chunk size and not a page size, which is the distinction that matters here:
# the rows come from one query streamed through a cursor, so this only sets how
# many are held at a time. Re-running the search per chunk with a growing OFFSET
# would instead make the database scan and discard everything already emitted on
# every chunk — quadratic, and minutes rather than seconds over a large export.
CHUNK_SIZE = 500


def iter_rows(
    conn,
    filters: SearchFilters,
    max_rows: int,
    run_ids: list[str] | None = None,
):
    """Yield export dicts for `filters`, at most `max_rows` of them.

    `run_ids` narrows the export to specific runs, which is how a single sample's
    metadata is exported alongside the filter-driven case. When it is given, the
    rows come back **in that order**: an AI search hands over its candidates
    ranked by relevance, and reading them back with SQL `IN` returned them by run
    id — the same twenty samples as the screen, in a different order, under a
    button that said "download these results".

    That ordering buffers the rows, so it is used only where the caller has
    already bounded the set: one accession, or a semantic candidate pool that
    `top_k` caps at 500.
    """
    if run_ids:
        ordered = {run: i for i, run in enumerate(run_ids)}
        rows = list(
            _iter_rows_unordered(conn, filters, max_rows, run_ids)
        )
        rows.sort(key=lambda r: ordered.get(r["run_id"], len(ordered)))
        yield from rows
        return
    yield from _iter_rows_unordered(conn, filters, max_rows, run_ids)


def _iter_rows_unordered(
    conn,
    filters: SearchFilters,
    max_rows: int,
    run_ids: list[str] | None = None,
):
    """`iter_rows` without the reordering, so it can stream."""
    repo = SearchRepository(conn)
    ext_repo = ExtendedRepository(conn)
    sample_repo = SampleRepository(conn)
    emitted = 0

    # Read once, not per row. Most runs have no matrix, and finding that out by
    # asking the filesystem about each of them in turn is the cost of the whole
    # export. The set is names only, so it stays small even for a corpus where
    # everything has been processed.
    processed = download_service.processed_run_ids()

    for rows in repo.iter_search(
        filters, limit=max_rows, run_id_in=run_ids, chunk=CHUNK_SIZE
    ):
        chunk_run_ids = [r["Run"] for r in rows]
        ext_map: dict[str, dict] = {}
        for field_name in _ENRICHED:
            for rec in ext_repo.get_fields_for_runs(chunk_run_ids, field_name):
                ext_map.setdefault(rec["Run"], {})[field_name] = rec["field_value"]

        # Every study each run belongs to, not the one `sra_core.Series` kept.
        # 870 runs are in two, and a download that named one of them lost a
        # relation the curated tables have — the sheet would have said a sample
        # belonged to a single study when it belongs to two.
        series_map = sample_repo.series_for_runs(chunk_run_ids)

        for row in rows:
            if emitted >= max_rows:
                return
            run = row["Run"]
            enriched = ext_map.get(run, {})
            out = {
                name: (enriched.get(src) if src in _ENRICHED else row.get(src))
                for name, src in EXPORT_COLUMNS
            }
            # Semicolon-separated so the cell stays one field in every reader;
            # a comma would need quoting and splits badly when somebody opens
            # the sheet in a tool that re-parses it.
            out["series"] = ";".join(series_map.get(run, [])) or row.get("Series")
            # Opened only for the runs that have one. The file is where these
            # values live — see download_service.processing_parameters.
            params = (
                download_service.processing_parameters(run) if run in processed else None
            )
            out["preprocessing_software"] = params.software if params else None
            out["preprocessing_chemistry"] = params.chemistry if params else None
            out["preprocessing_reference"] = params.reference if params else None
            yield out
            emitted += 1


def iter_csv(
    conn,
    filters: SearchFilters,
    max_rows: int,
    run_ids: list[str] | None = None,
):
    """Yield the export as CSV text chunks, header first."""
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(HEADERS), extrasaction="ignore")
    writer.writeheader()
    yield buffer.getvalue()

    for row in iter_rows(conn, filters, max_rows, run_ids):
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerow(row)
        yield buffer.getvalue()
