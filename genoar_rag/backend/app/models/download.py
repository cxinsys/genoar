"""Models for the per-sample data access endpoint."""

from pydantic import BaseModel


class DownloadSource(BaseModel):
    """One way to obtain the data for a sample.

    `available` is False when GENOAR knows the source exists in principle but has
    no URL to hand out (see `note`), so a client can tell "not offered here" apart
    from "offered but empty".

    `action` says what the URL is. "download" is a file GENOAR serves or a direct
    object URL; "visit" is somebody else's web page, where the data lives under
    their terms rather than ours. A client that renders both as a download button
    claims to be handing over data it does not hold.
    """

    kind: str
    available: bool
    action: str = "download"
    url: str | None = None
    size_bytes: int | None = None
    description: str | None = None
    note: str | None = None
    # What the pipeline can account for about the file behind this source, as
    # one of SampleProvenance.status. None on a source that is not pipeline
    # output, and on a source read from another host, where there is nothing to
    # check. Repeated here so a client reading only the sources list can tell
    # verified output from the rest without holding the response beside it.
    provenance: str | None = None


class ProcessingParameters(BaseModel):
    """How the matrix on offer was produced.

    Read out of the HDF5 itself rather than recorded beside it. Cell Ranger
    writes these into every matrix it makes, so they cannot drift from the file
    they describe, and a run reprocessed with a different version says so
    without anyone having to remember to update a note.

    Each field is None when the file does not carry it — an older Cell Ranger,
    or a matrix from somewhere else.
    """

    software: str | None = None
    chemistry: str | None = None
    reference: str | None = None


class SampleProvenance(BaseModel):
    """What the pipeline can account for about this run's Cell Ranger output.

    Stage 3 leaves a receipt beside each sample's output saying what produced
    it. This is that receipt as the service read it, reported so a caller can
    tell verified output from output nobody has verified. The service also acts
    on it, and `offered` says which way it acted.

    `status` is one of:

    verified    A run's own Cell Ranger produced the output and recorded it.
                The receipt says `fresh`.
    adopted     An operator told a run to reuse output nobody could account for
                (GENOAR_ADOPT_PRIOR_RESULTS=1). The receipt says `adopted`. No
                run has tied the output to the input it claims to come from.
    unreadable  A receipt is there and this service cannot act on it. It does
                not parse, or it does not say what produced the output.
    unrecorded  There is no receipt. The output predates provenance tracking, or
                it was produced somewhere other than this pipeline.

    `verified` is the same fact as `status == "verified"`, in the form a client
    can branch on without knowing the vocabulary.
    """

    status: str
    verified: bool
    # Whether the service hands the files out. Verified output is always
    # offered. Unrecorded output is offered unless the deployment sets
    # PROCESSED_RESULTS_REQUIRE_RECEIPT. Adopted and unreadable are never
    # offered.
    offered: bool
    # The run named on the receipt as having produced or adopted the output.
    # None when there is no readable receipt.
    receipt_run_id: str | None = None
    # When the receipt was written, as the receipt states it.
    recorded_at: str | None = None
    note: str


class SampleDownloadResponse(BaseModel):
    run_id: str
    geo_accession: str | None = None
    requested_accession: str | None = None
    # Present when this run has a processed matrix to read them from.
    processing: ProcessingParameters | None = None
    # Present when this deployment reads its processed data off a mounted
    # results directory and this run has output there. None when there is
    # nothing on disk for the service to have read a receipt beside.
    provenance: SampleProvenance | None = None
    # Stated explicitly so an agent reading the response knows no credential step
    # is needed before following the URLs.
    requires_api_key: bool = False
    sources: list[DownloadSource] = []
