"""Resolve a public accession to the run id GENOAR stores samples under.

Callers arrive with whatever accession they have. SRA run ids (SRR/ERR/DRR)
address `sra_core.Run` directly. GEO sample ids (GSM) do not and have to be
looked up.

Which column holds the GSM changed with the curated rebuild. The original crawl
scraped it into `sra_extended` under `GEO_Accession (exp)`, for 2,245 of the
6,199 runs. The filtered tables carry it as `Sample Name`, for all 6,199 — so
the rebuild both improved the coverage and moved the value, and a resolver
reading only the old name found nothing at all. Both are read, the curated field
first; see `AccessionRepository`.
"""

import re

from app.exceptions import SampleNotFoundError
from app.repositories.accession_repository import (
    GEO_ACCESSION_FIELDS,
    AccessionRepository,
)

RUN_PREFIXES = ("SRR", "ERR", "DRR")
GEO_SAMPLE_PREFIX = "GSM"

# The shapes an accession can take: a prefix and digits, nothing else. Anything
# else is refused before it reaches a query or a response header — the export
# names its file after the run id, and a run id that is not one of these is
# not a run id.
_RUN_ID = re.compile(r"^(SRR|ERR|DRR)\d+$", re.IGNORECASE)
_GEO_SAMPLE_ID = re.compile(r"^GSM\d+$", re.IGNORECASE)


def is_well_formed(accession: str) -> bool:
    """Whether `accession` has the shape of a run id or a GEO sample id."""
    return bool(_RUN_ID.match(accession) or _GEO_SAMPLE_ID.match(accession))


def looks_like_run_id(accession: str) -> bool:
    return accession.upper().startswith(RUN_PREFIXES)


def looks_like_geo_sample(accession: str) -> bool:
    return accession.upper().startswith(GEO_SAMPLE_PREFIX)


def geo_accession_for_run(conn, run_id: str) -> str | None:
    """The GSM recorded for a run.

    Only values that look like a GSM are returned. `Sample Name` is the
    submitter's own label as often as it is an accession — 15 runs call it
    `L1-1` and the like — and those are not accessions to link out with.
    """
    found = AccessionRepository(conn).geo_accessions_for_run(run_id)
    for field in GEO_ACCESSION_FIELDS:
        value = (found.get(field) or "").strip()
        if looks_like_geo_sample(value):
            return value
    return None


def runs_for_geo_sample(conn, accession: str) -> list[str]:
    """Every run recorded under a GSM, in a stable order.

    Callers that need a single run take the first; the whole list is here so a
    caller that wants to offer the choice can.
    """
    return AccessionRepository(conn).runs_for_geo_sample(accession)


def resolve(conn, accession: str) -> str:
    """Return the run id for `accession`, raising SampleNotFoundError if unknown.

    Where a GEO sample spans several runs the first by run id is returned, so the
    result is stable across calls.
    """
    accession = (accession or "").strip()
    if not accession or not is_well_formed(accession):
        raise SampleNotFoundError(accession)

    if looks_like_geo_sample(accession):
        runs = runs_for_geo_sample(conn, accession)
        if not runs:
            raise SampleNotFoundError(accession)
        return runs[0]

    # Treat anything else as a run id; the caller's lookup reports if it is absent.
    return accession
