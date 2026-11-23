"""Dashboard statistics service with caching."""

import json
import logging
import sqlite3
import time
from pathlib import Path

from app.config import settings
from app.models.stats import (
    CompletenessTier,
    DashboardStats,
    DistributionItem,
    SpeciesCompleteness,
    SpeciesCorpus,
)

logger = logging.getLogger(__name__)
from app.repositories.extended_repository import ExtendedRepository
from app.repositories.sample_repository import SampleRepository

# The ladder, and what each rung is called on the dashboard. The order is the
# meaning rather than the size: read down the list, the metadata gets richer.
# `no_tissue` is the remainder rather than a rung, and sits at the end for the
# same reason an "Other" slice does — a chart of how complete the metadata is
# would be a strange place to leave out the samples that have none.
COMPLETENESS_TIERS: tuple[tuple[str, str], ...] = (
    ("tissue_only", "Tissue only"),
    ("tissue_disease", "Tissue + disease"),
    ("tissue_cell_type", "Tissue + cell type"),
    ("tissue_disease_cell_type", "Tissue + disease + cell type"),
    ("no_tissue", "No tissue recorded"),
)

# The two species the corpus is made of. Organism has 41 distinct values, but
# everything past these two comes to about 1% between them.
COMPLETENESS_SPECIES: tuple[tuple[str, str], ...] = (
    ("human", "Homo sapiens"),
    ("mouse", "Mus musculus"),
)


def _to_distribution(rows: list[dict], total: int) -> list[DistributionItem]:
    return [
        DistributionItem(
            label=r.get("label") or r.get("value", "Unknown"),
            count=r["count"],
            percentage=round(r["count"] / total * 100, 1) if total > 0 else 0.0,
        )
        for r in rows
    ]


def _census_path() -> Path | None:
    if settings.cell_counts_path:
        return Path(settings.cell_counts_path)
    if settings.processed_results_dir:
        return Path(settings.processed_results_dir) / "cell_counts.json"
    return None


def _load_census() -> dict[str, int] | None:
    """The cell census, or None where there is none to read.

    None and not {} on purpose: an empty dict would sum to a cell count of
    zero and the dashboard would print it, which is a claim about the corpus.
    No census is the absence of a figure, and is shown as one.
    """
    path = _census_path()
    if path is None or not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text())
        return {str(run): int(n) for run, n in raw.items()}
    except (OSError, ValueError, AttributeError) as exc:
        logger.warning("Cell census at %s unreadable, ignoring: %s", path, exc)
        return None


def _corpus(repo: SampleRepository) -> tuple[list[SpeciesCorpus], int | None]:
    """Each species' studies, samples, runs and cells, and the cells in all.

    The census is taken over the processed directory, which may hold more
    runs than the database does — on the deployment it holds 74 mouse runs
    the curated corpus does not. Those are not in the corpus and are not
    counted: the total is the sum over the database's runs, the same runs
    the species figures are summed over, so the whole and its parts agree.
    """
    census = _load_census()
    organism_of = repo.organism_of_runs() if census else {}
    if census:
        census = {run: n for run, n in census.items() if run in organism_of}
    out = []
    for species, organism in COMPLETENESS_SPECIES:
        level = repo.corpus_by_organism(organism)
        cells = (
            sum(n for run, n in census.items() if organism_of.get(run) == organism)
            if census
            else None
        )
        out.append(
            SpeciesCorpus(
                species=species,
                organism=organism,
                series=level["series"],
                geo_samples=level["geo_samples"],
                runs=level["runs"],
                cells=cells,
            )
        )
    total_cells = sum(census.values()) if census else None
    return out, total_cells


def _completeness(repo: SampleRepository) -> list[SpeciesCompleteness]:
    species_list = []
    for species, organism in COMPLETENESS_SPECIES:
        counts = {r["tier"]: r["count"] for r in repo.completeness_by_tier(organism)}
        # The organism's own total, summed from the rungs rather than counted
        # separately, so the shares cannot fail to add up to the whole.
        total = sum(counts.values())
        species_list.append(
            SpeciesCompleteness(
                species=species,
                organism=organism,
                total=total,
                tiers=[
                    CompletenessTier(
                        key=key,
                        label=label,
                        count=counts.get(key, 0),
                        percentage=(
                            round(counts.get(key, 0) / total * 100, 1) if total else 0.0
                        ),
                    )
                    for key, label in COMPLETENESS_TIERS
                ],
            )
        )
    return species_list


class StatsService:
    # Keyed by dataset: these are the headline figures of one body of data, and
    # a deployment serving two would otherwise show one page the other's
    # totals.
    _cache: dict[str, tuple[float, DashboardStats]] = {}
    # CACHE_TTL_SECONDS, read once at import. See FilterService, which shares it.
    _cache_ttl: float = settings.cache_ttl_seconds

    def __init__(self, conn: sqlite3.Connection, dataset: str = "default"):
        self._conn = conn
        self._dataset = dataset

    def get_dashboard_stats(self) -> DashboardStats:
        now = time.time()
        cached = StatsService._cache.get(self._dataset)
        if cached and (now - cached[0]) < StatsService._cache_ttl:
            return cached[1]

        sample_repo = SampleRepository(self._conn)
        ext_repo = ExtendedRepository(self._conn)

        total = sample_repo.count_all()
        total_series = sample_repo.count_series()
        corpus, total_cells = _corpus(sample_repo)

        organism_rows = ext_repo.get_distinct_values("Organism", limit=20)
        assay_rows = ext_repo.get_distinct_values("Assay Type", limit=20)
        platform_rows = sample_repo.platform_distribution()
        library_rows = ext_repo.get_distinct_values("LibrarySource", limit=20)

        result = DashboardStats(
            total_samples=total,
            total_series=total_series,
            total_geo_samples=sample_repo.count_geo_samples(),
            total_cells=total_cells,
            shared_series=sample_repo.count_series_shared(
                [organism for _, organism in COMPLETENESS_SPECIES]
            ),
            corpus=corpus,
            organism_distribution=_to_distribution(organism_rows, total),
            assay_distribution=_to_distribution(assay_rows, total),
            platform_distribution=_to_distribution(platform_rows, total),
            library_source_distribution=_to_distribution(library_rows, total),
            metadata_completeness=_completeness(sample_repo),
        )

        StatsService._cache[self._dataset] = (now, result)
        return result

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache = {}
