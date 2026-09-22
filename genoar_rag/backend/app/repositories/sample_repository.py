"""Repository for sra_core table queries."""

from app.repositories.base import BaseRepository
from app.utils.normalization import DISEASE_FIELD_NAMES


class SampleRepository(BaseRepository):
    def get_by_run_id(self, run_id: str) -> dict | None:
        return self._fetchone("SELECT * FROM sra_core WHERE Run = ?", [run_id])

    def get_by_run_ids(self, run_ids: list[str]) -> list[dict]:
        """Several runs at once, in no particular order.

        Callers hold the order they want — a vector search's ranking, usually —
        and map the rows onto it themselves.
        """
        if not run_ids:
            return []
        placeholders = ", ".join("?" for _ in run_ids)
        return self._fetchall(
            f"SELECT * FROM sra_core WHERE Run IN ({placeholders})",
            run_ids,
        )

    def get_by_series(self, series: str, limit: int = 100, offset: int = 0) -> list[dict]:
        """One study's runs, read through `sra_series` rather than `sra_core`.

        870 runs belong to more than one GSE, and `sra_core.Series` holds one of
        them. Asking that column for a study therefore answered for 760 of the
        curation's 888 studies and returned nothing for the other 128, which were
        the second study of a shared run every time. `sra_series` carries the whole
        mapping, so a run appears under each study it belongs to.
        """
        return self._fetchall(
            "SELECT c.* FROM sra_core c"
            " JOIN sra_series s ON s.Run = c.Run"
            " WHERE s.Series = ? ORDER BY c.Run LIMIT ? OFFSET ?",
            [series, limit, offset],
        )

    def count_by_series(self, series: str) -> int:
        return self._fetchval(
            "SELECT COUNT(*) FROM sra_series WHERE Series = ?",
            [series],
        )

    def get_siblings_of_run(
        self, run_id: str, limit: int = 100, offset: int = 0
    ) -> list[dict]:
        """Every run sharing a study with this one, across all of its studies.

        The panel this feeds is "Samples in This Series", and for a run in two
        studies there is no single series to be in. Asking the core column
        answered for one of them and silently dropped the other's samples.

        The run itself is included: it is one of the study's samples, and the
        page marks it rather than omitting it.
        """
        return self._fetchall(
            "SELECT DISTINCT c.* FROM sra_core c"
            " JOIN sra_series s ON s.Run = c.Run"
            " WHERE s.Series IN (SELECT Series FROM sra_series WHERE Run = ?)"
            " ORDER BY c.Run LIMIT ? OFFSET ?",
            [run_id, limit, offset],
        )

    def count_siblings_of_run(self, run_id: str) -> int:
        return self._fetchval(
            "SELECT COUNT(DISTINCT s.Run) FROM sra_series s"
            " WHERE s.Series IN (SELECT Series FROM sra_series WHERE Run = ?)",
            [run_id],
        )

    def series_for_runs(self, run_ids: list[str]) -> dict[str, list[str]]:
        """Every study each run belongs to, in one query.

        A page of results asks for twenty of these at once; one query per card
        would be twenty round trips to say something the same table could answer
        in one.
        """
        if not run_ids:
            return {}
        placeholders = ", ".join("?" for _ in run_ids)
        out: dict[str, list[str]] = {}
        for row in self._fetchall(
            f"SELECT Run, Series FROM sra_series WHERE Run IN ({placeholders})"
            " ORDER BY Run, Series",
            run_ids,
        ):
            out.setdefault(row["Run"], []).append(row["Series"])
        return out

    def series_for_run(self, run_id: str) -> list[str]:
        """Every study a run belongs to, in a stable order."""
        return [
            row["Series"]
            for row in self._fetchall(
                "SELECT Series FROM sra_series WHERE Run = ? ORDER BY Series",
                [run_id],
            )
        ]

    def list_samples(self, limit: int = 20, offset: int = 0) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM sra_core ORDER BY Run LIMIT ? OFFSET ?",
            [limit, offset],
        )

    def count_all(self) -> int:
        return self._fetchval("SELECT COUNT(*) FROM sra_core")

    def count_series(self) -> int:
        """How many studies the corpus covers.

        From `sra_series`, so a study reached only through a run it shares with
        another study is counted. Against `sra_core.Series` this reads 888 rather
        than 760.
        """
        return self._fetchval("SELECT COUNT(DISTINCT Series) FROM sra_series")

    def platform_distribution(self) -> list[dict]:
        return self._fetchall(
            "SELECT Platform as label, COUNT(*) as count FROM sra_core "
            "WHERE Platform IS NOT NULL GROUP BY Platform ORDER BY count DESC"
        )

    def completeness_by_tier(self, organism: str) -> list[dict]:
        """One organism's runs, grouped by which descriptive fields they carry.

        Tissue, cell type and disease are the three fields a reader narrows by,
        and they are not kept in the same place. The first two are columns on
        sra_core. Disease was submitted under any of nine different names and
        lives in the EAV table — `disease_state_modified` looks like the column
        for it and holds a value for 500 runs out of nearly 200,000, so it is
        not one.

        The arms are ordered richest first so that each run matches exactly one:
        the counts partition the organism rather than overlapping, which is what
        lets them be drawn as shares of a whole.
        """
        placeholders = ", ".join("?" for _ in DISEASE_FIELD_NAMES)
        return self._fetchall(
            f"""
            WITH described AS (
                SELECT DISTINCT Run FROM sra_extended
                WHERE field_name IN ({placeholders})
                  AND TRIM(COALESCE(field_value, '')) <> ''
            )
            SELECT
                CASE
                    WHEN TRIM(COALESCE(c.tissue, '')) = '' THEN 'no_tissue'
                    WHEN d.Run IS NOT NULL AND TRIM(COALESCE(c.cell_type, '')) <> ''
                        THEN 'tissue_disease_cell_type'
                    WHEN TRIM(COALESCE(c.cell_type, '')) <> '' THEN 'tissue_cell_type'
                    WHEN d.Run IS NOT NULL THEN 'tissue_disease'
                    ELSE 'tissue_only'
                END AS tier,
                COUNT(*) AS count
            FROM sra_core c
            JOIN sra_extended o
              ON o.Run = c.Run
             AND o.field_name = 'Organism'
             AND o.field_value = ?
            LEFT JOIN described d ON d.Run = c.Run
            GROUP BY tier
            """,
            [*DISEASE_FIELD_NAMES, organism],
        )
