"""Text normalization and SQL safety utilities."""


def normalize_text(text: str) -> str:
    """Normalize text: strip whitespace, collapse internal spaces."""
    return " ".join(text.split())


def escape_like(text: str) -> str:
    """Escape LIKE pattern special characters for parameterized queries.

    Uses backslash as escape character (must pair with ESCAPE '\\' in SQL).
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Disease-related field names in sra_extended (most common ones)
# Values submitters typed to mean "nothing here": a dash, "N/A", and so on.
# They are text as far as the database is concerned, and they sort before every
# real name because punctuation precedes letters, so ordering by tissue used to
# open with 67 rows called "-" and "--" before the first actual tissue. Sorting
# treats them as missing; they are still returned, just not first.
#
# Compared against LOWER(TRIM(value)), so only lowercase entries belong here.
MISSING_VALUE_TOKENS = (
    "",
    "-",
    "--",
    "---",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "not applicable",
    "not collected",
    "missing",
)


DISEASE_FIELD_NAMES = (
    # The consolidated field the preprocessing writes, and the first one to read:
    # it is what the filtered metadata tables carry, where the names below are the
    # raw submitter fields it was consolidated from. Absent from this list until
    # 2026-08-03 — which was invisible while the database held the whole crawl,
    # where it covered a fraction of a percent of rows, and became half the dataset
    # once the database was cut down to the samples the preprocessing selected.
    "disease_state_modified",
    "disease_state",
    "disease",
    "diagnosis",
    "condition",
    "donor_condition",
    "patient_diagnosis",
    "disease_status",
    "broad_diagnosis",
    "clinical_diagnosis",
)


# The disease nomenclaturist's answer: one UMLS concept per run, where the names
# above are whatever the submitter typed.
#
# It does not become a filter of its own, and it does not join the list above
# either. Every run that has a concept also has free text, so a second filter
# covers a subset of the first, and merging the two vocabularies into one list
# grew it from 99 entries to 139 — the same samples under more names.
#
# Instead one value is chosen per run, at load time: the concept where there is
# one, the free text where there is not. That is `disease_resolved`, and it is
# what the disease filter reads. The list falls to 75 entries and the duplicates
# go with it — "Healthy", "Healthy control", "Healthy donor", "HIVneg" and
# "Never smokers" are one entry, "Control Groups", because that is what they are.
#
# Resolving once when the data is written rather than in every query keeps this
# out of the SQL: the filter still reads a single field name.
DISEASE_CATEGORY_FIELD = "disease (UMLS) - 1st level"

# Where the resolved value is kept. Derived, so the loader owns it — regenerate
# it whenever the curation changes rather than editing it in place.
DISEASE_RESOLVED_FIELD = "disease_resolved"
