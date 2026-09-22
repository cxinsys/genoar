"""Dynamic SQL query builder with parameterized queries."""

from app.models.common import SortOrder
from app.models.search import SearchFilters
from app.utils.normalization import (
    DISEASE_RESOLVED_FIELD,
    MISSING_VALUE_TOKENS,
    escape_like,
)

# Literal, not a bound parameter: a placeholder inside ORDER BY would have to be
# threaded between the WHERE parameters and the LIMIT ones. The tokens are a
# module constant that never sees user input, and the quoting below is exact.
_MISSING_TOKEN_SQL = ", ".join(f"'{t}'" for t in MISSING_VALUE_TOKENS)


def _is_missing(expr: str) -> str:
    """SQL that is true when `expr` holds no usable value.

    NULL or one of the placeholder strings. Used to keep those rows off the
    front of a sorted page without hiding them from the results.
    """
    return f"({expr} IS NULL OR LOWER(TRIM({expr})) IN ({_MISSING_TOKEN_SQL}))"

# Mapping from API sort fields to actual DB columns.
#
# Anything not listed here falls back to c.Run, silently: the dashboard's
# By Assay / By Series tabs asked for sorts that were not in this map and so
# returned the same rows as By Tissue, which is what made them look inert.
#
# assay_type is not a column — it is an EAV row — so it is ordered by a
# correlated lookup. The field name is a constant from EXTENDED_FIELD_MAP below,
# never user input, so it is safe to inline.
SORT_COLUMN_MAP = {
    "run_id": "c.Run",
    "release_date": "c.created_at",
    "tissue": "c.tissue",
    # A run's first study by name, not the one `sra_core.Series` happens to keep.
    # 870 runs belong to two, and sorting on the core column ordered them by
    # whichever survived — so two samples of the same study could sit apart while
    # a sample of another study sat between them. The lowest of a run's studies
    # is a definition that does not depend on which one was stored.
    "series": "s_sort.Series",
    "assay_type": "e_sort.field_value",
}

# Sorts that read a value the sra_core row does not carry. The join is only added
# when that sort is asked for, so an ordinary search still touches one table.
# Joining rather than sorting on a correlated subquery halves the work, and it is
# safe here because no run holds two 'Assay Type' rows.
SORT_JOIN_MAP = {
    "assay_type": (
        "LEFT JOIN sra_extended e_sort "
        "ON e_sort.Run = c.Run AND e_sort.field_name = 'Assay Type'"
    ),
    # A run in two studies would otherwise appear twice, so the join is to one
    # row per run — its lowest study — rather than to all of them.
    "series": (
        "LEFT JOIN (SELECT Run, MIN(Series) AS Series FROM sra_series GROUP BY Run)"
        " s_sort ON s_sort.Run = c.Run"
    ),
}

# sra_extended field name mapping (API filter name -> EAV field_name)
EXTENDED_FIELD_MAP = {
    "organism": "Organism",
    "assay_type": "Assay Type",
    "library_source": "LibrarySource",
}


def build_search_query(
    filters: SearchFilters,
    count_only: bool = False,
    run_id_in: list[str] | None = None,
    page: tuple[int, int] | None = None,
) -> tuple[str, list]:
    """Build a parameterized search query from filters.

    Args:
        run_id_in: Optional list of run IDs to restrict results to.
                   Used by hybrid search to intersect semantic candidates
                   with SQL filters in a single query.
        page: Overrides (limit, offset). SearchFilters caps `limit` at 100
              because that is what a page of the UI should ever ask for; an
              export is not a page, and walking a whole result set 100 rows at
              a time means one query per hundred rows with an offset that grows
              each time — work that squares with the size of the set.

    Returns (sql_string, params_list).
    """
    params: list = []
    where_clauses: list[str] = []

    # --- restrict to specific run IDs (hybrid intersection) ---
    #
    # `is not None`, not truthiness: an empty list is a restriction to nothing,
    # not the absence of one. Treating it as absent meant a semantic export whose
    # search matched no candidate fell through to no restriction at all and
    # returned the whole corpus — the widest possible answer to the narrowest
    # possible question.
    if run_id_in is not None:
        if not run_id_in:
            where_clauses.append("1 = 0")
        else:
            placeholders = ", ".join("?" for _ in run_id_in)
            where_clauses.append(f"c.Run IN ({placeholders})")
            params.extend(run_id_in)

    # --- sra_core direct filters ---
    if filters.tissue:
        placeholders = ", ".join("?" for _ in filters.tissue)
        where_clauses.append(f"LOWER(c.tissue) IN ({placeholders})")
        params.extend(t.lower() for t in filters.tissue)

    if filters.cell_type:
        placeholders = ", ".join("?" for _ in filters.cell_type)
        where_clauses.append(f"LOWER(c.cell_type) IN ({placeholders})")
        params.extend(ct.lower() for ct in filters.cell_type)

    if filters.platform:
        placeholders = ", ".join("?" for _ in filters.platform)
        where_clauses.append(f"c.Platform IN ({placeholders})")
        params.extend(filters.platform)

    if filters.series:
        # Through sra_series, not c.Series: a run shared by two studies carries
        # only one of them in the core column, so matching there hides the run
        # from the other study.
        where_clauses.append(
            "EXISTS (SELECT 1 FROM sra_series s WHERE s.Run = c.Run AND s.Series = ?)"
        )
        params.append(filters.series)

    # --- sra_extended subquery filters ---
    #
    # Matched case-insensitively, as tissue, cell type, disease and the keyword
    # already were. These three were the exceptions: `organism=homo sapiens`
    # returned nothing while `organism=Homo sapiens` returned 3,114, so whether
    # a hand-written URL worked came down to the capitals in it.
    for filter_name, field_name in EXTENDED_FIELD_MAP.items():
        values = getattr(filters, filter_name)
        if values:
            placeholders = ", ".join("?" for _ in values)
            where_clauses.append(
                f"c.Run IN (SELECT Run FROM sra_extended "
                f"WHERE field_name = ? AND LOWER(field_value) IN ({placeholders}))"
            )
            params.append(field_name)
            params.extend(v.lower() for v in values)

    # --- disease filter (one resolved field, written at load time) ---
    if filters.disease:
        value_placeholders = ", ".join("?" for _ in filters.disease)
        where_clauses.append(
            f"c.Run IN (SELECT Run FROM sra_extended "
            f"WHERE field_name = ? "
            f"AND LOWER(field_value) IN ({value_placeholders}))"
        )
        params.append(DISEASE_RESOLVED_FIELD)
        params.extend(d.lower() for d in filters.disease)

    # --- keyword search (LIKE across multiple core fields) ---
    if filters.keyword:
        escaped = escape_like(filters.keyword)
        keyword_fields = ["c.tissue", "c.cell_type", "c.treatment", "c.genotype"]
        or_clauses = " OR ".join(f"LOWER({f}) LIKE LOWER(?) ESCAPE '\\'" for f in keyword_fields)
        where_clauses.append(f"({or_clauses})")
        params.extend(f"%{escaped}%" for _ in keyword_fields)

    # --- Assemble query ---
    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    if count_only:
        sql = f"SELECT COUNT(*) FROM sra_core c WHERE {where_sql}"
    else:
        sort_col = SORT_COLUMN_MAP.get(filters.sort_by, "c.Run")
        sort_dir = "DESC" if filters.sort_order == SortOrder.desc else "ASC"
        # Two additions to the plain ORDER BY:
        #   missing values first in the key, so the samples with no tissue (13%
        #   of them) or a submitter's "-" sink to the end instead of filling the
        #   first page;
        #   c.Run last, so rows sharing a value keep a fixed order. Without that
        #   tiebreaker two pages of the same sort can repeat or skip a sample,
        #   since the engine is free to order equal keys differently each query.
        join_sql = SORT_JOIN_MAP.get(filters.sort_by, "")
        sql = (
            f"SELECT c.* FROM sra_core c {join_sql} WHERE {where_sql} "
            f"ORDER BY {_is_missing(sort_col)}, {sort_col} {sort_dir}, c.Run ASC "
            f"LIMIT ? OFFSET ?"
        )
        params.extend(list(page) if page else [filters.limit, filters.offset])

    return sql, params
