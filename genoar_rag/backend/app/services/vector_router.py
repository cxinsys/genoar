"""Species-aware routing over per-species VectorService backends.

The GENOAR paper encodes human samples with SapBERT and mouse samples with MiniLLM,
each into its own FAISS index. VectorRouter owns one VectorService per species and
routes each query to the right backend:

  - search_by_text: routed by the query's `organism` filter (via organism_to_species).
    No organism -> default species. Multiple/unknown organisms -> search every
    available backend and merge by score.
  - search_similar_by_id: routed by which backend actually holds the run_id.

A backend that fails to load (missing file, model mismatch) is dropped and the
failure recorded in `load_failures`. What a dropped backend means is the
caller's decision: the dataset registry treats a configured backend that did
not load as a startup error, so a running server's configured species are all
live.

The public surface mirrors the parts of VectorService the search strategies use
(search_by_text/total_vectors) plus search_similar_by_id, so it is a drop-in for the
single-service wiring — with an optional `organisms` argument for routing.
"""

import logging

from app.services.vector_service import VectorSearchResult, VectorService

logger = logging.getLogger(__name__)


class VectorRouter:
    def __init__(
        self,
        backends: dict[str, VectorService],
        organism_to_species: dict[str, str],
        default_species: str,
    ):
        self._backends = backends
        self._organism_to_species = organism_to_species
        self._default_species = default_species
        self._initialized = False
        self._load_failures: dict[str, str] = {}

    def initialize(self) -> None:
        """Initialize each backend independently; drop any that fail.

        The failure is recorded per species so the caller can decide what a
        dropped backend means. The registry treats a configured backend that
        failed as a startup error rather than a degraded feature — see
        datasets._build_vector_router.
        """
        live: dict[str, VectorService] = {}
        for species, backend in self._backends.items():
            try:
                backend.initialize()
                live[species] = backend
                logger.info(
                    "Vector backend '%s' ready: %d vectors (%s)",
                    species,
                    backend.total_vectors,
                    backend.model_name,
                )
            except Exception as exc:
                self._load_failures[species] = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Vector backend '%s' unavailable — dropping it", species, exc_info=True
                )
        self._backends = live
        self._initialized = bool(live)

    @property
    def load_failures(self) -> dict[str, str]:
        """Why each dropped backend failed to load, by species."""
        return dict(self._load_failures)

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def total_vectors(self) -> int:
        return sum(b.total_vectors for b in self._backends.values())

    def available_species(self) -> list[str]:
        return sorted(self._backends)

    def run_ids_by_species(self) -> dict[str, list[str]]:
        """Every run each live backend's index describes, for corpus checks."""
        return {
            species: backend.run_ids()
            for species, backend in self._backends.items()
        }

    # -- routing ------------------------------------------------------------

    def _resolve_species(self, organisms: list[str] | None) -> list[str]:
        """Species backends to query for the given organism filter.

        No organism means every species, because that is what the page's "All"
        sends: it maps the choice to an empty list. Falling back to
        `default_species` here would make All search human only — "covid-19
        samples" across everything would return five human samples and none of
        the mouse lung ones, which is the wrong answer rather than a partial
        one.

        An organism naming a species with no live backend still narrows to
        nothing, rather than widening back to all: the caller asked for something
        specific and an empty result says so honestly.

        Scores from two species are not on one scale — human is indexed with
        SapBERT and mouse with MiniLM, and each model's cosine values mean
        something only against its own. Merging them ranks approximately. That is
        a known limit of searching both at once, not a reason to quietly search
        one.
        """
        if not organisms:
            return self.available_species()

        seen = set()
        species: list[str] = []
        for org in organisms:
            sp = self._organism_to_species.get(org)
            if sp and sp not in seen:
                seen.add(sp)
                species.append(sp)
        return [sp for sp in species if sp in self._backends]

    def search_by_text(
        self,
        query: str,
        top_k: int = 10,
        organisms: list[str] | None = None,
    ) -> list[VectorSearchResult]:
        if not query or not query.strip():
            return []

        targets = self._resolve_species(organisms)
        if not targets:
            return []

        if len(targets) == 1:
            return self._backends[targets[0]].search_by_text(query, top_k=top_k)

        # Multiple species: merge and sort by raw score, descending.
        #
        # So the similarity bar reads as sorted — which is what a "Similarity"
        # order promises the reader. The caveat is real and kept here: each
        # species is indexed with its own model — human with SapBERT, mouse with
        # MiniLM — and their cosines live on different scales, so this ordering
        # is only approximate across species. A query's phrasing can let one
        # model's scores dominate the top: "tumor microenvironment" once
        # returned 100 mouse and no human out of a 3,114-sample human corpus,
        # while "covid-19 samples" returned 98 human and 2 mouse. A per-rank
        # interleave keeps both represented but breaks the monotonic bar; the
        # trade here is made toward the bar. To restore interleave, take one
        # from each list per round instead of sorting the pool.
        #
        # Single-species search — every request that names an organism, which is
        # what the paper evaluates — does not come through here.
        per_species = [
            self._backends[sp].search_by_text(query, top_k=top_k) for sp in targets
        ]
        merged: list[VectorSearchResult] = [r for lst in per_species for r in lst]
        merged.sort(key=lambda r: r.score, reverse=True)
        return merged[:top_k]

    def search_similar_by_id(self, run_id: str, top_k: int = 10) -> list[VectorSearchResult]:
        for backend in self._backends.values():
            if backend.contains(run_id):
                return backend.search_similar_by_id(run_id, top_k=top_k)
        return []

    def close(self) -> None:
        for backend in self._backends.values():
            backend.close()
        self._backends = {}
        self._initialized = False
