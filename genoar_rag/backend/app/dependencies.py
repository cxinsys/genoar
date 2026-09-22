"""FastAPI dependency injection."""

from typing import Generator

from fastapi import Depends, HTTPException, Query

from app.services.datasets import DatasetRegistry, UnknownDataset
from app.services.vector_router import VectorRouter

# The registry of configured datasets, set during app lifespan.
_registry: DatasetRegistry | None = None


def set_registry(registry: DatasetRegistry) -> None:
    global _registry
    _registry = registry


def get_registry() -> DatasetRegistry:
    assert _registry is not None, "Dataset registry not initialized"
    return _registry


def get_dataset(
    dataset: str | None = Query(
        default=None,
        description=(
            "Which of the configured datasets to answer from. Omit for the "
            "default, which is the only one unless this deployment says "
            "otherwise."
        ),
    ),
) -> str:
    """The dataset a request is about.

    One dependency rather than a parameter per endpoint, so that a new
    endpoint cannot accept the name while forgetting to check it, and so that
    an unknown name is refused in one place. Refused rather than fallen back
    from: a request that names a dataset means it, and answering from a
    different one would be a wrong answer wearing the right shape.
    """
    registry = get_registry()
    if dataset is None:
        return registry.default
    try:
        registry.spec(dataset)
    except UnknownDataset:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No dataset named {dataset!r}. This deployment serves: "
                f"{', '.join(registry.names)}."
            ),
        ) from None
    return dataset


def get_db(dataset: str = Depends(get_dataset)) -> Generator:
    """A connection to the dataset this request is about.

    Which dataset a query reaches is settled here, so nothing downstream has
    to carry it: a repository is handed a connection and asks it for rows.
    """
    with get_registry().pool(dataset).get_connection() as conn:
        yield conn


def get_vector_service(
    dataset: str = Depends(get_dataset),
) -> VectorRouter | None:
    """This dataset's vector search, or None where it has none.

    Also settled here, and for the same reason as the connection: an index is
    built over one body of data, so a search that reaches this one cannot
    return a run from another and nothing downstream needs to check.
    """
    return get_registry().vectors(dataset)
