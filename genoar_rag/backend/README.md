# GENOAR RAG Backend

[Korean / 한국어](README.ko.md)

Search API over 6,199 SRA samples. Structured SQL filtering, species-routed
semantic search over FAISS, sample metadata, statistics, downloads, and export.
FastAPI on MySQL/MariaDB or SQLite.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  FastAPI Application                                             │
│                                                                  │
│  ┌─────────┐ ┌────────┐ ┌───────┐ ┌──────┐ ┌──────┐ ┌────────┐  │
│  │ /samples│ │/search │ │/filters│ │/stats│ │/export│ │/health│  │
│  └────┬────┘ └───┬────┘ └───┬───┘ └──┬───┘ └───┬──┘ └───┬────┘  │
│       │          │          │        │         │        │        │
│  ┌────┴──────────┴──────────┴────────┴─────────┴────────┴─────┐  │
│  │                      Service Layer                         │  │
│  │  SampleService   SearchService (Strategy)   FilterService  │  │
│  │  DownloadService ExportService  StatsService  accession    │  │
│  │  VectorRouter ─┬─ VectorService (human)                    │  │
│  │                └─ VectorService (mouse)                    │  │
│  └────────────────────┬──────────────────────┬────────────────┘  │
│                       │                      │                   │
│  ┌────────────────────┴──────────┐           │                   │
│  │        Repository Layer       │           │                   │
│  │ SampleRepo ExtendedRepo       │           │                   │
│  │ SearchRepo                    │           │                   │
│  └────────────────────┬──────────┘           │                   │
│                       │                      │                   │
│  ┌────────────────────┴──────────┐           │                   │
│  │   Connection pool             │           │                   │
│  │   SQLite (WAL) or MySQL       │           │                   │
│  └────────────────────┬──────────┘           │                   │
└───────────────────────┼──────────────────────┼───────────────────┘
                        │                      │
         ┌──────────────┴─────┐   ┌────────────┴──────────────┐
         │ sra_hybrid.db      │   │ faiss_human.bin  (500 MB) │
         │ or MariaDB         │   │ faiss_mouse.bin  ( 36 MB) │
         │ (read-only, 931MB) │   │ results/success/ (HDF5)   │
         └────────────────────┘   └───────────────────────────┘
```

### Layers

| Layer | Responsibility | Files |
|-------|----------------|-------|
| **Router** | HTTP request/response, parameter validation | `app/routers/*.py` |
| **Service** | Business logic, caching, batch fetches that avoid N+1, vector routing | `app/services/*.py` |
| **Repository** | SQL queries, data access abstraction | `app/repositories/*.py` |
| **DB** | Connection pool, WAL mode, MySQL adapter | `app/db/connection.py` |
| **Model** | Pydantic schemas, validation | `app/models/*.py` |
| **Util** | Normalization, dynamic SQL builder | `app/utils/*.py` |
| **Scripts** | Index build, SQLite → MariaDB migration | `scripts/*.py` |

### Design patterns

- **Strategy**: `SearchStrategy` has three implementations — `SQLSearchStrategy`,
  `SemanticSearchStrategy`, `HybridSearchStrategy` — selected by the request's
  `search_mode`
- **Datasets**: a `DatasetRegistry` holds one connection pool and one
  `VectorRouter` per configured dataset (`DATASETS` + `DATASET_<NAME>_*`;
  one nameless dataset from the top-level settings when unset). The dataset
  is chosen per request by the `?dataset=` dependency, so repositories and
  services never know datasets exist — they are handed a connection that
  already points at the right corpus. See the top-level README's
  "Multiple datasets" for deployment
- **Species routing**: `VectorRouter` owns one `VectorService` per species, each
  with its own embedding model and FAISS index, chosen by the `organism` filter.
  Embedding models are shared process-wide between datasets using the same model
- **Fail-closed loading**: a configured vector backend must load, or startup
  fails naming the reason — a missing or broken index is never a silently
  absent feature. A species is served without semantic search only by
  explicitly emptying its index+mapping pair; keyword search is unaffected
  either way
- **Repository**: `sra_core` / `sra_extended` split, EAV queries optimized
- **Connection pool**: 5 connections by default, works against a read-only DB
- **In-memory cache**: filter options and statistics, 1-hour TTL
- **DI**: request-scoped DB connections through FastAPI `Depends()`

---

## Database Schema

The column names below are the ones the code queries. They are not lowercase
conventions — `Run`, `Sample_Name`, `STR_tis` and friends are the names as they
exist in the database the pipeline produces.

### sra_core (6,199 rows)

| Column | Type | Description | Used for |
|--------|------|-------------|----------|
| Run | TEXT | SRA Run ID | Lookup key, sorting (`sort_by=run_id`) |
| Series | TEXT | GEO Series ID | `series` filter |
| BioSample | TEXT | BioSample ID | — |
| Sample_Name | TEXT | Submitter's sample name | Detail response |
| tissue | TEXT | Tissue (86.5% populated) | `tissue` filter, keyword search |
| cell_type | TEXT | Cell type (46.7% populated) | `cell_type` filter, keyword search |
| disease_state_modified | TEXT | Normalized disease state | Surfaced as `disease` in the API |
| treatment, sex, Age, strain, genotype | TEXT | Submitter free text | `treatment`/`genotype` are keyword-searchable |
| Platform | TEXT | Sequencing platform | `platform` filter |
| Instrument | TEXT | Instrument model | — |
| size | REAL | File size | Detail response |
| STR_tis, STR_dis, STR_cell | TEXT | Normalized concept strings | — |
| CUI_tis, CUI_dis, CUI_cell | TEXT | UMLS concept codes | — |
| created_at | TIMESTAMP | Row creation time | Sorting (`sort_by=release_date`) |

Two API names differ from their column: `disease` reads
`disease_state_modified`, and `sort_by=release_date` sorts on `created_at`
(`app/utils/query_builder.py`, `SORT_COLUMN_MAP`).

### sra_extended (126,646 rows) — EAV

| Column | Type | Description |
|--------|------|-------------|
| Run | TEXT | FK → sra_core.Run |
| field_name | TEXT | Metadata field name (38 distinct) |
| field_value | TEXT | Field value |
| data_type | TEXT | Data type (text, numeric) |

**API filter → EAV mapping**:

| API filter | EAV field_name | Scale |
|------------|----------------|-------|
| `organism` | `Organism` | Homo sapiens 3,114, Mus musculus 3,085 |
| `assay_type` | `Assay Type` | RNA-Seq 5,981, OTHER 218 |
| `library_source` | `LibrarySource` | TRANSCRIPTOMIC 4,791, TRANSCRIPTOMIC SINGLE CELL 1,408 |
| `disease` | `disease_state`, `disease`, `diagnosis`, `condition` + 5 more (9 total) | Aggregates fragmented disease fields |

A third table, `sra_vectors`, exists in the database and is carried over by the
migration script, but the API does not read it — vector search uses the FAISS
indexes on disk.

---

## API Endpoints

### `GET /api/v1/samples`

Sample list with pagination.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `offset` | int | 0 | Start position (≥0) |
| `limit` | int | 20 | Page size (1–100) |

**Response**: `PaginatedResponse[SampleSummary]`

```json
{
  "items": [
    {
      "run_id": "SRR22351028",
      "series": "GSE218390",
      "biosample": "SAMN31805197",
      "tissue": "Bone Marrow",
      "cell_type": "Lin-CD34+ cells",
      "organism": "Homo sapiens",
      "assay_type": "RNA-Seq",
      "library_source": "TRANSCRIPTOMIC",
      "platform": "ILLUMINA",
      "instrument": "Illumina NovaSeq 6000"
    }
  ],
  "total": 6199,
  "offset": 0,
  "limit": 20
}
```

### `GET /api/v1/samples/{accession}`

One sample's detail (core + extended metadata).

`{accession}` takes either an SRA run id (`SRR...`) or a GEO sample id
(`GSM...`). A GSM is resolved to a run id through the `GEO_Accession (exp)` field
in `sra_extended`. That field is present for only part of the corpus, so a GSM
that does not resolve does not prove the sample is absent.

**Response**: `SampleDetail`

```json
{
  "run_id": "SRR22351028",
  "series": "GSE218390",
  "tissue": "Bone Marrow",
  "cell_type": "Lin-CD34+ cells",
  "organism": "Homo sapiens",
  "treatment": "Untreated",
  "genotype": "PTPN11 A72T",
  "extended_fields": [
    {"field_name": "Organism", "field_value": "Homo sapiens", "data_type": "text"},
    {"field_name": "library_layout", "field_value": "PAIRED", "data_type": "text"}
  ]
}
```

**Errors**: `404` — `SampleNotFoundError`

### `GET /api/v1/samples/{accession}/series`

Every sample in the same series.

**Response**: `PaginatedResponse[SampleSummary]`

### `GET /api/v1/samples/{accession}/similar`

Semantically similar samples, from the vector index that holds this run. The
sample's own vector is used as the query, so no text is encoded and no model is
loaded for this call.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 10 | Number of neighbours (1–100) |

Each item carries `similarity_score` plus tissue, cell type, disease, organism,
and assay type joined from the database.

**Errors**: `503` — no vector backend is loaded. A run that exists but is in no
index returns an empty list rather than an error.

### `GET /api/v1/samples/{accession}/download`

Where this sample's data can be obtained. **No API key is required.**

Raw reads are not offered here. They were deposited in GEO and SRA by the
studies' authors under those archives' terms, so the response links to the
archive's record for the run — GEO where the crawler captured a GSM, SRA
otherwise — rather than to the file.

Cell Ranger outputs (HDF5) are read from the directory `PROCESSED_RESULTS_DIR`
points at. Stage 3 writes each sample to `<dir>/<run_id>/cellranger_output/outs/`
(the `cellranger_count` rule in `srr_pipeline_package/pipeline/Snakefile_hs.smk`),
so a listing of that directory answers where a run's output is.

Being on disk is where the question starts. The results directory outlives any one
run, so a matrix sitting in it says nothing by itself about which input produced
it. Stage 3 writes a receipt (`.genoar_cellranger.json`) beside each run's output,
naming the run that produced it and saying whether that run verified it. This
endpoint reads the same receipt before offering the files.

| `provenance.status` | What the receipt says | Offered |
|---------------------|-----------------------|---------|
| `verified` | A run's own Cell Ranger produced the output and recorded it | Always |
| `adopted` | An operator told a run to reuse output nobody could account for (`GENOAR_ADOPT_PRIOR_RESULTS=1`) | Never |
| `unreadable` | A receipt is there and this service cannot act on it. It does not parse, or it names a provenance the service does not know | Never |
| `unrecorded` | There is no receipt. The output predates provenance tracking, or it was produced outside this pipeline | Unless `PROCESSED_RESULTS_REQUIRE_RECEIPT` is true |

`PROCESSED_RESULTS_REQUIRE_RECEIPT` governs the `unrecorded` row alone, and it
defaults to false so that deployments whose results predate receipts keep serving
them. Adopted and unreadable output is withheld whatever the setting says.
Neither can exist unless the pipeline that wrote it already writes receipts, so
no deployment predating receipts holds either one.

The response carries a `provenance` object, reported whether or not the output is
offered, so a caller can tell a withheld sample from an unprocessed one. Beside
`status` it carries `verified`, `offered`, `receipt_run_id`, `recorded_at` and a
`note` saying what the deployment did and what to do about it. Each processed
source repeats the status in its own `provenance` field, so a client reading only
`sources` can tell verified output from the rest.

An unprocessed run comes back with `available: false` and the reason, and no
`provenance`.

```json
{
  "run_id": "SRR5799166",
  "geo_accession": "GSM2692027",
  "requested_accession": "GSM2692027",
  "requires_api_key": false,
  "provenance": {
    "status": "verified", "verified": true, "offered": true,
    "receipt_run_id": "run-20250114T091500Z-4127-8891",
    "recorded_at": "2025-01-14T09:41:02Z",
    "note": "A pipeline run produced this output from this sample's input and left a receipt saying so."
  },
  "sources": [
    {"kind": "processed_h5", "available": true, "action": "download",
     "size_bytes": 20971520, "provenance": "verified",
     "url": "/api/v1/samples/SRR5799166/files/filtered_feature_bc_matrix.h5"},
    {"kind": "geo_record", "available": true, "action": "visit",
     "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM2692027"}
  ]
}
```

Withheld output is named and not handed over. One `processed_h5` source comes
back `available: false`, carrying the provenance status and a `note` saying why:

```json
{"kind": "processed_h5", "available": false, "provenance": "adopted",
 "note": "This output was adopted on an operator's instruction ..."}
```

The response also carries `processing` — the Cell Ranger version, the detected
chemistry and the reference the matrix was aligned against — read out of the
matrix file rather than recorded beside it, so it cannot fall out of step with
the file it describes. Absent for a run with no processed matrix, and absent for
output the service withholds. Describing how a file was made while refusing to
hand it over would make the same claim in a quieter place.

Files served from another host (`PROCESSED_H5_URL_TEMPLATE`) carry no
`provenance`. Nothing about them can be checked from here, so nothing about them
is claimed, and `PROCESSED_RESULTS_REQUIRE_RECEIPT` has no bearing on them.

`action` distinguishes a file from a link. `download` is something GENOAR
serves — the metadata it curated, the matrices its pipeline produced. `visit` is
the archive's own record page, where the raw reads are held: they were deposited
there by their authors under that archive's terms, and GENOAR neither
redistributes them nor dresses a link to them as its own download. Runs with a
GEO accession get `geo_record`, the rest `sra_record`.

**Errors**: `404` — no sample for that accession

### `GET /api/v1/samples/{accession}/files/{filename}`

Streams a processed output. Only the pipeline's known filenames resolve, so the
path cannot be steered outside the results directory.

| filename | Approx. size | Purpose |
|----------|-------------|---------|
| `filtered_feature_bc_matrix.h5` | 20 MB | Default input for downstream analysis |
| `raw_feature_bc_matrix.h5` | 31 MB | Unfiltered matrix |
| `molecule_info.h5` | 265 MB | One record per detected molecule, before counts are summed per gene |

**Errors**: `404` — unknown filename, the run is unprocessed, the output is one
this deployment withholds, or `PROCESSED_RESULTS_DIR` is unset

This route resolves through the same provenance check the download listing uses,
so a sample whose output is withheld is not reachable by typing its filename
either.

### `GET /api/v1/search`

Structured search: filter combinations, keyword, and — when a vector backend is
loaded — semantic and hybrid modes.

**Filters** (all query parameters):

| Parameter | Type | Description |
|-----------|------|-------------|
| `organism` | list[str] | Species (multi-select). Also selects the semantic index |
| `tissue` | list[str] | Tissue (multi-select, case-insensitive) |
| `cell_type` | list[str] | Cell type (multi-select) |
| `assay_type` | list[str] | Assay type (multi-select) |
| `library_source` | list[str] | Library source (multi-select) |
| `disease` | list[str] | Disease (multi-select, 9 EAV fields aggregated) |
| `platform` | list[str] | Platform (multi-select) |
| `series` | str | GEO Series ID (single value) |
| `keyword` | str | Text search (tissue, cell_type, treatment, genotype) |
| `search_mode` | str | `sql` (default), `semantic`, `hybrid` |
| `offset` | int | Start position (default 0) |
| `limit` | int | Page size (default 20, max 100) |
| `sort_by` | str | `run_id` or `release_date` |
| `sort_order` | str | `asc` or `desc` |

In `semantic` mode the keyword is encoded and matched against the vector index;
an empty keyword returns no results rather than everything. In `hybrid` mode the
semantic candidates (up to `SEMANTIC_POOL_SIZE`, 500) are intersected with the
SQL filters. Both return `503` when no vector backend is loaded.

**Example**:
```
GET /api/v1/search?tissue=bone+marrow&organism=Homo+sapiens&limit=3
```

**Response**: `SearchResponse`

```json
{
  "items": [],
  "total": 3513,
  "offset": 0,
  "limit": 3,
  "search_mode": "sql",
  "filters_applied": {
    "organism": ["Homo sapiens"],
    "tissue": ["bone marrow"],
    "cell_type": [],
    "assay_type": [],
    "library_source": [],
    "disease": [],
    "platform": [],
    "series": null,
    "keyword": null
  }
}
```

### `GET /api/v1/export`

Exports the filtered samples' core metadata as CSV or JSON. The filter
parameters match `/api/v1/search`, and no API key is required.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `format` | `csv` | `csv` or `json` |
| `max_rows` | 250000 | A cap, not a page size. The default is above the corpus, so an export left alone returns everything the filters match |
| `accession` | — | Export a single sample by SRR or GSM; other filters are then ignored |

Columns are an explicit allowlist. The paper states that GEO submission metadata
is excluded from the download section for copyright reasons, so only identifiers,
archive-technical fields, and GENOAR's own normalized tissue/cell_type/disease
with their UMLS codes are emitted. Submitter free text (`Sample_Name`,
`treatment`, `sex`, `Age`, `strain`, `genotype`) and all of `sra_extended` stay
out. CSV streams; JSON is materialized because it has to be one document.

### `GET /api/v1/filters`

Filter options with counts, cached for an hour. Seven categories: tissue,
cell_type, platform, organism, assay_type, library_source, disease.

```json
{
  "categories": [
    {
      "name": "tissue",
      "total_distinct": 50,
      "values": [
        {"value": "Blood", "count": 12089},
        {"value": "Bone Marrow", "count": 8234}
      ]
    },
    {
      "name": "organism",
      "total_distinct": 41,
      "values": [
        {"value": "Homo sapiens", "count": 170661},
        {"value": "Mus musculus", "count": 24665}
      ]
    }
  ]
}
```

### `GET /api/v1/stats`

Dashboard statistics, cached for an hour.

```json
{
  "total_samples": 6199,
  "total_series": 275,
  "organism_distribution": [
    {"label": "Homo sapiens", "count": 170661, "percentage": 86.3},
    {"label": "Mus musculus", "count": 24665, "percentage": 12.5}
  ],
  "assay_distribution": [],
  "platform_distribution": [],
  "library_source_distribution": []
}
```

### `GET /health`

Health check, and the authoritative answer on what semantic search can serve:

```json
{"status": "ok", "datasets": ["default"], "semantic_search": true, "semantic_species": ["human", "mouse"]}
```

A configured backend that fails to load stops startup rather than being
dropped, so on a running server `semantic_species` is both what was configured
and what works.

### `GET /api/v1/datasets`

The datasets this deployment serves — what a client needs before it can pass
`?dataset=`:

```json
{"items": [{"name": "main", "label": "Curated", "is_default": true},
           {"name": "atlas", "label": "Atlas", "is_default": false}],
 "default": "main", "is_split": true}
```

Every `/api/v1` endpoint accepts `?dataset=<name>`; omitted means the default,
and an unknown name is 404 rather than a fallback. A single-dataset deployment
reports one entry and `is_split: false`, and its URLs carry no dataset.

---

## Semantic Search

One embedding model and one FAISS index per species, as the GENOAR paper
specifies.

| Species | Model | Dim | Vectors | Index size |
|---------|-------|-----|---------|-----------|
| human | `cambridgeltl/SapBERT-from-PubMedBERT-fulltext` | 768 | 3,114 | 9.1 MB |
| mouse | `sentence-transformers/all-MiniLM-L6-v2` | 384 | 3,085 | 4.5 MB |

### Routing

`VectorRouter` picks backends from the request's `organism` filter
(`ORGANISM_TO_SPECIES`). No organism, or an unmapped one, falls back to
every loaded species — that is what the page's "All" sends. When a query maps to several species, each is searched
and the results are **interleaved by rank**. Each species is indexed with its own
model (human SapBERT, mouse MiniLM), so their cosines are not on one scale:
sorting the combined pool by score ranked by whichever model scored higher for
that wording, not by relevance — "tumor microenvironment" returned 100 mouse
samples and none of the 3,114 human ones, while "covid-19 samples" returned 98
human and 2 mouse though the mouse hits scored higher. A rank is comparable
where a score is not, so each round takes one from each species, better score
first.

A request naming an organism — the path the paper evaluates — uses a single
index and does not come through this merge.

`/similar` routes differently: it asks each backend which one holds the run id,
so it needs no organism hint.

### Build verification

`scripts/build_faiss_index.py` writes a `<index>.meta.json` sidecar recording the
model an index was built with. Querying an index with a model it was not built
with returns wrong results without erroring, so the service checks at load time:

| Sidecar | Model | Behaviour |
|---------|-------|-----------|
| present | matches | loads |
| present | differs | refuses to load (raises) |
| absent | unverifiable | refuses if `REQUIRE_INDEX_MODEL_MATCH=true` (default), else warns and loads |

A backend that refuses stops startup, with the mismatch in the error. To serve
without that species instead, empty its index and mapping settings — an
explicitly absent backend is configuration; a failing one is a fault.

```bash
python scripts/build_faiss_index.py --db ../data/sra_hybrid.db \
  --model cambridgeltl/SapBERT-from-PubMedBERT-fulltext \
  --organism "Homo sapiens" \
  --out-index ../data/faiss_human.bin \
  --out-mapping ../data/run_mapping_human.json
```

Other options: `--cache-dir`, `--batch-size` (default 64), `--limit` (cap rows
for a test build).

The embedding model itself is loaded lazily, on the first text query — starting
the service does not pay for it, and `/similar` never does.

---

## Project Structure

```
backend/
├── pyproject.toml              # Dependencies and project settings
├── Dockerfile                  # Python 3.12 image
├── app/
│   ├── main.py                 # App factory, lifespan (DB pool + vector router), CORS, handlers
│   ├── config.py               # pydantic-settings configuration
│   ├── dependencies.py         # DI (DB pool, vector service)
│   ├── exceptions.py           # SampleNotFoundError
│   ├── db/
│   │   └── connection.py       # SQLiteConnectionPool (WAL) + MySQLConnectionPool
│   ├── models/
│   │   ├── common.py           # PaginationParams, SortParams, PaginatedResponse
│   │   ├── sample.py           # SampleSummary, SampleDetail, ExtendedField
│   │   ├── search.py           # SearchFilters, SearchMode, SearchResponse
│   │   ├── semantic.py         # SimilarSampleItem, SimilarSamplesResponse
│   │   ├── download.py         # DownloadSource, SampleDownloadResponse
│   │   ├── filters.py          # FilterValue, FilterCategory, FilterOptionsResponse
│   │   └── stats.py            # DashboardStats, DistributionItem
│   ├── routers/
│   │   ├── samples.py          # /api/v1/samples/*  (detail, series, similar, download, files)
│   │   ├── search.py           # /api/v1/search
│   │   ├── filters.py          # /api/v1/filters
│   │   ├── stats.py            # /api/v1/stats
│   │   └── export.py           # /api/v1/export
│   ├── services/
│   │   ├── sample_service.py   # Sample detail, batch fetches that avoid N+1
│   │   ├── search_service.py   # SQL / Semantic / Hybrid strategies
│   │   ├── vector_router.py    # Species routing across vector backends
│   │   ├── vector_service.py   # One FAISS index + model, with build verification
│   │   ├── accession.py        # SRR / GSM resolution
│   │   ├── download_service.py # Raw + processed source discovery
│   │   ├── export_service.py   # Column allowlist, CSV/JSON emission
│   │   ├── filter_service.py   # Filter options + 1h TTL cache
│   │   └── stats_service.py    # Dashboard statistics + 1h TTL cache
│   ├── repositories/
│   │   ├── base.py             # BaseRepository (fetchone/fetchall/fetchval)
│   │   ├── sample_repository.py
│   │   ├── extended_repository.py
│   │   └── search_repository.py
│   └── utils/
│       ├── normalization.py    # Case normalization, LIKE escaping, disease field names
│       └── query_builder.py    # Dynamic SQL builder (parameterized)
├── scripts/
│   ├── build_faiss_index.py    # Per-species index build + meta sidecar
│   └── migrate_sqlite_to_mysql.py
└── tests/
    ├── conftest.py             # Test DB fixture (representative samples + EAV data)
    ├── unit/                   # 161 test functions
    ├── integration/            # 35 test functions
    └── api/                    # 80 test functions
```

---

## Running

### Docker

```bash
cd genoar_rag/backend

# Build
docker build -t genoar-backend .

# Tests
docker run --rm -v $(pwd):/app genoar-backend python -m pytest tests/ -v

# The whole stack (backend + frontend) comes from the parent directory
cd .. && docker compose up
# → http://127.0.0.1:8001/health (no Swagger UI; the port is loopback-only)
```

The image bakes in the legacy PubMedBERT weights at build time. The configured
models (SapBERT, MiniLM) are not baked, so the first text query downloads them
into `EMBEDDING_MODEL_CACHE_DIR` unless that cache is already populated.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_BACKEND` | `mysql` | `mysql` or `sqlite` |
| `DB_PATH` | `/data/genoar/sra_hybrid.db` | SQLite path (`DB_BACKEND=sqlite`, also the fallback target) |
| `DB_HOST` | `localhost` | MySQL/MariaDB host |
| `DB_PORT` | `3306` | MySQL/MariaDB port |
| `DB_USER` | `genoar` | MySQL/MariaDB user |
| `DB_PASSWORD` | (empty) | MySQL/MariaDB password |
| `DB_NAME` | `genoar` | MySQL/MariaDB database |
| `DB_POOL_SIZE` | `5` | Connection pool size |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed CORS origins |
| `CACHE_TTL_SECONDS` | `3600` | Cache TTL |
| `SEMANTIC_SEARCH_ENABLED` | `true` | Master switch for vector search |
| `HUMAN_EMBEDDING_MODEL_NAME` | SapBERT | Human embedding model |
| `HUMAN_FAISS_INDEX_PATH` / `HUMAN_RUN_MAPPING_PATH` | `/data/genoar/faiss_human.bin` / `...json` | Human index and mapping |
| `MOUSE_EMBEDDING_MODEL_NAME` | all-MiniLM-L6-v2 | Mouse embedding model |
| `MOUSE_FAISS_INDEX_PATH` / `MOUSE_RUN_MAPPING_PATH` | `/data/genoar/faiss_mouse.bin` / `...json` | Mouse index and mapping |
| `REQUIRE_INDEX_MODEL_MATCH` | `true` | Refuse an index that cannot be shown to match the model |
| `DEFAULT_SPECIES` | `human` | Legacy. No organism filter now searches every loaded species |
| `DEFAULT_SIMILAR_LIMIT` | `10` | Default neighbour count |
| `EMBEDDING_MODEL_CACHE_DIR` | (unset) | HuggingFace cache directory |
| `PROCESSED_RESULTS_DIR` | (empty) | Stage 3 success directory. Empty disables processed downloads |
| `PROCESSED_RESULTS_REQUIRE_RECEIPT` | `false` | Withhold output that carries no provenance receipt. Adopted and unreadable output is withheld either way. `PROCESSED_RESULTS_DIR` only |
| `PROCESSED_H5_URL_TEMPLATE` | (empty) | Used only when `PROCESSED_RESULTS_DIR` is unset |
| `SRA_DOWNLOAD_URL_TEMPLATE` | NCBI ODP bucket | Where raw reads are advertised from |

### Database backends (SQLite / MariaDB)

The same code serves both. Local development and tests use the file-based
SQLite backend; deployments set `DB_BACKEND=mysql` (the default) and point at a
MySQL/MariaDB server. Only the backend selection differs — the query code is
shared, with a thin adapter translating SQLite's `?` placeholders to MySQL's `%s`.

With `DB_BACKEND=mysql`, a server that cannot be reached at startup falls back to
the SQLite file at `DB_PATH`; if that file is missing too, startup fails rather
than serving nothing. `DB_BACKEND=sqlite` has no fallback.

To move the pipeline's `sra_hybrid.db` into MariaDB, use
`scripts/migrate_sqlite_to_mysql.py`. It creates the four tables (`sra_core`,
`sra_extended`, `sra_vectors`, `sra_series`) in MariaDB, copies every row including ids, and
verifies the row counts against the source at the end.

```bash
cd genoar_rag/backend

# 1. Start MariaDB (the compose mysql profile, from genoar_rag/)
#    docker compose --profile mysql up -d mariadb

# 2. Copy SQLite -> MariaDB. Use --drop for refreshes too: --upsert never deletes,
#    so rows dropped from the source stay live (the 197k -> 6,199 cut is that case)
python scripts/migrate_sqlite_to_mysql.py \
  --sqlite ../data/sra_hybrid.db \
  --host 127.0.0.1 --port 3306 --user genoar --password <PW> --db genoar \
  --drop

# 3. Run against MariaDB
DB_BACKEND=mysql DB_HOST=127.0.0.1 DB_USER=genoar DB_PASSWORD=<PW> DB_NAME=genoar \
  uvicorn app.main:app
```

The MariaDB backend needs the `pymysql` driver (included in `pyproject.toml`).
The schema keeps every text column as `TEXT` and puts prefix indexes only on the
columns that need them (`Run`, `field_name`, `STR_*`), so the copy cannot fail on
value length.

### Dependencies

```toml
[project]
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "pydantic>=2.5.0",
    "pydantic-settings>=2.1.0",
    "faiss-cpu>=1.7.4",
    "sentence-transformers>=2.2.0",
    "numpy>=1.24.0",
    "pymysql>=1.1.0",
]

[project.optional-dependencies]
test = [
    "pytest>=7.0.0",
    "httpx>=0.25.0",
]
```

---

## Tests

Last full run: **399 passed, 3 skipped**.

| Category | Test functions | Covers |
|----------|---------------|--------|
| Unit | 161 | Models, normalization, SQL builder, search/filter/stats/sample services, accession, download, export, vector service and router, index build |
| Integration | 35 | DB connection, sample/extended/search repositories, vector integration |
| API | 80 | samples, search, filters, stats, download, similar, export endpoints |

```bash
python -m pytest tests/ -q                  # everything
python -m pytest tests/unit -q              # one category
python -m pytest tests/api/test_search_api.py -q
```

Tests run against a fixture database built in `conftest.py`, so no production
data or FAISS index is required.

### Figures measured against the real database

```
Total samples: 6,199          Total series: 888
sra_core:      6,199 rows     sra_series: 7,069 run-study pairs
sra_extended:  126,646 rows across 38 distinct field names
sra_vectors:   6,199 rows     tissue populated 100%   cell_type populated 35.8%

Filter categories:
  tissue:        295 distinct, top=Lung (406)
  cell_type:     310 distinct, top=CD45+ cells (134)
  platform:        1 distinct, top=ILLUMINA (6,199)
  organism:        2 distinct, top=Homo sapiens (3,114)
  assay_type:      2 distinct, top=RNA-Seq (5,981)
  library_source:  2 distinct, top=TRANSCRIPTOMIC (4,791)
  disease:        55 distinct, top=Control Groups (152)
```

Measured 2026-08-04, after the database was cut to the runs the curated
filtered tables name. The figures before that describe the whole GEO crawl —
197,757 runs across 41 organisms — which is not what the service serves.

---

## Edge Cases

| Case | Handling |
|------|----------|
| tissue case mismatch (blood/Blood/BLOOD) | `LOWER()` comparison |
| cell_type 53% NULL | Optional field, NULLs excluded |
| Fragmented disease data (98 field names) | 9 principal fields aggregated |
| EAV query performance | `Run IN (SELECT ...)` subquery, JOINs avoided |
| Keyword SQL injection | Parameterized queries only, LIKE metacharacters escaped |
| Empty filter request | Returns the full paginated sample list |
| Unknown run id | 404 SampleNotFoundError |
| GSM that resolves to nothing | 404 — the field covers only part of the corpus, so this is not proof of absence |
| Large result sets | limit capped at 100, separate COUNT query |
| N+1 on extended fields | Run IDs batched into one IN query |
| Read-only database | WAL/query_only PRAGMAs skipped gracefully on failure |
| MariaDB unreachable | Falls back to the dataset's own SQLite file if it exists and holds the serving tables; auth/schema errors refuse startup instead |
| Index built with another model | Refused at load; startup fails naming the mismatch |
| Semantic query with no backend | 503 rather than silently empty results |
| Empty semantic keyword | Returns nothing rather than everything |
| Run absent from every index | `/similar` returns an empty list, not an error |
| Processed output the pipeline cannot account for | Named in the download response with `available: false` and the reason, withheld from `/files`, and `processing` omitted |
| Sample's own vector in its neighbours | Filtered out of `/similar` results |

---

## Performance (not applied)

With write access to the database, a composite index would speed up EAV queries:

```sql
CREATE INDEX IF NOT EXISTS idx_extended_field_value
  ON sra_extended(field_name, field_value);
```

**Expected effect**: EAV filter queries ~700ms → <50ms

---

## Roadmap

- **Performance index**: apply `idx_extended_field_value`
- **Cross-species scoring**: multi-species semantic results are merged on raw
  scores from different models; a calibrated ordering would be better
- **User features**: authentication, bookmarks, and notifications are unbuilt —
  the frontend has placeholder UI for all three
