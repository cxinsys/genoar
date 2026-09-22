"""Distinct filter values, a page at a time.

Every category needs the same three things — a page of values with their sample
counts, the number of values that exist in total, and an optional substring
match — so the queries are built once from the category's spec rather than
written out per category.
"""

from app.repositories.base import BaseRepository
from app.services.filter_catalog import CORE_COLUMNS, FilterCategorySpec
from app.utils.normalization import escape_like


class FilterRepository(BaseRepository):
    def values(
        self,
        spec: FilterCategorySpec,
        limit: int,
        offset: int = 0,
        query: str | None = None,
    ) -> list[dict]:
        """One page of values, most frequent first.

        The secondary sort by value is what makes paging safe: counts tie
        constantly in these categories, and without a deterministic order a row
        could appear on two pages or on none.
        """
        table, select_expr, group_expr, where, params = self._parts(spec, query)
        sql = (
            f"SELECT {select_expr} as value, COUNT(*) as count "
            f"FROM {table} WHERE {where} "
            f"GROUP BY {group_expr} ORDER BY count DESC, value ASC "
            f"LIMIT ? OFFSET ?"
        )
        return self._fetchall(sql, [*params, limit, offset])

    def total(self, spec: FilterCategorySpec, query: str | None = None) -> int:
        """How many distinct values the category holds under the same filter."""
        table, _select, group_expr, where, params = self._parts(spec, query)
        sql = f"SELECT COUNT(DISTINCT {group_expr}) FROM {table} WHERE {where}"
        return self._fetchval(sql, params) or 0

    def _parts(
        self, spec: FilterCategorySpec, query: str | None
    ) -> tuple[str, str, str, str, list]:
        """Table, select and grouping expressions, WHERE clause and parameters."""
        if spec.is_core:
            if spec.column not in CORE_COLUMNS:
                raise ValueError(f"Column not allowed in a filter query: {spec.column}")
            table = "sra_core"
            value_expr = spec.column
            where = f"{spec.column} IS NOT NULL AND {spec.column} != ''"
            params: list = []
        else:
            table = "sra_extended"
            value_expr = "field_value"
            placeholders = ", ".join("?" for _ in spec.field_names)
            where = (
                f"field_name IN ({placeholders}) "
                f"AND field_value IS NOT NULL AND field_value != ''"
            )
            params = list(spec.field_names)

        if spec.case_insensitive:
            group_expr = f"LOWER({value_expr})"
            # Group case-insensitively but show the value as somebody wrote it.
            # MIN picks the same representative every time, which matters because
            # the label would otherwise change between pages; in ASCII it also
            # prefers the capitalised spelling ("Blood" before "blood").
            select_expr = f"MIN({value_expr})"
        else:
            group_expr = value_expr
            select_expr = value_expr

        if query and query.strip():
            where += f" AND LOWER({value_expr}) LIKE LOWER(?) ESCAPE '\\'"
            params.append(f"%{escape_like(query.strip())}%")

        return table, select_expr, group_expr, where, params
