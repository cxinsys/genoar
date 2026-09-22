"""Repository for sra_extended EAV table queries."""

from app.repositories.base import BaseRepository


class ExtendedRepository(BaseRepository):
    def get_fields_for_run(self, run_id: str) -> list[dict]:
        return self._fetchall(
            "SELECT field_name, field_value, data_type FROM sra_extended "
            "WHERE Run = ? ORDER BY field_name",
            [run_id],
        )

    def get_fields_for_runs(self, run_ids: list[str], field_name: str) -> list[dict]:
        """Batch fetch a specific field for multiple runs (N+1 prevention)."""
        if not run_ids:
            return []
        placeholders = ", ".join("?" for _ in run_ids)
        return self._fetchall(
            f"SELECT Run, field_value FROM sra_extended "
            f"WHERE field_name = ? AND Run IN ({placeholders})",
            [field_name, *run_ids],
        )

    def get_distinct_values(self, field_name: str, limit: int = 50) -> list[dict]:
        """Get distinct values with counts for a field."""
        return self._fetchall(
            "SELECT field_value as value, COUNT(*) as count FROM sra_extended "
            "WHERE field_name = ? AND field_value IS NOT NULL "
            "GROUP BY field_value ORDER BY count DESC LIMIT ?",
            [field_name, limit],
        )

    def get_disease_values(self, field_names: tuple[str, ...], limit: int = 50) -> list[dict]:
        """Get distinct disease values across multiple EAV field names."""
        placeholders = ", ".join("?" for _ in field_names)
        return self._fetchall(
            f"SELECT field_value as value, COUNT(*) as count FROM sra_extended "
            f"WHERE field_name IN ({placeholders}) AND field_value IS NOT NULL "
            f"GROUP BY LOWER(field_value) ORDER BY count DESC LIMIT ?",
            [*field_names, limit],
        )
