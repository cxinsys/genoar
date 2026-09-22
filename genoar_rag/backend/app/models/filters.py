"""Filter option models."""

from pydantic import BaseModel, Field


class FilterValue(BaseModel):
    value: str
    count: int = Field(ge=0)


class FilterCategory(BaseModel):
    name: str
    values: list[FilterValue]
    # Every distinct value the category holds, not the number returned: a client
    # showing a page needs to know how much it is not showing.
    total_distinct: int = Field(ge=0)


class FilterCategoryPage(FilterCategory):
    """One page of a single category, with the request it answers."""

    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    query: str | None = None


class FilterOptionsResponse(BaseModel):
    categories: list[FilterCategory]
