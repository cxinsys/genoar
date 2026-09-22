"""Semantic search response models."""

from pydantic import BaseModel, Field


class SimilarSampleItem(BaseModel):
    run_id: str
    similarity_score: float = Field(ge=0.0, le=1.0)
    tissue: str | None = None
    cell_type: str | None = None
    disease: str | None = None
    organism: str | None = None
    assay_type: str | None = None


class SimilarSamplesResponse(BaseModel):
    query_run_id: str
    items: list[SimilarSampleItem]
    total: int = Field(ge=0)
