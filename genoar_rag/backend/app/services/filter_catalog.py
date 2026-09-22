"""Where each filter category's values come from.

The categories are spread across two shapes of storage — a column on `sra_core`
and one or more `field_name` values in the `sra_extended` EAV table — and the
disease category folds several field names into one list. Stating that once here
keeps the service, the repository and the endpoint working from the same
description instead of each repeating the layout.
"""

from dataclasses import dataclass, field

from app.utils.normalization import DISEASE_RESOLVED_FIELD


@dataclass(frozen=True)
class FilterCategorySpec:
    """How to read one category's distinct values.

    `case_insensitive` decides whether values differing only in case are one
    entry or two. Tissue and disease are written freely by submitters ("Blood",
    "blood"), so they fold; a platform or an organism comes from a controlled
    vocabulary and is left as recorded.
    """

    name: str
    column: str | None = None
    field_names: tuple[str, ...] = field(default_factory=tuple)
    case_insensitive: bool = False

    @property
    def is_core(self) -> bool:
        return self.column is not None


FILTER_CATEGORIES: tuple[FilterCategorySpec, ...] = (
    FilterCategorySpec(name="tissue", column="tissue", case_insensitive=True),
    FilterCategorySpec(name="cell_type", column="cell_type", case_insensitive=True),
    FilterCategorySpec(name="platform", column="Platform"),
    FilterCategorySpec(name="organism", field_names=("Organism",)),
    FilterCategorySpec(name="assay_type", field_names=("Assay Type",)),
    FilterCategorySpec(name="library_source", field_names=("LibrarySource",)),
    # One field, because the loader has already picked one value per run — the
    # UMLS concept where the nomenclaturist found one, the submitter's own words
    # where it did not. Still folded by case: the half that is free text is
    # written as freely as tissue is.
    FilterCategorySpec(
        name="disease", field_names=(DISEASE_RESOLVED_FIELD,), case_insensitive=True
    ),
)

_BY_NAME = {spec.name: spec for spec in FILTER_CATEGORIES}

# The only column names that reach the SQL string. Categories are defined here
# rather than by a request, but the repository interpolates `column` into the
# query, so the set it may interpolate is stated explicitly.
CORE_COLUMNS = frozenset(
    spec.column for spec in FILTER_CATEGORIES if spec.column is not None
)


def get_category_spec(name: str) -> FilterCategorySpec | None:
    return _BY_NAME.get(name)


def category_names() -> list[str]:
    return [spec.name for spec in FILTER_CATEGORIES]
