"""Dashboard statistics service with caching."""

import sqlite3
import time

from app.config import settings
from app.models.stats import (
    CompletenessTier,
    DashboardStats,
    DistributionItem,
    SpeciesCompleteness,
)
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

        organism_rows = ext_repo.get_distinct_values("Organism", limit=20)
        assay_rows = ext_repo.get_distinct_values("Assay Type", limit=20)
        platform_rows = sample_repo.platform_distribution()
        library_rows = ext_repo.get_distinct_values("LibrarySource", limit=20)

        result = DashboardStats(
            total_samples=total,
            total_series=total_series,
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
