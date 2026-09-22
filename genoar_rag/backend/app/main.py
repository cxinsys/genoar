"""FastAPI application factory with lifespan management."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.dependencies import set_registry
from app.exceptions import SampleNotFoundError
from app.routers import datasets as datasets_router
from app.routers import export, filters, samples, search, stats
from app.services.datasets import DatasetRegistry
from app.services.filter_service import FilterService
from app.services.stats_service import StatsService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — one pool and one vector router per configured dataset. A
    # deployment that configures none gets exactly one of each, built from the
    # ordinary settings, which is what it had before datasets existed.
    registry = DatasetRegistry(settings.dataset_specs())
    registry.initialize(settings)
    set_registry(registry)
    logger.info(
        "DB backend: %s; datasets: %s (default %s)",
        settings.db_backend,
        ", ".join(registry.names),
        registry.default,
    )

    yield

    # Shutdown
    FilterService.clear_cache()
    StatsService.clear_cache()
    registry.close_all()


app = FastAPI(
    title="GENOAR RAG Backend",
    version="0.1.0",
    description="SQL-based structured search API for SRA samples",
    lifespan=lifespan,
    # No Swagger UI, ReDoc or schema. The public site reaches this service
    # through the frontend's rewrite of /api and /health only, so a reachable
    # /docs is a management interface the deployment never meant to show — and
    # the one thing a vulnerability checklist reads it as.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Every route is a GET and nothing here uses a cookie or a session, so a
    # preflight that advertised the other verbs and credentials was describing
    # an API this is not.
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# The same browser-facing headers the frontend sets on its own pages. The
# frontend cannot set them on these: Next applies its `headers()` config to
# what it serves, and /api and /health are rewrites — the backend's response
# goes through untouched. So the backend says it itself.
_RESPONSE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


@app.middleware("http")
async def response_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in _RESPONSE_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


@app.exception_handler(SampleNotFoundError)
async def sample_not_found_handler(request: Request, exc: SampleNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


app.include_router(datasets_router.router)
app.include_router(samples.router)
app.include_router(search.router)
app.include_router(filters.router)
app.include_router(stats.router)
app.include_router(export.router)


@app.get("/health")
def health():
    """Whether the service is up, and what semantic search it can do.

    Reported for the default dataset — the one-line answer a monitor wants.
    A configured backend that fails to load stops startup rather than being
    dropped, so on a running server every dataset's configured species are
    live; a dataset absent from semantic search is one that configured none.
    """
    from app.dependencies import get_registry

    registry = get_registry()
    vs = registry.vectors(registry.default)
    enabled = vs is not None and vs.is_initialized
    return {
        "status": "ok",
        "datasets": registry.names,
        "semantic_search": enabled,
        "semantic_species": vs.available_species() if enabled else [],
    }
