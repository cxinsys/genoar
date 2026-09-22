"""Sample service - business logic for sample queries."""

import sqlite3

from app.exceptions import SampleNotFoundError
from app.models.sample import ExtendedField, SampleDetail, SampleSummary
from app.repositories.extended_repository import ExtendedRepository
from app.repositories.sample_repository import SampleRepository
from app.utils.normalization import DISEASE_RESOLVED_FIELD


def _row_to_summary(
    row: dict, ext_map: dict | None = None, series_map: dict | None = None
) -> SampleSummary:
    """Convert a sra_core row + optional extended fields to SampleSummary."""
    return SampleSummary(
        run_id=row["Run"],
        series=row.get("Series"),
        all_series=(series_map or {}).get(row["Run"], []),
        biosample=row.get("BioSample"),
        tissue=row.get("tissue"),
        cell_type=row.get("cell_type"),
        disease=row.get("disease_state_modified"),
        disease_category=(
            ext_map.get(row["Run"], {}).get(DISEASE_RESOLVED_FIELD) if ext_map else None
        ),
        organism=ext_map.get(row["Run"], {}).get("Organism") if ext_map else None,
        assay_type=ext_map.get(row["Run"], {}).get("Assay Type") if ext_map else None,
        library_source=ext_map.get(row["Run"], {}).get("LibrarySource") if ext_map else None,
        platform=row.get("Platform"),
        instrument=row.get("Instrument"),
    )


def _enrich_summaries(
    rows: list[dict], conn: sqlite3.Connection
) -> list[SampleSummary]:
    """Enrich core rows with extended fields (batch to avoid N+1)."""
    if not rows:
        return []
    run_ids = [r["Run"] for r in rows]
    ext_repo = ExtendedRepository(conn)

    ext_map: dict[str, dict] = {}
    for field_name in ("Organism", "Assay Type", "LibrarySource", DISEASE_RESOLVED_FIELD):
        for rec in ext_repo.get_fields_for_runs(run_ids, field_name):
            ext_map.setdefault(rec["Run"], {})[field_name] = rec["field_value"]

    series_map = SampleRepository(conn).series_for_runs(run_ids)
    return [_row_to_summary(row, ext_map, series_map) for row in rows]


class SampleService:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._repo = SampleRepository(conn)

    def get_sample_detail(self, run_id: str) -> SampleDetail:
        row = self._repo.get_by_run_id(run_id)
        if not row:
            raise SampleNotFoundError(run_id)

        ext_repo = ExtendedRepository(self._conn)
        ext_rows = ext_repo.get_fields_for_run(run_id)
        ext_fields = [
            ExtendedField(
                field_name=e["field_name"],
                field_value=e["field_value"],
                data_type=e["data_type"],
            )
            for e in ext_rows
        ]

        # Extract key extended fields
        ext_dict = {e["field_name"]: e["field_value"] for e in ext_rows}

        return SampleDetail(
            run_id=row["Run"],
            series=row.get("Series"),
            all_series=self._repo.series_for_run(run_id),
            biosample=row.get("BioSample"),
            sample_name=row.get("Sample_Name"),
            tissue=row.get("tissue"),
            cell_type=row.get("cell_type"),
            disease=row.get("disease_state_modified"),
            disease_category=ext_dict.get(DISEASE_RESOLVED_FIELD),
            organism=ext_dict.get("Organism"),
            assay_type=ext_dict.get("Assay Type"),
            library_source=ext_dict.get("LibrarySource"),
            platform=row.get("Platform"),
            instrument=row.get("Instrument"),
            treatment=row.get("treatment"),
            sex=row.get("sex"),
            age=row.get("Age"),
            strain=row.get("strain"),
            genotype=row.get("genotype"),
            size=row.get("size"),
            str_tis=row.get("STR_tis"),
            str_dis=row.get("STR_dis"),
            str_cell=row.get("STR_cell"),
            cui_tis=row.get("CUI_tis"),
            cui_dis=row.get("CUI_dis"),
            cui_cell=row.get("CUI_cell"),
            extended_fields=ext_fields,
        )

    def list_samples(
        self, limit: int = 20, offset: int = 0
    ) -> tuple[list[SampleSummary], int]:
        rows = self._repo.list_samples(limit, offset)
        total = self._repo.count_all()
        summaries = _enrich_summaries(rows, self._conn)
        return summaries, total

    def get_series_samples(
        self, series: str, limit: int = 100, offset: int = 0
    ) -> tuple[list[SampleSummary], int]:
        rows = self._repo.get_by_series(series, limit, offset)
        total = self._repo.count_by_series(series)
        summaries = _enrich_summaries(rows, self._conn)
        return summaries, total

    def get_sibling_samples(
        self, run_id: str, limit: int = 100, offset: int = 0
    ) -> tuple[list[SampleSummary], int]:
        """The runs sharing any study with this one — all of its studies, not one.

        An unknown run is an error, not an empty study: the caller asked about a
        sample that is not here, and an empty list would read as "this sample is
        in no study".
        """
        if not self._repo.get_by_run_id(run_id):
            raise SampleNotFoundError(run_id)
        rows = self._repo.get_siblings_of_run(run_id, limit, offset)
        total = self._repo.count_siblings_of_run(run_id)
        summaries = _enrich_summaries(rows, self._conn)
        return summaries, total

    def get_summaries_for_run_ids(self, run_ids: list[str]) -> list[SampleSummary]:
        """Batch lookup of sample summaries by run IDs."""
        rows = self._repo.get_by_run_ids(run_ids)
        return _enrich_summaries(rows, self._conn)
