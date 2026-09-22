"""Reads for resolving a public accession to the runs GENOAR stores.

A GEO sample id is not a column: it sits in the `sra_extended` EAV table, under
one of two names depending on when the row was written. Keeping the queries here
rather than in the service keeps every statement that touches the database on
the same side of the layer, which is also the side that gets swapped for MySQL.
"""

from app.repositories.base import BaseRepository

# In priority order. `Sample Name` is what the filtered tables call it and what
# `sra_core.Sample_Name` mirrors; `GEO_Accession (exp)` is the crawl's name for
# the same thing, kept so a database restored to a pre-rebuild point resolves too.
GEO_ACCESSION_FIELDS = ("Sample Name", "GEO_Accession (exp)")


class AccessionRepository(BaseRepository):
    def geo_accessions_for_run(self, run_id: str) -> dict[str, str]:
        """The GSM-bearing fields a run carries, keyed by field name."""
        placeholders = ", ".join("?" for _ in GEO_ACCESSION_FIELDS)
        rows = self._fetchall(
            f"SELECT field_name, field_value FROM sra_extended"
            f" WHERE Run = ? AND field_name IN ({placeholders})",
            [run_id, *GEO_ACCESSION_FIELDS],
        )
        return {row["field_name"]: row["field_value"] for row in rows}

    def runs_for_geo_sample(self, accession: str) -> list[str]:
        """Every run recorded under a GSM, in a stable order.

        1,268 GSMs in the curated set name more than one run: one biological
        sample sequenced across several SRA runs.
        """
        placeholders = ", ".join("?" for _ in GEO_ACCESSION_FIELDS)
        rows = self._fetchall(
            f"SELECT DISTINCT Run FROM sra_extended"
            f" WHERE field_name IN ({placeholders}) AND field_value = ? ORDER BY Run",
            [*GEO_ACCESSION_FIELDS, accession],
        )
        return [row["Run"] for row in rows]
