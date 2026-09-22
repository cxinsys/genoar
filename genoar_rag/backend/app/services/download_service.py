"""Assemble the data-access options for one sample.

What GENOAR hands over is what GENOAR made: the analysis-ready HDF5 its pipeline
produced, and the metadata it curated. The raw reads belong to the archive they
were deposited in, so they are linked to rather than offered — see
`_source_record`. Analysis-ready HDF5 comes from the pipeline's own output
directory, mounted read-only into the service.

That directory's layout is fixed by Stage 3 (see
srr_pipeline_package/pipeline/Snakefile_hs.smk, rule cellranger_count), which
writes each sample to <results>/<run_id>/cellranger_output/outs/. The files are
located by layout, so a listing answers which runs have output there.

A file being on disk is where the question starts. It is not where it ends.
Results live on a persistent volume that outlives any one run, so a matrix
sitting under the results directory says nothing by itself about which input
produced it. Stage 3 settled that for the pipeline by writing a receipt beside
each sample's output, and this service reads the same receipt before handing the
output over. See `sample_provenance`.

Nothing here requires a credential, which is deliberate: identifying users would
drag the service into an access-audit regime it does not need.
"""

import json
import logging
import os
from pathlib import Path
from urllib.parse import quote

from app.config import settings
from app.services.accession import geo_accession_for_run

logger = logging.getLogger(__name__)

# Where Stage 3 puts a sample's outputs, relative to the results directory.
SAMPLE_OUTS_SUBPATH = "{run_id}/cellranger_output/outs"

# Offerable outputs, in the order a client should prefer them. The first is
# what downstream analysis normally starts from; the other two are larger and
# only needed for re-analysis.
#
# Each description says what is in the file and nothing else. "Cell-filtered
# feature-barcode matrix" described a property of the contents and read as a
# selection GENOAR had made; naming the file instead put `filtered_` back on
# screen, which is the same word doing the same thing.
PROCESSED_FILES: tuple[tuple[str, str, str], ...] = (
    (
        "processed_h5",
        "filtered_feature_bc_matrix.h5",
        "Counts per gene for the barcodes called as cells.",
    ),
    (
        "raw_h5",
        "raw_feature_bc_matrix.h5",
        "Counts for every barcode, before cell calling.",
    ),
    (
        "molecule_info_h5",
        "molecule_info.h5",
        # Not "for Cell Ranger reanalyze", which this said and the READMEs
        # still did. `reanalyze` takes the filtered matrix; this file is what
        # `aggr` reads. Neither is stated now: no run in the corpus carries a
        # molecule_info.h5 to check against, and the description's job is to
        # say what is in the file rather than which command consumes it.
        "One record per detected molecule, before counts are summed per gene.",
    ),
)

DOWNLOADABLE_FILENAMES = frozenset(name for _, name, _ in PROCESSED_FILES)

NO_PROCESSED_SOURCE_NOTE = (
    "This deployment has no processed-data location configured "
    "(PROCESSED_RESULTS_DIR), so GENOAR cannot offer the *.h5 for this run."
)
NOT_PROCESSED_NOTE = "This run has not been processed by the pipeline."

# Provenance
#
# What stage 3 writes beside a sample's output, and what this service is
# entitled to say because of it. The vocabulary is the pipeline's; see
# srr_pipeline_package/pipeline/run_docker_pipeline.sh and its README.

# The receipt, in the run's own directory under the results directory.
RECEIPT_NAME = ".genoar_cellranger.json"

# The two things a receipt may say produced the output beside it. A receipt
# saying anything else was written by something this service does not know, and
# a reader may not act on a word whose meaning it is guessing at.
RECEIPT_FRESH = "fresh"
RECEIPT_ADOPTED = "adopted"

# What this service concluded, one per receipt case. See SampleProvenance.
PROVENANCE_VERIFIED = "verified"
PROVENANCE_ADOPTED = "adopted"
PROVENANCE_UNREADABLE = "unreadable"
PROVENANCE_UNRECORDED = "unrecorded"

VERIFIED_NOTE = (
    "A pipeline run produced this output from this sample's input and left a "
    "receipt saying so."
)

ADOPTED_NOTE = (
    "This output was adopted on an operator's instruction "
    "(GENOAR_ADOPT_PRIOR_RESULTS=1). No pipeline run has tied it to this "
    "sample's input, and the pipeline counts it towards nothing. GENOAR hands "
    "out only what the pipeline can account for, so the file is named here and "
    "withheld. Re-running stage 3 over the sample replaces the adoption with "
    "verified output."
)

UNREADABLE_RECEIPT_NOTE = (
    "The provenance receipt beside this output does not say what produced it. "
    "GENOAR cannot tell verified work from output taken on an operator's word, "
    "so the file is withheld. Re-running stage 3 over the sample writes a "
    "receipt this service can read."
)

# The legacy case, and the whole reason the switch exists. Both notes describe
# the same output. They differ in what this deployment does about it.
UNRECORDED_OFFERED_NOTE = (
    "This output carries no provenance receipt. It was produced before the "
    "pipeline recorded provenance, or it was produced elsewhere. This "
    "deployment offers such output. Set PROCESSED_RESULTS_REQUIRE_RECEIPT=true "
    "to withhold it."
)

UNRECORDED_WITHHELD_NOTE = (
    "This output carries no provenance receipt. It was produced before the "
    "pipeline recorded provenance, or it was produced elsewhere. This "
    "deployment requires a receipt (PROCESSED_RESULTS_REQUIRE_RECEIPT), so the "
    "file is withheld. Re-running stage 3 over the sample writes one."
)


def _layouts() -> tuple[str, ...]:
    """Where a run's outputs sit under the results directory.

    The built-in layout is what stage 3 writes, and it is always tried. A
    deployment whose processed data was written by another run of the
    pipeline — or delivered rather than produced — names its own patterns in
    PROCESSED_RESULTS_LAYOUTS, comma-separated, each containing `{run_id}`.
    That is configuration and not a code change because the shape of somebody
    else's directory is a fact about their disk, not about this service.
    """
    configured = getattr(settings, "processed_results_layouts", "") or ""
    extra = tuple(p.strip() for p in configured.split(",") if p.strip())
    return (SAMPLE_OUTS_SUBPATH, *extra)


def _run_id_parents() -> list[Path]:
    """The directories whose entries are run ids, one per layout.

    A layout names the run somewhere in its path; everything before that
    segment is a fixed prefix, so listing that prefix lists run ids.
    """
    root = Path(settings.processed_results_dir)
    parents = []
    for layout in _layouts():
        segments = layout.split("/")
        for i, segment in enumerate(segments):
            if "{run_id}" in segment:
                parents.append(root.joinpath(*segments[:i]))
                break
    return parents


def processed_run_ids() -> set[str]:
    """Every run with a directory under the results dir, from one listing.

    For callers working through many rows at once. Asking `processed_file_path`
    per row is a stat per row, and two hundred thousand stats over NFS is an
    export's whole budget spent discovering that most runs have nothing. Each
    layout has one level of run ids, so one listing per layout answers it for
    all of them.

    Empty when nothing is configured, which reads the same as nothing being
    processed — and is, as far as a caller is concerned.

    This answers where output is, and it does not ask where the output came
    from. That stays one receipt read per run, and it is asked by the functions
    that hand something over: `processed_file_path` and, through it,
    `processing_parameters`. A caller narrowing a corpus down with this set and
    then asking one of those for each survivor gets the provenance rule applied
    without paying for it on the runs that have no output at all.
    """
    if not settings.processed_results_dir:
        return set()
    found: set[str] = set()
    for parent in _run_id_parents():
        try:
            with os.scandir(parent) as entries:
                found |= {entry.name for entry in entries if entry.is_dir()}
        except OSError:
            continue
    return found


def sample_outs_dirs(run_id: str) -> list[Path]:
    """Where this run's outputs might be, in the order to look."""
    if not settings.processed_results_dir:
        return []
    root = Path(settings.processed_results_dir)
    return [root / layout.format(run_id=run_id) for layout in _layouts()]


def _run_dir(layout: str, run_id: str) -> Path:
    """The run's own directory under one layout.

    A layout names the run in one of its segments; that segment and everything
    before it is the run's directory, and everything after it is where the
    outputs sit inside it. Stage 3 writes the receipt in the run's directory,
    so this is where to look for one however deep the outputs are.
    """
    root = Path(settings.processed_results_dir)
    segments = layout.split("/")
    for i, segment in enumerate(segments):
        if "{run_id}" in segment:
            head = [s.format(run_id=run_id) for s in segments[: i + 1]]
            return root.joinpath(*head)
    return root / layout.format(run_id=run_id)


def _inside_results(path: Path) -> bool:
    """Whether a path resolves inside the results directory.

    The same containment rule the file route needs, asked of receipts too. A
    layout is configuration and configuration can be mistyped, and a receipt
    read from outside the results directory would decide what this service
    serves from it.
    """
    root = Path(settings.processed_results_dir).resolve()
    try:
        path.resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def _sample_location(run_id: str) -> tuple[Path, Path] | None:
    """(outputs directory, run directory) for this run, or None.

    The first layout that actually holds one of the known outputs is the one
    this run's data is in, so it is the one whose receipt speaks for it. A
    layout with a directory and no output in it is not this run's location; the
    search moves on, exactly as `processed_file_path` does.
    """
    if not settings.processed_results_dir:
        return None
    for layout in _layouts():
        outs = Path(settings.processed_results_dir) / layout.format(run_id=run_id)
        if not any((outs / name).is_file() for name in DOWNLOADABLE_FILENAMES):
            continue
        if not _inside_results(outs):
            continue
        return outs, _run_dir(layout, run_id)
    return None


def _read_receipt(run_dir: Path, outs: Path) -> tuple[str, dict]:
    """The receipt for one sample, as (state, payload).

    State is "absent" when there is no receipt, "unreadable" when there is one
    this service cannot act on, and "present" otherwise.

    Two places are looked in. Stage 3 writes the receipt in the run's own
    directory, which is the first. A deployment that copied a sample's outputs
    somewhere flatter may have brought the receipt along beside them, which is
    the second. The run directory wins where both exist, because that is the
    one the pipeline wrote.
    """
    seen = []
    for candidate in (run_dir / RECEIPT_NAME, outs / RECEIPT_NAME):
        if candidate in seen:
            continue
        seen.append(candidate)
        if not candidate.is_file():
            continue
        if not _inside_results(candidate):
            continue
        try:
            with open(candidate, "rb") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            logger.warning("Could not read the receipt at %s", candidate, exc_info=True)
            return "unreadable", {}
        if not isinstance(payload, dict):
            logger.warning("The receipt at %s is not a record", candidate)
            return "unreadable", {}
        return "present", payload
    return "absent", {}


def sample_provenance(run_id: str):
    """What the pipeline can account for about this run's output, or None.

    None when this deployment reads no results directory, and when the run has
    no output in it. Both mean there is nothing here to have provenance about.

    The four cases, and what each one gets:

    A receipt saying `fresh`. A run ran Cell Ranger over this input and its own
    completion record said so. The output is verified and is offered.

    A receipt saying `adopted`. An operator told a run to reuse output nobody
    could account for. The pipeline reports it and counts it towards nothing,
    and this service withholds it. Adoption is what lets a run proceed past
    output with no provenance. It has never been a finding about the output, so
    it cannot be the ground for publishing it, and passing it on would be the
    same claim the pipeline refuses to make.

    A receipt this service cannot act on. It does not parse, or it names a
    provenance this service does not know. Withheld. A word whose meaning is
    guessed at is worth less than no word: the guess could credit an adoption
    as verified work, which is the one mistake the receipt exists to prevent.

    No receipt. The output predates provenance tracking, or it came from
    somewhere else. Deployments serving such output exist, and making their
    results vanish would be a regression whatever the argument for it, so this
    is offered by default. A deployment whose results all come from runs that
    write receipts sets PROCESSED_RESULTS_REQUIRE_RECEIPT and gets the stricter
    reading.

    The adopted and unreadable cases need no switch. Neither can exist unless
    the pipeline that wrote them already writes receipts, so no deployment
    predating receipts holds either one.

    Read fresh each time. An operator who reprocesses a sample changes what the
    answer should be, and a cached answer would keep serving the old one.
    """
    from app.models.download import SampleProvenance

    location = _sample_location(run_id)
    if location is None:
        return None
    outs, run_dir = location
    state, receipt = _read_receipt(run_dir, outs)

    if state == "absent":
        offered = not settings.processed_results_require_receipt
        return SampleProvenance(
            status=PROVENANCE_UNRECORDED,
            verified=False,
            offered=offered,
            note=UNRECORDED_OFFERED_NOTE if offered else UNRECORDED_WITHHELD_NOTE,
        )

    if state == "unreadable":
        return SampleProvenance(
            status=PROVENANCE_UNREADABLE,
            verified=False,
            offered=False,
            note=UNREADABLE_RECEIPT_NOTE,
        )

    recorded = receipt.get("provenance")
    receipt_run_id = receipt.get("run_id")
    recorded_at = receipt.get("completed_at")
    # Only strings reach the response. A receipt is a file on disk and a field
    # in it can be any JSON value, and a number where a run id belongs would
    # fail the model rather than the sample.
    receipt_run_id = receipt_run_id if isinstance(receipt_run_id, str) else None
    recorded_at = recorded_at if isinstance(recorded_at, str) else None

    if recorded == RECEIPT_FRESH:
        return SampleProvenance(
            status=PROVENANCE_VERIFIED,
            verified=True,
            offered=True,
            receipt_run_id=receipt_run_id,
            recorded_at=recorded_at,
            note=VERIFIED_NOTE,
        )

    if recorded == RECEIPT_ADOPTED:
        return SampleProvenance(
            status=PROVENANCE_ADOPTED,
            verified=False,
            offered=False,
            receipt_run_id=receipt_run_id,
            recorded_at=recorded_at,
            note=ADOPTED_NOTE,
        )

    logger.warning(
        "The receipt for %s records a provenance this service does not know "
        "(%r); its output is withheld",
        run_id,
        recorded,
    )
    return SampleProvenance(
        status=PROVENANCE_UNREADABLE,
        verified=False,
        offered=False,
        receipt_run_id=receipt_run_id,
        recorded_at=recorded_at,
        note=UNREADABLE_RECEIPT_NOTE,
    )


def _locate(run_id: str, filename: str) -> Path | None:
    """Find one output file on disk, without asking where it came from.

    `filename` is checked against the known outputs rather than joined blindly,
    and the resolved path has to land inside the results directory, so neither
    a caller nor a mistyped layout can walk out of it.
    """
    if filename not in DOWNLOADABLE_FILENAMES:
        return None
    if not settings.processed_results_dir:
        return None
    for outs in sample_outs_dirs(run_id):
        path = outs / filename
        if not path.is_file():
            continue
        if not _inside_results(path):
            continue
        return path
    return None


def processed_file_path(run_id: str, filename: str) -> Path | None:
    """Resolve one output file, or None if it is not on offer.

    Two questions, and both have to be answered before a file is handed over:
    where it is, and whether the pipeline can account for it. The file route
    resolves URLs through here, so a sample whose output the manifest withholds
    is not reachable by typing its filename either.
    """
    if filename not in DOWNLOADABLE_FILENAMES:
        return None
    provenance = sample_provenance(run_id)
    if provenance is None or not provenance.offered:
        return None
    return _locate(run_id, filename)


def _source_record(conn, run_id: str):
    """Where the run came from, as a link to the archive's own page.

    Not a file, and not a download button. The data was deposited in GEO by its
    authors under the archive's terms, so GENOAR has no right to redistribute it
    or to present it as its own — a "Raw reads (SRA)" button pointing at NCBI's
    public bucket would be somebody else's data wearing GENOAR's chrome.

    The record page is the honest form of the same offer, and the more useful
    one: it carries the study, the terms and every format the archive has, where
    a bucket URL carries tens of gigabytes and nothing else.
    """
    from app.models.download import DownloadSource

    geo = geo_accession_for_run(conn, run_id)
    if geo:
        return DownloadSource(
            kind="geo_record",
            available=True,
            action="visit",
            url=settings.geo_record_url_template.format(accession=geo),
            description=(
                f"The GEO record this sample was deposited in ({geo}), "
                "where the raw data and its terms of use are held."
            ),
        )
    # Only part of the corpus carries a GSM, so the run's own archive record is
    # the fallback rather than an error.
    return DownloadSource(
        kind="sra_record",
        available=True,
        action="visit",
        url=settings.sra_record_url_template.format(run_id=run_id),
        description=(
            "The SRA record for this run, where the raw reads are held by the "
            "public archive."
        ),
    )


def _processed_sources(run_id: str, dataset: str | None = None) -> list:
    from app.models.download import DownloadSource

    # The file route answers for the dataset the URL names, so a manifest
    # built for one dataset must say which — otherwise its links resolve
    # against the default. A single-dataset deployment passes None and its
    # URLs stay what they always were.
    query = f"?dataset={quote(dataset)}" if dataset else ""
    if settings.processed_results_dir:
        provenance = sample_provenance(run_id)
        # Nothing on disk for this run: it was not processed. One row, and not
        # one per file, because there is no file. Provenance is None only where
        # no known output was found under any layout, so enumerating would find
        # nothing to name. The row says the processed output is missing, under
        # the name of the output a caller would have asked for.
        if provenance is None:
            return [
                DownloadSource(
                    kind="processed_h5",
                    available=False,
                    description=PROCESSED_FILES[0][2],
                    note=NOT_PROCESSED_NOTE,
                )
            ]
        # There is output and the pipeline cannot account for it. Each file is
        # reported unavailable with the reason, which is the shape this endpoint
        # already uses for a source that exists in principle and has no URL. A
        # caller learns which files the run holds and that GENOAR will not hand
        # them over, which is more than silence tells them and more honest than a
        # download button.
        #
        # Enumerated the way the offered branch enumerates. A run holding a raw
        # matrix as well is withholding two files, and one row headed as the
        # preprocessed matrix understated that. A run holding only a raw matrix
        # got a row naming a file it does not have.
        if not provenance.offered:
            # `_locate` is the only way to ask here. `processed_file_path`
            # answers None for every file on a run this service withholds, which
            # is the point of it, so it cannot say what the run holds.
            withheld = []
            for kind, filename, description in PROCESSED_FILES:
                if _locate(run_id, filename) is None:
                    continue
                withheld.append(
                    DownloadSource(
                        kind=kind,
                        available=False,
                        description=description,
                        note=provenance.note,
                        provenance=provenance.status,
                    )
                )
            if withheld:
                return withheld
            # Provenance is decided only where output was found, so reaching
            # here means the two reads disagreed. A file resolving outside the
            # results directory does it, because provenance checks containment
            # on the directory and `_locate` checks it on the file. Output
            # removed while the request was in flight does it too. Either way
            # the withholding stays on screen under the preferred output's name,
            # which is what this endpoint says when there is no file to name,
            # and the disagreement is worth a line in the log.
            logger.warning(
                "%s has provenance %s and no output this service can resolve; "
                "its withheld source names the preferred output instead",
                run_id,
                provenance.status,
            )
            return [
                DownloadSource(
                    kind="processed_h5",
                    available=False,
                    description=PROCESSED_FILES[0][2],
                    note=provenance.note,
                    provenance=provenance.status,
                )
            ]
        sources = []
        for kind, filename, description in PROCESSED_FILES:
            # `_locate` rather than `processed_file_path`: provenance was
            # decided once above and holds for every file in the directory.
            path = _locate(run_id, filename)
            if path is None:
                continue
            sources.append(
                DownloadSource(
                    kind=kind,
                    available=True,
                    url=f"/api/v1/samples/{run_id}/files/{filename}{query}",
                    size_bytes=path.stat().st_size,
                    description=description,
                    provenance=provenance.status,
                )
            )
        if sources:
            return sources
        return [
            DownloadSource(
                kind="processed_h5",
                available=False,
                description=PROCESSED_FILES[0][2],
                note=NOT_PROCESSED_NOTE,
            )
        ]

    # Files on another host. Nothing about them can be checked from here, so
    # nothing about them is claimed: the source carries no provenance either
    # way, and PROCESSED_RESULTS_REQUIRE_RECEIPT has no bearing on it.
    template = settings.processed_h5_url_template
    return [
        DownloadSource(
            kind="processed_h5",
            available=bool(template),
            url=template.format(run_id=run_id) if template else None,
            description=PROCESSED_FILES[0][2],
            note=None if template else NO_PROCESSED_SOURCE_NOTE,
        )
    ]


def _text(value) -> str | None:
    """One HDF5 string as text. They come back as bytes, or as arrays of one."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace") or None
    if isinstance(value, str):
        return value or None
    # numpy scalars and single-element arrays
    try:
        return _text(value.item() if hasattr(value, "item") else value[0])
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


_h5py = None
_h5py_looked_for = False


def _hdf5_reader():
    """h5py, or None with a note in the log saying why there is none.

    Looked up once. It is a declared dependency, so its absence means an image
    built against an older pyproject — which is worth saying out loud exactly
    once, rather than per request and rather than not at all. Not at all is what
    happened: a deployment whose image predated the dependency served empty
    parameters for every sample with no sign anywhere of why.
    """
    global _h5py, _h5py_looked_for
    if not _h5py_looked_for:
        _h5py_looked_for = True
        try:
            import h5py

            _h5py = h5py
        except ImportError:
            logger.warning(
                "h5py is not installed: no preprocessing parameters will be "
                "reported. The image needs rebuilding against pyproject.toml."
            )
    return _h5py


def processing_parameters(run_id: str):
    """How this run's matrix was made, read from the matrix.

    Cell Ranger writes the version and the detected chemistry into the file's
    root attributes and the reference name alongside the features, so a matrix
    describes its own making. That is worth more than the same three values
    written down beside it: the pipeline's own `_cmdline` and `_versions` were
    cleared from this deployment's results, and a note kept by hand would have
    gone stale the first time anything was reprocessed.

    None when there is no matrix for the run, or when it cannot be read — the
    parameters are worth having and not worth failing a download page over.

    None too when the matrix is one the service will not hand over, because
    `processed_file_path` is what resolves it. Describing how a file was made
    while refusing to hand it over would be the same overclaim in a quieter
    place: the export's preprocessing columns would say a sample was processed
    by GENOAR when the pipeline cannot account for it.
    """
    from app.models.download import ProcessingParameters

    path = processed_file_path(run_id, PROCESSED_FILES[0][1])
    if path is None:
        return None

    h5py = _hdf5_reader()
    if h5py is None:
        return None

    try:
        with h5py.File(path, "r") as f:
            genome = f.get("matrix/features/genome")
            return ProcessingParameters(
                software=_text(f.attrs.get("software_version")),
                chemistry=_text(f.attrs.get("chemistry_description")),
                # One value per feature, all the same for a single-reference
                # run, so the first stands for the set.
                reference=_text(genome[0]) if genome is not None and len(genome) else None,
            )
    except Exception:  # noqa: BLE001 - a bad file must not take the page with it
        logger.warning("Could not read parameters from %s", path, exc_info=True)
        return None


def build_sources(conn, run_id: str, dataset: str | None = None) -> list:
    """A run's sources, in the order a client should prefer them.

    The processed matrices first: they are what GENOAR actually holds and what
    an analysis starts from. The archive record last, because it is a pointer
    somewhere else rather than something on offer here.
    """
    return [*_processed_sources(run_id, dataset), _source_record(conn, run_id)]


def build_response(
    conn,
    run_id: str,
    requested_accession: str | None = None,
    dataset: str | None = None,
):
    from app.models.download import SampleDownloadResponse

    return SampleDownloadResponse(
        run_id=run_id,
        geo_accession=geo_accession_for_run(conn, run_id),
        requested_accession=requested_accession,
        processing=processing_parameters(run_id),
        # Reported whether or not the output is offered, and reported for
        # verified output too. A caller that can see only what it was given
        # cannot tell a withheld sample from an unprocessed one, and cannot tell
        # verified output from output this deployment happens to serve. The
        # service still decides; it says what it decided on.
        provenance=sample_provenance(run_id),
        sources=build_sources(conn, run_id, dataset),
    )
