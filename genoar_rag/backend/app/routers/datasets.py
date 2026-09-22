"""What this deployment serves, so a client need not be told separately."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.dependencies import get_registry
from app.services.datasets import DatasetRegistry

router = APIRouter(prefix="/api/v1", tags=["datasets"])


class DatasetInfo(BaseModel):
    name: str
    label: str
    is_default: bool


class DatasetsResponse(BaseModel):
    items: list[DatasetInfo]
    default: str
    #: Whether more than one body of data is served. A client uses this to
    #: decide whether naming the corpus on a page carries information — with
    #: one dataset the name is on every page and says nothing.
    is_split: bool


@router.get("/datasets", response_model=DatasetsResponse)
def list_datasets(registry: DatasetRegistry = Depends(get_registry)):
    """The datasets configured here, in the order they were named.

    Served rather than compiled into the client so that which bodies of data a
    deployment holds is one deployment's configuration and not two. A client
    that hard-coded them would have to be rebuilt to learn that a second one
    had appeared, and could disagree with the server about what exists.
    """
    return DatasetsResponse(
        items=[
            DatasetInfo(
                name=name,
                label=registry.label(name) or name,
                is_default=name == registry.default,
            )
            for name in registry.names
        ],
        default=registry.default,
        is_split=registry.is_split,
    )
