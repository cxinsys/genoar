"""The bodies of data this deployment serves, and how a request reaches one.

A deployment usually has one. It configures nothing, the service builds a
single dataset from the ordinary settings, and no dataset name appears in a URL
or on a page — which is what the service did before this module existed.

A deployment can also have more than one, and then its pages need not all
describe the same corpus: the dashboard can present one body of data while the
search page searches another. That is a configuration, not two products, so a
dataset is the ordinary settings with some paths replaced — its own database,
its own vector indexes, the same code.

Datasets are kept apart by *where they are* rather than by a column saying
which rows belong to which. A membership column only works when the two sets
can share rows, which requires them to agree about every run they have in
common; separate files require nothing of each other and let two datasets be
unrelated. It also means nothing downstream has to know datasets exist: a
repository is handed a connection, and which dataset that connection reaches
was decided before it got there.
"""

import logging

from app.config import DatasetSpec, Settings
from app.db.connection import ConnectionPool, create_pool_for
from app.services.vector_router import VectorRouter
from app.services.vector_service import VectorService

logger = logging.getLogger(__name__)


class UnknownDataset(KeyError):
    """A request named a dataset this deployment does not serve."""


class DatasetRegistry:
    """Every configured dataset, with its connection pool and vector router.

    Built once at startup. The pools are held here rather than one per request
    because a pool is expensive to make and a dataset is not chosen per
    request — it is chosen per page, from a fixed set.
    """

    def __init__(self, specs: list[DatasetSpec]):
        if not specs:
            raise ValueError("a deployment serves at least one dataset")
        self._specs = {spec.name: spec for spec in specs}
        self._order = [spec.name for spec in specs]
        self._pools: dict[str, ConnectionPool] = {}
        self._vectors: dict[str, VectorRouter | None] = {}

    # -- what exists ---------------------------------------------------

    @property
    def names(self) -> list[str]:
        """In the order the deployment named them."""
        return list(self._order)

    @property
    def default(self) -> str:
        """What a request naming no dataset is asking about.

        The first configured, so a deployment that adds a second dataset does
        not change what its existing addresses mean.
        """
        return self._order[0]

    @property
    def is_split(self) -> bool:
        """Whether this deployment serves more than one body of data.

        What the pages use to decide whether saying which one is worth the
        line. With a single dataset the answer is on every page and tells the
        reader nothing.
        """
        return len(self._order) > 1

    def label(self, name: str) -> str:
        return self._require(name).label

    def spec(self, name: str) -> DatasetSpec:
        return self._require(name)

    def _require(self, name: str) -> DatasetSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise UnknownDataset(name) from None

    # -- what it holds -------------------------------------------------

    def initialize(
        self,
        settings: Settings,
        pool_factory=create_pool_for,
        vector_factory=None,
    ) -> None:
        """Open each dataset's database and load its indexes.

        The two factories are here so a test can hand over a pool it already
        holds, or a stub index, without the registry knowing it is being
        tested. Production passes neither.
        """
        make_vectors = vector_factory or _build_vector_router
        try:
            for name in self._order:
                spec = self._specs[name]
                self._pools[name] = pool_factory(settings, spec)
                self._vectors[name] = make_vectors(settings, spec)
                _require_index_over_this_corpus(
                    name, self._pools[name], self._vectors[name]
                )
        except Exception:
            # One dataset failing fails the startup; what the others already
            # opened must not stay open behind the raise.
            self.close_all()
            raise

    def pool(self, name: str) -> ConnectionPool:
        self._require(name)
        return self._pools[name]

    def vectors(self, name: str) -> VectorRouter | None:
        """This dataset's vector search, or None where it has none.

        Per dataset, because an index is built over one body of data and
        knows nothing of any other. It is also why nothing has to filter
        search results by dataset afterwards: the index a query reaches only
        contains runs that dataset holds.
        """
        self._require(name)
        return self._vectors.get(name)

    def close_all(self) -> None:
        for pool in self._pools.values():
            pool.close_all()
        for router in self._vectors.values():
            if router is not None:
                router.close()
        self._pools.clear()
        self._vectors.clear()


def _require_index_over_this_corpus(
    name: str, pool: ConnectionPool, router
) -> None:
    """Refuse an index naming any run this dataset's database does not hold.

    An index's size and build-model are checked when it loads; whether its
    runs belong to *this* corpus was not, and an index built over another
    dataset loads cleanly. Every mapped run must be present — sampling is
    not enough, because the realistic wrong pairing is two corpora that
    share most of their runs, where a sample can consist entirely of shared
    ones. The database may hold more than the index (not everything need be
    embedded); the index may not hold more than the database, or semantic
    hits would name rows this dataset cannot return. Mappings are thousands
    of runs, so the full check is one startup query per dataset.
    """
    if router is None or pool is None:
        return
    mapped = router.run_ids_by_species()
    if not any(mapped.values()):
        return
    with pool.get_connection() as conn:
        rows = conn.execute("SELECT Run FROM sra_core").fetchall()
    db_runs = {row["Run"] for row in rows}
    for species, run_ids in mapped.items():
        missing = [run_id for run_id in run_ids if run_id not in db_runs]
        if missing:
            raise ValueError(
                f"dataset {name!r}: {len(missing)} of {len(run_ids)} runs in "
                f"its {species} index are not in its database (e.g. "
                f"{missing[0]!r}) — the index was built over a different "
                "corpus; rebuild it over this dataset's data"
            )


def _build_vector_router(
    settings: Settings, spec: DatasetSpec
) -> VectorRouter | None:
    """This dataset's species-routed vector search, or None.

    None only where the configuration says so: semantic search disabled, or
    no species backend named. A backend that *is* named must load, or startup
    fails naming the reason. Degrading instead — drop the backend, warn, serve
    keyword search — means a missing mount or broken index boots a server whose
    configured feature is silently gone, visible nowhere but a log line. A
    deployment that wants no semantic search says so by configuring none.
    """
    if not settings.semantic_search_enabled:
        return None
    backends = {
        species: VectorService(
            faiss_index_path=cfg["index_path"],
            run_mapping_path=cfg["mapping_path"],
            embedding_model_name=cfg["model_name"],
            model_cache_dir=settings.embedding_model_cache_dir,
            require_model_match=settings.require_index_model_match,
        )
        for species, cfg in spec.species.items()
    }
    if not backends:
        logger.info("dataset %s: no species backends configured", spec.name)
        return None

    router = VectorRouter(
        backends, settings.organism_to_species, settings.default_species
    )
    router.initialize()
    dropped = [sp for sp in backends if sp not in router.available_species()]
    if dropped:
        failures = router.load_failures
        detail = "; ".join(
            f"{sp}: {failures.get(sp, 'failed to load')}" for sp in dropped
        )
        raise ValueError(
            f"dataset {spec.name!r} configures semantic search for "
            f"{', '.join(dropped)} but the backend did not load ({detail}). "
            "Fix the index/mapping files or remove that species' "
            "configuration to serve this dataset without it"
        )
    logger.info(
        "dataset %s: semantic search on species=%s, %d vectors",
        spec.name,
        router.available_species(),
        router.total_vectors,
    )
    return router
