"""API test fixtures: TestClient with test DB."""

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import DEFAULT_DATASET, DatasetSpec, settings
from app.db.connection import SQLiteConnectionPool
from app.dependencies import set_registry
from app.exceptions import SampleNotFoundError
from app.routers import datasets as datasets_router
from app.routers import export, filters, samples, search, stats
from app.services.datasets import DatasetRegistry
from app.services.filter_service import FilterService
from app.services.stats_service import StatsService
from fastapi import Request
from fastapi.responses import JSONResponse


@pytest.fixture(scope="session")
def api_pool(test_db_path) -> SQLiteConnectionPool:
    pool = SQLiteConnectionPool(test_db_path, pool_size=2)
    pool.initialize()
    yield pool
    pool.close_all()


def spec(name: str = DEFAULT_DATASET, label: str = "") -> DatasetSpec:
    """A dataset whose paths are never read, because the pool is handed over."""
    return DatasetSpec(name=name, label=label, db_path="", db_name="", species={})


def _create_test_app(
    pools: SQLiteConnectionPool | dict[str, SQLiteConnectionPool],
    vectors: dict[str, object] | None = None,
    labels: dict[str, str] | None = None,
) -> FastAPI:
    """An app over one dataset, or over several.

    A single pool is the ordinary case and makes one dataset named like a
    deployment that configured none. A mapping makes one dataset per key, in
    key order, which is what a test of two bodies of data wants.
    """
    if not isinstance(pools, dict):
        pools = {DEFAULT_DATASET: pools}
    labels = labels or {}
    vectors = vectors or {}
    registry = DatasetRegistry(
        [spec(name, labels.get(name, "")) for name in pools]
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        registry.initialize(
            settings,
            pool_factory=lambda _s, sp: pools[sp.name],
            # No semantic search unless a test supplies an index.
            vector_factory=lambda _s, sp: vectors.get(sp.name),
        )
        set_registry(registry)
        yield
        FilterService.clear_cache()
        StatsService.clear_cache()

    test_app = FastAPI(lifespan=lifespan)

    @test_app.exception_handler(SampleNotFoundError)
    async def sample_not_found_handler(request: Request, exc: SampleNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    test_app.include_router(datasets_router.router)
    test_app.include_router(samples.router)
    test_app.include_router(search.router)
    test_app.include_router(filters.router)
    test_app.include_router(stats.router)
    test_app.include_router(export.router)
    return test_app


@pytest.fixture
def client(api_pool):
    """TestClient that uses the test DB pool."""
    FilterService.clear_cache()
    StatsService.clear_cache()
    test_app = _create_test_app(api_pool)
    with TestClient(test_app) as c:
        yield c
