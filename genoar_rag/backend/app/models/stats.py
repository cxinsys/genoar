"""Dashboard statistics models."""

from pydantic import BaseModel, Field


class DistributionItem(BaseModel):
    label: str
    count: int = Field(ge=0)
    percentage: float = Field(ge=0.0, le=100.0)


class CompletenessTier(BaseModel):
    """One rung of the metadata ladder, and how many runs stand on it.

    The rungs partition a species: every run falls in exactly one, and the
    counts sum to `SpeciesCompleteness.total`. Overlapping sets would read as a
    ladder too, and would make a share of the whole impossible to draw.
    """

    key: str
    label: str
    count: int = Field(ge=0)
    percentage: float = Field(ge=0.0, le=100.0)


class SpeciesCompleteness(BaseModel):
    """How much is known about one species' samples.

    Split by species because the answer differs by more than a little. Mouse
    samples are described by experimental condition rather than by disease, so
    a combined chart would hide that a quarter of the human corpus carries a
    diagnosis and almost none of the mouse corpus does.
    """

    species: str
    organism: str
    total: int = Field(ge=0)
    tiers: list[CompletenessTier]


class SpeciesCorpus(BaseModel):
    """One species' share of the corpus, counted at each level it exists at.

    Study, sample, run and cell are four different things and the corpus has
    a different number of each. Reported together so that a reader can see
    which figure a number is, rather than meeting 7,275 and taking it for the
    sample count when it is the run count.
    """

    species: str
    organism: str
    series: int = Field(ge=0)
    geo_samples: int = Field(ge=0)
    runs: int = Field(ge=0)
    # None where no cell census has been loaded: a deployment serving metadata
    # only has nothing to count, and reporting 0 would be a claim.
    cells: int | None = Field(default=None, ge=0)


class DashboardStats(BaseModel):
    # `total_samples` is the run count and has always been; the name is kept
    # for the clients that read it. `corpus` below says what each level holds.
    total_samples: int = Field(ge=0)
    total_series: int = Field(ge=0)
    total_geo_samples: int = Field(default=0, ge=0)
    total_cells: int | None = Field(default=None, ge=0)
    # Studies counted under more than one species. The per-species series
    # figures in `corpus` sum to the whole plus this.
    shared_series: int = Field(default=0, ge=0)
    corpus: list[SpeciesCorpus] = []
    organism_distribution: list[DistributionItem]
    assay_distribution: list[DistributionItem]
    platform_distribution: list[DistributionItem]
    library_source_distribution: list[DistributionItem]
    metadata_completeness: list[SpeciesCompleteness]
