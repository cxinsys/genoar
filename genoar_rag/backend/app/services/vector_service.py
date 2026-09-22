"""Vector search over one FAISS index built with one embedding model.

One instance backs a single species (see vector_router.VectorRouter): human uses
SapBERT, mouse all-MiniLM-L6-v2. The model an index was built with is recorded in
a `<index>.meta.json` sidecar and checked at load time.
"""

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VectorSearchResult:
    run_id: str
    score: float


class VectorService:
    """FAISS-based vector similarity search over one index.

    One instance per dataset per species — each owns its index and mapping.
    Loads the FAISS index eagerly at initialize() time (~1s for 580MB). The
    embedding model is loaded lazily on the first search_by_text() call and
    shared process-wide between instances configured with the same model.
    """

    def __init__(
        self,
        faiss_index_path: str,
        run_mapping_path: str,
        embedding_model_name: str,
        model_cache_dir: str | None = None,
        require_model_match: bool = False,
    ):
        self._faiss_index_path = faiss_index_path
        self._run_mapping_path = run_mapping_path
        self._embedding_model_name = embedding_model_name
        self._model_cache_dir = model_cache_dir
        self._require_model_match = require_model_match

        self._index: faiss.Index | None = None
        self._run_ids: list[str] = []
        self._run_id_to_pos: dict[str, int] = {}
        self._model = None
        self._encode_lock = threading.Lock()
        self._initialized = False

    def initialize(self) -> None:
        """Load FAISS index and run mapping from disk."""
        index_path = Path(self._faiss_index_path)
        mapping_path = Path(self._run_mapping_path)

        if not index_path.exists():
            raise FileNotFoundError(f"FAISS index not found: {index_path}")
        if not mapping_path.exists():
            raise FileNotFoundError(f"Run mapping not found: {mapping_path}")

        self._index = faiss.read_index(str(index_path))
        with open(mapping_path) as f:
            self._run_ids = json.load(f)

        if self._index.ntotal != len(self._run_ids):
            raise ValueError(
                f"Index size ({self._index.ntotal}) != mapping size ({len(self._run_ids)})"
            )

        # Build reverse lookup — first occurrence wins for duplicates
        self._run_id_to_pos = {}
        for i, rid in enumerate(self._run_ids):
            if rid not in self._run_id_to_pos:
                self._run_id_to_pos[rid] = i

        # Guard: confirm the index was built with the model we intend to query it with
        self._check_build_metadata()

        self._initialized = True
        logger.info(
            "VectorService initialized: %d vectors, dim=%d",
            self._index.ntotal,
            self._index.d,
        )

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def total_vectors(self) -> int:
        if self._index is None:
            return 0
        return self._index.ntotal

    @property
    def model_name(self) -> str:
        return self._embedding_model_name

    def contains(self, run_id: str) -> bool:
        """Whether this index holds a vector for run_id (used for id-based routing)."""
        return run_id in self._run_id_to_pos

    def run_ids(self) -> list[str]:
        """Every run this index describes, in mapping order.

        For cross-checking the index against the database claiming to be
        the same corpus.
        """
        return list(self._run_ids)

    def search_similar_by_id(
        self, run_id: str, top_k: int = 10
    ) -> list[VectorSearchResult]:
        """Find similar samples by run_id using stored vectors."""
        self._check_initialized()
        top_k = self._clamp_top_k(top_k)

        pos = self._run_id_to_pos.get(run_id)
        if pos is None:
            return []

        vec = self._index.reconstruct(pos).reshape(1, -1)
        # Request extra to account for self-match
        search_k = min(top_k + 1, self._index.ntotal)
        scores, indices = self._index.search(vec, search_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:  # FAISS sentinel
                continue
            candidate_id = self._run_ids[idx]
            if candidate_id == run_id:
                continue
            results.append(VectorSearchResult(run_id=candidate_id, score=float(score)))

        return results[:top_k]

    def search_by_text(
        self, query: str, top_k: int = 10
    ) -> list[VectorSearchResult]:
        """Encode a text query and search the FAISS index."""
        self._check_initialized()
        top_k = self._clamp_top_k(top_k)

        if not query or not query.strip():
            return []

        self._ensure_model()

        with self._encode_lock:
            embedding = self._model.encode(
                [query], normalize_embeddings=True, show_progress_bar=False
            )

        query_vec = np.array(embedding, dtype=np.float32)
        scores, indices = self._index.search(query_vec, top_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            results.append(
                VectorSearchResult(run_id=self._run_ids[idx], score=float(score))
            )

        return results

    def close(self) -> None:
        """Release this service's resources.

        Drops only this service's reference to the shared encoder; the cache
        keeps the model for whatever else is serving with it.
        """
        self._index = None
        self._run_ids = []
        self._run_id_to_pos = {}
        self._model = None
        self._initialized = False
        logger.info("VectorService closed")

    def _check_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("VectorService not initialized — call initialize() first")

    def _clamp_top_k(self, top_k: int) -> int:
        if top_k <= 0:
            return 1
        if self._index is not None and top_k > self._index.ntotal:
            return self._index.ntotal
        return top_k

    def _check_build_metadata(self) -> None:
        """Verify the index was built with the configured embedding model.

        scripts/build_faiss_index.py writes a `<index>.meta.json` sidecar recording the
        model an index was built with. Querying an index with a different model than it
        was built with returns wrong (but not erroring) results, so:
          - sidecar present + model mismatch -> refuse (raise);
          - sidecar absent -> cannot verify: refuse if require_model_match, else warn.
        """
        meta_path = Path(self._faiss_index_path + ".meta.json")
        if not meta_path.exists():
            if self._require_model_match:
                raise ValueError(
                    f"Index {self._faiss_index_path} has no build-model sidecar "
                    f"({meta_path.name}); cannot confirm it was built with "
                    f"'{self._embedding_model_name}'. Rebuild it with "
                    f"scripts/build_faiss_index.py, or set REQUIRE_INDEX_MODEL_MATCH=false."
                )
            logger.warning(
                "No build-model sidecar for %s — cannot verify it was built with '%s'",
                self._faiss_index_path,
                self._embedding_model_name,
            )
            return

        with open(meta_path) as f:
            meta = json.load(f)
        built_with = meta.get("model_name")
        if built_with and built_with != self._embedding_model_name:
            raise ValueError(
                f"Index {self._faiss_index_path} was built with '{built_with}' but the "
                f"configured model is '{self._embedding_model_name}'. Rebuild the index "
                f"with scripts/build_faiss_index.py or configure the matching model."
            )
        logger.info("Index build-model verified: %s", built_with or "(unrecorded)")

    def _ensure_model(self) -> None:
        """Lazy-load the embedding model on first use.

        Loaded through the process-wide cache: two services configured with
        the same model share one encoder object. Without that, a deployment
        serving several datasets loads the same SapBERT once per dataset —
        hundreds of megabytes each — for identical weights.
        """
        if self._model is not None:
            return

        logger.info("Loading embedding model: %s", self._embedding_model_name)
        entry = _shared_model(
            self._embedding_model_name, self._model_cache_dir
        )
        self._model = entry.model
        self._encode_lock = entry.encode_lock

        # Validate dimension compatibility
        model_dim = self._model.get_sentence_embedding_dimension()
        if model_dim != self._index.d:
            self._model = None
            raise ValueError(
                f"Model dimension ({model_dim}) != index dimension ({self._index.d})"
            )

        logger.info("Embedding model loaded (dim=%d)", model_dim)


# One encoder per model for the whole process. Each dataset gets its own
# VectorService (its own index), but the weights those services encode with
# are identical for the same model name, and SapBERT alone is hundreds of
# megabytes. Never evicted: the models a deployment uses are decided by its
# configuration, which does not change while it runs.
#
# The cache owns each model's encode lock too. The lock must live exactly as
# long as the object it serializes — a lock per service stops serializing
# anything the moment the encoder is shared.
class _SharedEncoder:
    def __init__(self, model):
        self.model = model
        self.encode_lock = threading.Lock()


_shared_models: dict = {}
_shared_models_lock = threading.Lock()


def _shared_model(model_name: str, cache_dir) -> _SharedEncoder:
    """The process-wide encoder for this model, created on first request.

    Creation happens under the cache lock so two datasets warming up at the
    same moment get one object, not one each. That serializes the first load
    of *different* models too, which is acceptable: it happens once per
    process, and the alternative is two multi-hundred-megabyte loads racing.
    """
    from sentence_transformers import SentenceTransformer

    key = (model_name, cache_dir)
    with _shared_models_lock:
        entry = _shared_models.get(key)
        if entry is None:
            entry = _SharedEncoder(
                SentenceTransformer(model_name, cache_folder=cache_dir)
            )
            _shared_models[key] = entry
    return entry
