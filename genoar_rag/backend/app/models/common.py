"""Common models for pagination, sorting, and generic responses."""

from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class SortOrder(str, Enum):
    asc = "asc"
    desc = "desc"


class PaginationParams(BaseModel):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class SortParams(BaseModel):
    sort_by: str = Field(default="run_id")
    sort_order: SortOrder = Field(default=SortOrder.asc)


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
