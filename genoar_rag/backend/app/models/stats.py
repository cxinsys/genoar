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


class DashboardStats(BaseModel):
    total_samples: int = Field(ge=0)
    total_series: int = Field(ge=0)
    organism_distribution: list[DistributionItem]
    assay_distribution: list[DistributionItem]
    platform_distribution: list[DistributionItem]
    library_source_distribution: list[DistributionItem]
    metadata_completeness: list[SpeciesCompleteness]
