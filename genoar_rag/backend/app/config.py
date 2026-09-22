"""Application configuration via pydantic-settings."""

import os
import re
from dataclasses import dataclass

from pydantic_settings import BaseSettings

# What a deployment that names no datasets is serving: one body of data, drawn
# from the settings below. The name is internal — a single-dataset deployment
# never puts it in a URL or on screen.
DEFAULT_DATASET = "default"


@dataclass(frozen=True)
class DatasetSpec:
    """One body of data the service serves, and where it lives.

    A dataset is the settings below with some paths replaced. That is the whole
    idea: a deployment with one database configures nothing and gets what it
    always got, and a deployment whose dashboard describes a different corpus
    from its search page says so by naming a second dataset and pointing it at
    another file.

    `species` has the same shape `species_backends()` returns, so a dataset
    carries its own vector indexes. The embedding models are not per-dataset:
    two datasets of the same kind of data are embedded the same way, and making
    the model configurable per dataset would double the settings to express
    something no deployment has yet needed.
    """

    name: str
    label: str
    db_path: str
    db_name: str
    species: dict[str, dict[str, str]]


class Settings(BaseSettings):
    # Database backend: "mysql" (default, MySQL/MariaDB server for deployment) or
    # "sqlite" (file-based, used for dev/tests and as the automatic fallback when
    # MariaDB is unreachable at startup; see db.connection.create_pool_for).
    db_backend: str = "mysql"

    # SQLite backend
    db_path: str = "/data/genoar/sra_hybrid.db"

    # MySQL/MariaDB backend (used when db_backend == "mysql")
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "genoar"
    db_password: str = ""
    db_name: str = "genoar"

    db_pool_size: int = 5
    cors_origins: list[str] = ["http://localhost:3000"]
    # How long the dashboard figures and the filter option lists are held before
    # being recomputed. Both are whole-column scans whose answer only changes when
    # the database is rebuilt, which for a served corpus is between deployments —
    # so the ceiling on staleness is what this sets, not how often it is right.
    # Read at import by FilterService and StatsService.
    cache_ttl_seconds: int = 3600

    # --- Datasets ---
    #
    # The bodies of data this deployment serves, comma-separated. Left empty —
    # which is the case unless a deployment says otherwise — the service serves
    # one dataset built from the settings above and behaves exactly as it did
    # before datasets existed: no dataset appears in a URL, and the pages all
    # describe the same corpus.
    #
    #     DATASETS=main,atlas
    #
    # Each name may then be given its own paths, by name and in upper case:
    #
    #     DATASET_ATLAS_DB_NAME=genoar_atlas
    #     DATASET_ATLAS_DB_PATH=/data/genoar/atlas.db
    #     DATASET_ATLAS_HUMAN_FAISS_INDEX_PATH=/data/genoar/atlas_human.bin
    #     DATASET_ATLAS_HUMAN_RUN_MAPPING_PATH=/data/genoar/atlas_human.json
    #     DATASET_ATLAS_MOUSE_FAISS_INDEX_PATH=...
    #     DATASET_ATLAS_MOUSE_RUN_MAPPING_PATH=...
    #     DATASET_ATLAS_LABEL=Second dataset
    #
    # The first name is the primary: the corpus the deployment always served,
    # read from the top-level settings. Every later name must say where its
    # data lives — its own schema (mysql) or file (sqlite), and its own
    # index+mapping per species it wants semantic search on. Nothing falls
    # back to a top-level path: a secondary dataset that omitted one would
    # silently serve the primary's data under its own name, so a missing
    # database target refuses to start and an unnamed species backend is off.
    #
    # A label is only read where a page has to say which corpus it is showing,
    # and only a deployment serving more than one ever does.
    datasets: str = ""

    # --- Semantic search (Phase 2) ---
    semantic_search_enabled: bool = True
    default_similar_limit: int = 10
    embedding_model_cache_dir: str | None = None

    # --- Species-routed vector search ---
    # Paper design (GENOAR): human samples are encoded with SapBERT, mouse samples
    # with MiniLLM. Each species owns its own embedding model + FAISS index (the two
    # indexes may differ in dimension), and a query is routed to a species by its
    # `organism` filter. See vector_router.VectorRouter.
    #
    # A species backend exists only when its model/index/mapping are all set —
    # and then it must load, or startup fails naming the reason. Dropping it
    # with a warning instead would let a missing mount or broken index boot a
    # server whose configured feature is silently gone. To run without a
    # species' semantic search, unset its index/mapping (or set
    # SEMANTIC_SEARCH_ENABLED=false for none at all); keyword search is
    # unaffected either way.

    # Both indexes must be built with scripts/build_faiss_index.py first (the
    # historical repo index faiss_index.bin was built with PubMedBERT and
    # matches neither model below).

    # Human: SapBERT, 768-dim, ~170k samples.
    human_embedding_model_name: str = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
    human_faiss_index_path: str = "/data/genoar/faiss_human.bin"
    human_run_mapping_path: str = "/data/genoar/run_mapping_human.json"

    # Mouse: all-MiniLM-L6-v2, 384-dim, ~25k samples. The paper writes this as
    # "MiniLLM"; the model it evaluated is all-MiniLM-L6-v2, a sentence-embedding
    # model. The differing dimension is fine because each species owns a
    # separate index.
    mouse_embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    mouse_faiss_index_path: str = "/data/genoar/faiss_mouse.bin"
    mouse_run_mapping_path: str = "/data/genoar/run_mapping_mouse.json"

    # Map an `organism` metadata value to a species key. Unknown organisms fall back
    # to `default_species`.
    organism_to_species: dict[str, str] = {
        "Homo sapiens": "human",
        "Mus musculus": "mouse",
    }
    # Species used when a query carries no organism filter.
    default_species: str = "human"

    # --- Data access (no API key, by design) ---
    # Where a sample came from. GENOAR points at the archive's record page rather
    # than at the raw file: the data was deposited by its authors under the
    # archive's terms, and GENOAR holds no right to redistribute it or to dress a
    # link to it as its own download. The record page is also the more useful
    # destination — it carries the study, the terms and every format on offer,
    # where a bucket URL carries tens of gigabytes and nothing else.
    #
    # GEO where the crawler captured a GSM for the run, SRA otherwise; only part
    # of the corpus carries the GEO field.
    geo_record_url_template: str = (
        "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession}"
    )
    sra_record_url_template: str = "https://trace.ncbi.nlm.nih.gov/Traces/?run={run_id}"

    # Analysis-ready Cell Ranger output (HDF5). Point this at the pipeline's
    # success directory and mount it read-only, the same way db_path and the FAISS
    # indexes are wired. Stage 3 writes each sample to
    # <dir>/<run_id>/cellranger_output/outs/, so a sample's files are located by
    # layout. What each sample's provenance is, and therefore whether the service
    # offers it, is read from the receipt stage 3 leaves beside the output. See
    # processed_results_require_receipt below.
    # Blank disables the processed source.
    processed_results_dir: str = ""

    # Alternative for deployments that serve the same files from another host
    # (object storage, CDN) rather than a mounted disk. Used only when
    # processed_results_dir is unset, and since nothing can be checked remotely,
    # every run is advertised as available. Example:
    # "https://data.example.org/genoar/{run_id}/filtered_feature_bc_matrix.h5"
    processed_h5_url_template: str = ""

    # Extra places a run's outputs may sit under processed_results_dir,
    # comma-separated, each naming the run with {run_id}. The layout stage 3
    # writes ("{run_id}/cellranger_output/outs") is always searched; this is
    # for a deployment holding processed data written some other way — two
    # species in two trees from two runs of the pipeline, say. Example:
    #
    #     PROCESSED_RESULTS_LAYOUTS=HS/{run_id}/cellranger_output/outs,MM/{run_id}
    #
    # A file still only resolves inside processed_results_dir, so a mistyped
    # pattern serves nothing rather than something it should not.
    processed_results_layouts: str = ""

    # Offer only Cell Ranger output that carries a provenance receipt.
    #
    # Stage 3 writes a receipt (.genoar_cellranger.json) beside each sample's
    # output saying what produced it. `fresh` is work a run ran and recorded.
    # `adopted` is output an operator told a run to reuse on request
    # (GENOAR_ADOPT_PRIOR_RESULTS=1), which no run has verified.
    #
    # Output with no receipt was produced before the pipeline recorded
    # provenance, or it was produced elsewhere. Deployments serving such output
    # exist, so it stays on offer by default and this switch is what withholds
    # it. Set PROCESSED_RESULTS_REQUIRE_RECEIPT=true on a deployment whose
    # results were all produced by a run that writes receipts.
    #
    # Adopted output, and output whose receipt this service cannot read, are
    # withheld whichever way this is set. Both exist only where the pipeline
    # already writes receipts, so no deployment predating receipts can hold
    # either, and neither is output the pipeline can account for. Adoption
    # exists to let a run proceed over output nobody could vouch for. It has
    # never been a statement that the output is fit to hand out.
    #
    # This governs processed_results_dir only. Nothing about a file on another
    # host can be checked, so processed_h5_url_template is unaffected.
    processed_results_require_receipt: bool = False

    # Refuse to load an index whose recorded build-model differs from (or cannot be
    # verified against) the configured model. Prevents silently querying a PubMedBERT
    # index with SapBERT. Set REQUIRE_INDEX_MODEL_MATCH=false to knowingly run a
    # legacy index that has no build-model sidecar.
    require_index_model_match: bool = True

    model_config = {"env_prefix": "", "case_sensitive": False}

    def species_backends(self) -> dict[str, dict[str, str]]:
        """Return {species: {model_name, index_path, mapping_path}} for configured species.

        A species is included when all three of its values are non-empty, and
        absent when index and mapping are both unset — that is how a
        deployment turns a species off. Half a pair is a startup error, the
        same rule the per-dataset overrides follow: an index means nothing
        without the mapping that names its runs, and silently disabling the
        species would hide the typo that caused it.
        """
        raw = {
            "human": (
                self.human_embedding_model_name,
                self.human_faiss_index_path,
                self.human_run_mapping_path,
            ),
            "mouse": (
                self.mouse_embedding_model_name,
                self.mouse_faiss_index_path,
                self.mouse_run_mapping_path,
            ),
        }
        backends = {}
        for species, (model_name, index_path, mapping_path) in raw.items():
            if bool(index_path) != bool(mapping_path):
                missing = (
                    f"{species.upper()}_RUN_MAPPING_PATH" if index_path
                    else f"{species.upper()}_FAISS_INDEX_PATH"
                )
                raise ValueError(
                    f"half a {species} semantic backend is configured: "
                    f"{missing} is missing. Give both the index and its run "
                    f"mapping, or neither to leave {species} semantic search "
                    "off"
                )
            if index_path and mapping_path and not model_name:
                raise ValueError(
                    f"a {species} semantic backend is configured but "
                    f"{species.upper()}_EMBEDDING_MODEL_NAME is empty: "
                    "nothing can embed queries for it. Set the model, or "
                    "empty the index and mapping to leave the species off"
                )
            if model_name and index_path and mapping_path:
                backends[species] = {
                    "model_name": model_name,
                    "index_path": index_path,
                    "mapping_path": mapping_path,
                }
        return backends

    def _species_models(self) -> dict[str, str]:
        """Every species this service can embed, and the model that embeds it.

        Distinct from `species_backends()`, which is the primary corpus's
        *enabled* backends: a species the top-level settings leave off can
        still be embedded, so another dataset may serve it by naming its own
        index and mapping.
        """
        return {
            "human": self.human_embedding_model_name,
            "mouse": self.mouse_embedding_model_name,
        }

    def _species_override_pair(self, name: str, key: str) -> tuple[str, str]:
        """This dataset's index+mapping override for one species.

        Returns ("", "") where neither is given. Half a pair is a startup
        error: an index is meaningless without the mapping that names its
        runs, and silently pairing the other half from elsewhere is how a
        vector position ends up naming the wrong run.
        """
        index_path = self._dataset_override(name, f"{key}_faiss_index_path")
        mapping_path = self._dataset_override(name, f"{key}_run_mapping_path")
        if bool(index_path) != bool(mapping_path):
            missing = (
                f"{key}_run_mapping_path" if index_path
                else f"{key}_faiss_index_path"
            )
            raise ValueError(
                f"dataset {name!r} names half a {key} semantic backend: "
                f"DATASET_{name.upper()}_{missing.upper()} is missing. "
                "Give both the index and its run mapping, or neither to "
                f"leave {key} semantic search off for this dataset"
            )
        return (index_path or "", mapping_path or "")

    def _dataset_override(self, name: str, key: str) -> str | None:
        """`DATASET_<NAME>_<KEY>` from the environment, or None.

        Read here rather than declared as fields because the names are not
        known until a deployment chooses them. Pydantic would need a field per
        dataset per setting, which cannot be written for names nobody has
        picked yet.
        """
        value = os.environ.get(f"DATASET_{name.upper()}_{key.upper()}")
        return value.strip() or None if value else None

    def dataset_specs(self) -> list[DatasetSpec]:
        """Every dataset this deployment serves, in the order it named them.

        The first is the default: the one a request that names none is asking
        about, and the one every page shows unless told otherwise.

        With `datasets` empty this returns exactly one spec built from the
        top-level settings — the shape the service had before it could hold
        more than one, and the shape a deployment with one database keeps.
        """
        names = [n.strip() for n in self.datasets.split(",") if n.strip()]
        if not names:
            return [
                DatasetSpec(
                    name=DEFAULT_DATASET,
                    label="",
                    db_path=self.db_path,
                    db_name=self.db_name,
                    species=self.species_backends(),
                )
            ]

        for name in names:
            # A name is spelled into DATASET_<NAME>_* environment keys and
            # ?dataset= URL queries, so it is restricted to what both carry
            # unambiguously.
            if not re.fullmatch(r"[a-z0-9_]+", name):
                raise ValueError(
                    f"dataset name {name!r} is not usable: names appear in "
                    "environment keys and URLs, so they are limited to "
                    "[a-z0-9_]+"
                )

        for name in names:
            if names.count(name) > 1:
                raise ValueError(
                    f"dataset {name!r} is named more than once in DATASETS"
                )

        specs = []
        for position, name in enumerate(names):
            if position == 0:
                specs.append(self._primary_spec(name))
            else:
                specs.append(self._secondary_spec(name))

        targets: dict[str, str] = {}
        for spec in specs:
            target = spec.db_name if self.db_backend == "mysql" else spec.db_path
            if target in targets:
                raise ValueError(
                    f"datasets {targets[target]!r} and {spec.name!r} both "
                    f"reach database {target!r}: two names for one corpus is "
                    "the mislabeling datasets exist to prevent"
                )
            targets[target] = spec.name
        return specs

    def _primary_spec(self, name: str) -> DatasetSpec:
        """The first-named dataset: the top-level settings under a name.

        It is the corpus this deployment always served, so it reads the same
        settings a single-dataset deployment reads, with `DATASET_<NAME>_*`
        overrides allowed on top.
        """
        species = {}
        enabled = self.species_backends()
        for key, model_name in self._species_models().items():
            index_path, mapping_path = self._species_override_pair(name, key)
            if index_path and mapping_path:
                if not model_name:
                    raise ValueError(
                        f"dataset {name!r} configures a {key} semantic "
                        "backend but no model embeds that species: set "
                        f"{key.upper()}_EMBEDDING_MODEL_NAME"
                    )
                species[key] = {
                    "model_name": model_name,
                    "index_path": index_path,
                    "mapping_path": mapping_path,
                }
            elif key in enabled:
                species[key] = enabled[key]
        return DatasetSpec(
            name=name,
            label=self._dataset_override(name, "label") or name,
            db_path=self._dataset_override(name, "db_path") or self.db_path,
            db_name=self._dataset_override(name, "db_name") or self.db_name,
            species=species,
        )

    def _secondary_spec(self, name: str) -> DatasetSpec:
        """Any dataset after the first: nothing of it may be implied.

        A secondary dataset exists to serve a *different* corpus, so every
        artifact it reads must be named explicitly. Falling back to a
        top-level setting here would serve the primary's data under this
        dataset's name — the exact failure datasets exist to prevent — so a
        missing database target refuses to start instead.
        """
        db_path = self._dataset_override(name, "db_path") or ""
        db_name = self._dataset_override(name, "db_name") or ""
        if self.db_backend == "mysql" and not db_name:
            raise ValueError(
                f"dataset {name!r} needs DATASET_{name.upper()}_DB_NAME: "
                "without its own schema it would serve the default database "
                f"({self.db_name!r}) under another name"
            )
        if self.db_backend == "sqlite" and not db_path:
            raise ValueError(
                f"dataset {name!r} needs DATASET_{name.upper()}_DB_PATH: "
                "without its own file it would serve the default database "
                f"({self.db_path!r}) under another name"
            )
        species = {}
        for key, model_name in self._species_models().items():
            index_path, mapping_path = self._species_override_pair(name, key)
            if index_path and mapping_path:
                if not model_name:
                    raise ValueError(
                        f"dataset {name!r} configures a {key} semantic "
                        "backend but no model embeds that species: set "
                        f"{key.upper()}_EMBEDDING_MODEL_NAME"
                    )
                species[key] = {
                    "model_name": model_name,
                    "index_path": index_path,
                    "mapping_path": mapping_path,
                }
        return DatasetSpec(
            name=name,
            label=self._dataset_override(name, "label") or name,
            db_path=db_path,
            db_name=db_name,
            species=species,
        )


settings = Settings()
