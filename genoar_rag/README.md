# GENOAR RAG Web UI

[Korean / 한국어](README.ko.md)

A web application for searching, browsing, and inspecting SRA (Sequence Read
Archive) metadata by keyword or natural language, and for retrieving the
pipeline's processed outputs.

---

## Quick Start

### Prerequisites

- **Docker** and **Docker Compose**
- **A metadata database** — a MariaDB server (default) or a SQLite file (`sra_hybrid.db`). See [Prepare the database](#step-2-prepare-the-database)
- *(Optional)* **FAISS indexes** — only if you want semantic search. See [Semantic Search](#semantic-search-species-routing)

### Step 1: Clone

```bash
git clone <repository-url>
cd genoar_rag
```

### Step 2: Prepare the database

The backend supports two database backends, selected with the `DB_BACKEND`
environment variable.

| Backend | Value | Used for |
|---------|-------|----------|
| MySQL/MariaDB | `mysql` (default) | Deployment |
| SQLite | `sqlite` | Development and tests, and as the automatic fallback when MariaDB is unreachable |

With `DB_BACKEND=mysql`, an unreachable server falls back to the SQLite file at
`DB_PATH` (startup fails if that file is missing). `DB_BACKEND=sqlite` has no
fallback.

#### Starting with SQLite (simplest)

The database file is not committed to the repository because of its size, so
supply it yourself.

```bash
mkdir -p data
cp /path/to/your/sra_hybrid.db data/sra_hybrid.db
```

Select the SQLite backend in `.env`:

```bash
cp .env.example .env
```

```env
DB_BACKEND=sqlite
DB_FILE=./data/sra_hybrid.db     # an absolute path also works
```

Resulting layout:
```
genoar_rag/
├── data/
│   └── sra_hybrid.db    ← place it here
├── backend/
├── frontend/
├── docker-compose.yml
└── ...
```

#### Starting with MariaDB (default deployment)

`DB_PASSWORD` and `DB_ROOT_PASSWORD` ship empty in `.env.example`, so choose
them yourself first — the image refuses to initialise on an empty password and
the profile will restart-loop with `Database is uninitialized and password
option is not specified` until both are set:

```env
DB_PASSWORD=<choose one>
DB_ROOT_PASSWORD=<choose another>
```

The MariaDB service sits behind a compose profile, so it has to be enabled
explicitly:

```bash
docker compose --profile mysql up
```

To move existing SQLite data over, use the migration script:

```bash
python backend/scripts/migrate_sqlite_to_mysql.py --drop     # first load and every refresh
python backend/scripts/migrate_sqlite_to_mysql.py --upsert   # non-destructive update
```

Connection settings come from `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`,
and `DB_NAME` in `.env`.

### Step 3: Run

```bash
docker compose up --build                    # SQLite backend
docker compose --profile mysql up --build    # with MariaDB
```

The first build can take several minutes.

### Step 4: Open

| Service | URL | Description |
|---------|-----|-------------|
| **Web UI** | http://localhost:3005 | Main web application |
| **Health Check** | http://localhost:3005/health | Server status + species available for semantic search |

### Step 5: Verify

1. Open http://localhost:3005 → the Dashboard should show stat cards, charts, and a sample list
2. Type `lung cancer` into the search bar → results should appear
3. Click any Run ID → the detail page should load
4. `curl http://localhost:3005/health` → with semantic search configured,
   `{"status":"ok","semantic_search":true,"semantic_species":["human", ...]}`

If something does not work, see [Troubleshooting](#troubleshooting).

---

## Database Schema

The application expects the schema below. SQLite and MariaDB use the same table
structure.

### `sra_core`

The main table. One row per SRA sample (Run).

```sql
CREATE TABLE sra_core (
    Run                    TEXT,   -- SRA Run ID (e.g. SRR22351028) — lookup key
    Series                 TEXT,   -- GEO Series ID (e.g. GSE218390)
    BioSample              TEXT,   -- BioSample ID (e.g. SAMN32123456)
    Sample_Name            TEXT,   -- Sample name
    tissue                 TEXT,   -- Tissue (e.g. Blood, Brain)
    cell_type              TEXT,   -- Cell type (e.g. T cell, Neuron)
    disease_state_modified TEXT,   -- Disease state — served as `disease` by the API
    Platform               TEXT,   -- Sequencing platform (e.g. ILLUMINA)
    Instrument             TEXT,   -- Instrument model (e.g. Illumina HiSeq 2500)
    sex                    TEXT,   -- Sex
    Age                    TEXT,   -- Age
    strain                 TEXT,   -- Strain
    genotype               TEXT,   -- Genotype
    treatment              TEXT,   -- Treatment
    size                   REAL,   -- File size (MB)
    STR_tis                TEXT,   -- Tissue STR (semantic text)
    STR_dis                TEXT,   -- Disease STR
    STR_cell               TEXT,   -- Cell type STR
    CUI_tis                TEXT,   -- Tissue UMLS CUI
    CUI_dis                TEXT,   -- Disease UMLS CUI
    CUI_cell               TEXT,   -- Cell type UMLS CUI
    created_at             TIMESTAMP  -- sorted on by sort_by=release_date
);
```

The column names matter: the API's `disease` reads `disease_state_modified`, and
`sort_by=release_date` sorts on `created_at`. A database built with different
names will load but serve those fields as empty.

### `sra_extended`

Extended metadata in an EAV (Entity-Attribute-Value) layout.

```sql
CREATE TABLE sra_extended (
    Run         TEXT,               -- references sra_core.Run
    field_name  TEXT,               -- attribute name (e.g. Organism, Assay Type)
    field_value TEXT,               -- attribute value
    data_type   TEXT                -- data type
);
```

Notable `field_name` values:
- `Organism` — species (e.g. Homo sapiens, Mus musculus)
- `Assay Type` — experiment type (e.g. RNA-Seq, ChIP-Seq)
- `LibrarySource` — library source (e.g. TRANSCRIPTOMIC, GENOMIC)
- `disease_state`, `disease`, `diagnosis`, … — disease-related fields (9 aggregated)

### Recommended Indexes

For search performance:

```sql
CREATE INDEX idx_extended_run ON sra_extended(Run);
CREATE INDEX idx_extended_field_name ON sra_extended(field_name);
CREATE INDEX idx_extended_field_value ON sra_extended(field_name, field_value);
CREATE INDEX idx_core_series ON sra_core(Series);
CREATE INDEX idx_core_tissue ON sra_core(tissue);
```

---

## Semantic Search (species routing)

Natural-language queries use a different embedding model and FAISS index per
species. This is the design the GENOAR paper settled on; a query is routed by its
`organism` filter.

| Species | Model | Dim | Samples |
|---------|-------|-----|---------|
| human | `cambridgeltl/SapBERT-from-PubMedBERT-fulltext` | 768 | 3,114 |
| mouse | `sentence-transformers/all-MiniLM-L6-v2` | 384 | 3,085 |

The differing dimensions are fine because each species owns a separate index. The
paper writes the mouse model as "MiniLLM"; the model it evaluated is
all-MiniLM-L6-v2, a sentence-embedding model.

### Building the indexes

**The `faiss_index.bin` that shipped with the repository was built with PubMedBERT
and matches neither model above.** The backend refuses to load an index whose
build model differs from the configured one (`REQUIRE_INDEX_MODEL_MATCH=true`), so
both indexes have to be rebuilt.

```bash
# human (SapBERT)
python backend/scripts/build_faiss_index.py --db ./data/sra_hybrid.db \
  --model cambridgeltl/SapBERT-from-PubMedBERT-fulltext \
  --organism "Homo sapiens" \
  --out-index ./data/faiss_human.bin \
  --out-mapping ./data/run_mapping_human.json

# mouse (all-MiniLM-L6-v2)
python backend/scripts/build_faiss_index.py --db ./data/sra_hybrid.db \
  --model sentence-transformers/all-MiniLM-L6-v2 \
  --organism "Mus musculus" \
  --out-index ./data/faiss_mouse.bin \
  --out-mapping ./data/run_mapping_mouse.json
```

Each build writes a `<index>.meta.json` sidecar next to the index, which is what
the backend checks the build model against. `docker-compose.yml` mounts the
sidecars too.

### Turning a species off

A configured species must load, or the server refuses to start — a missing or
broken index is a startup error naming the reason, never a silently absent
feature. To serve without a species, say so: set both of its path variables to
an explicitly empty value (in `.env` for Compose deployments):

```dotenv
MOUSE_FAISS_INDEX_PATH=
MOUSE_RUN_MAPPING_PATH=
```

Emptying one of the pair is refused at startup as a likely typo. With a species
off, its semantic search is disabled while keyword search and the other species
are unaffected; the `semantic_species` field of `/health` reports the species
actually serving, and the frontend's species toggle enables only those.
`SEMANTIC_SEARCH_ENABLED=false` turns all of it off at once.

---

## Multiple datasets

A deployment usually serves one body of data, configures nothing, and no
dataset name ever appears in a URL — everything above describes that case and
it stays exactly as it was. A deployment can also serve several, so that the
dashboard describes one corpus while the search page searches another.

### Naming the datasets

```dotenv
# .env
DATASETS=main,atlas          # first name is the primary
DASHBOARD_DATASET=atlas      # which one the dashboard describes (build arg)
```

The primary (`main` here) reads the ordinary top-level settings — it is the
corpus the deployment always served. Every later name must say where its data
lives; nothing falls back to a top-level path, because a secondary dataset
that inherited one would serve the primary's data under its own name. Names
are limited to `[a-z0-9_]+` (they appear in environment keys and URLs).

### The secondary dataset's settings — `datasets.env`

Per-dataset settings go in `datasets.env` next to `docker-compose.yml` (not
in `.env`: Compose reads `.env` for substitution but does not pass its
entries into containers). The file is optional and gitignored; forwarding it
needs Compose v2.24+.

```dotenv
# datasets.env
DATASET_ATLAS_DB_NAME=genoar_atlas                                # MariaDB schema
DATASET_ATLAS_DB_PATH=/data/genoar/atlas.db                       # SQLite file / fallback
DATASET_ATLAS_HUMAN_FAISS_INDEX_PATH=/data/genoar/atlas_human.bin
DATASET_ATLAS_HUMAN_RUN_MAPPING_PATH=/data/genoar/atlas_human.json
DATASET_ATLAS_LABEL=Atlas
```

Under the default MariaDB backend a secondary dataset needs its own
`DB_NAME`; under SQLite, its own `DB_PATH`. A species has semantic search
only where its index+mapping pair is given — absent means off, half a pair
refuses to start. The label is what pages show when they say which corpus
they are describing.

### Files, schemas, and verification

- **Mounts**: copy `docker-compose.override.datasets.example` to
  `docker-compose.override.yml` and point it at the dataset's DB, indexes,
  mappings, and `.meta.json` sidecars.
- **MariaDB schema**: create it (plus a read-only grant) via
  `mariadb-init/create-second-dataset.sql.example`, then load it with
  `scripts/migrate_sqlite_to_mysql.py --sqlite <file> --db <schema>` as root
  — once per dataset.
- **Startup checks**: every configured artifact must be real. A missing DB
  or serving table, a broken index, or an index whose runs are not all in
  that dataset's database refuses startup naming the reason. Two datasets
  reaching the same database are refused too.
- **Warm-up**: embedding models load on the first semantic query, so after a
  deploy run one semantic query per dataset and species before opening
  traffic.

### How requests choose a dataset

Every API endpoint takes `?dataset=<name>`; omitting it means the first
(primary) dataset, which is why existing URLs keep meaning what they meant.
An unknown name is 404, never a fallback. `GET /api/v1/datasets` lists the
configured datasets with their labels, and the pages pass the dataset through
search, detail, series, similar, export, and file downloads — a request that
starts in one dataset stays in it.

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_BACKEND` | `mysql` | `mysql` or `sqlite` |
| `DB_FILE` | `./data/sra_hybrid.db` | Host path to the SQLite database file |
| `DB_HOST` / `DB_PORT` | `mariadb` / `3306` | MariaDB endpoint |
| `DB_USER` / `DB_PASSWORD` / `DB_NAME` | `genoar` / — / `genoar` | MariaDB credentials |
| `BACKEND_PORT` | `8001` | Backend API port on the host's loopback address |
| `FRONTEND_PORT` | `3005` | External frontend port |
| `SEMANTIC_SEARCH_ENABLED` | `true` | Master switch for semantic search |
| `HUMAN_FAISS_INDEX` / `HUMAN_RUN_MAPPING` | `./data/faiss_human.bin` / `...json` | Human index paths |
| `MOUSE_FAISS_INDEX` / `MOUSE_RUN_MAPPING` | (empty) | Where the mouse index files live on the host (for the mounts) |
| `MOUSE_FAISS_INDEX_PATH` / `MOUSE_RUN_MAPPING_PATH` | container defaults | Set both explicitly empty to disable mouse only ([Turning a species off](#turning-a-species-off)) |
| `REQUIRE_INDEX_MODEL_MATCH` | `true` | Refuse to load an index built with a different model |
| `PROCESSED_RESULTS` | `../results/success` | Stage 3 output (HDF5) directory. Empty disables processed-data download |
| `PROCESSED_H5_URL_TEMPLATE` | (empty) | Only for deployments serving the files from another host |

See [.env.example](.env.example) for the full list with comments.

### Changing ports

If the defaults are taken, change them in `.env`:

```env
BACKEND_PORT=9001
FRONTEND_PORT=9005
```

---

## Project Structure

```
genoar_rag/
├── docker-compose.yml              # Runs the stack (backend + frontend + MariaDB profile)
├── .env.example                    # Environment variable template
├── data/                           # DB file + FAISS indexes (not in Git)
│   ├── sra_hybrid.db
│   ├── faiss_human.bin(+.meta.json)
│   └── run_mapping_human.json
│
├── backend/                        # FastAPI backend
│   ├── app/
│   │   ├── main.py                 # App factory + lifespan (DB pool, vector router)
│   │   ├── config.py               # Settings (DB backend, per-species models/indexes, downloads)
│   │   ├── models/                 # Pydantic schemas
│   │   ├── routers/                # Endpoints (samples, search, filters, stats, export)
│   │   ├── services/               # Business logic (search strategies, vector_router, download, export)
│   │   ├── repositories/           # SQL query layer
│   │   └── db/                     # SQLite / MySQL connection pools
│   ├── scripts/
│   │   ├── build_faiss_index.py    # Builds a per-species FAISS index
│   │   └── migrate_sqlite_to_mysql.py
│   ├── tests/                      # unit / api / integration
│   ├── Dockerfile
│   └── pyproject.toml
│
├── frontend/                       # Next.js frontend
│   ├── src/
│   │   ├── app/                    # Pages (Dashboard, Search, Sample Detail)
│   │   ├── components/             # Shared components
│   │   ├── hooks/                  # Custom hooks (API access)
│   │   ├── lib/                    # API client
│   │   ├── types/                  # TypeScript types
│   │   └── __tests__/              # Jest tests
│   ├── scripts/verify-browser-history.mjs   # Real-browser check
│   ├── Dockerfile
│   └── package.json
│
└── README.ko.md                    # Korean documentation
```

## Pages

| Route | Page | Description |
|-------|------|-------------|
| `/` | Dashboard | Data Overview (stats and distributions) + Metadata Explorer (sidebar filters, By Tissue/Assay/Series tabs, grid/list, pagination) |
| `/search` | Search | Keyword (SQL) / Semantic modes, species toggle, example queries, 6 filter dropdowns, CSV export |
| `/sample/{accession}` | Sample Detail | Metadata, series table, extended fields, Download Data, Similar Samples |
| `/browse` | (redirect) | Merged into the Dashboard (`/`); redirects while preserving the query string |

`{accession}` accepts both an SRA Run ID (`SRR...`) and a GEO Sample ID (`GSM...`).

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16, React 19, TypeScript 5, Tailwind CSS 4 |
| Backend | FastAPI, Python 3.10+, Pydantic, FAISS, sentence-transformers |
| Database | MySQL/MariaDB (default) or SQLite |
| Deployment | Docker, Docker Compose |

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Server status + species available for semantic search (`semantic_species`) |
| GET | `/api/v1/datasets` | The datasets this deployment serves, with labels and the default |
| GET | `/api/v1/stats` | Dashboard statistics (sample and series counts, distributions) |
| GET | `/api/v1/filters` | Filter options (7 categories) |
| GET | `/api/v1/samples` | Sample list (paginated) |
| GET | `/api/v1/samples/{accession}` | Sample detail + extended metadata (SRR or GSM) |
| GET | `/api/v1/samples/{accession}/series` | Samples in the same series |
| GET | `/api/v1/samples/{accession}/similar` | Semantically similar samples (per-species vector index) |
| GET | `/api/v1/samples/{accession}/download` | Where the data lives (public SRA URL + processed HDF5). No API key |
| GET | `/api/v1/samples/{accession}/files/{filename}` | Streams a Cell Ranger output from the mounted results dir |
| GET | `/api/v1/search` | Filters + keyword/semantic/hybrid search (`search_mode`) |
| GET | `/api/v1/export` | Core metadata export as CSV/JSON (GEO submission fields excluded) |

`search_mode` is one of `sql` (default), `semantic`, or `hybrid`. The latter two
return 503 when no vector backend is loaded.

Every `/api/v1` endpoint also takes `?dataset=<name>` on deployments serving
[multiple datasets](#multiple-datasets); omitted, it means the primary, and an
unknown name is 404.

The service publishes no Swagger UI, ReDoc or OpenAPI schema: the routes are
documented here and on the site's own `/api-docs` page. The backend port is
bound to the host's loopback address, so `curl http://127.0.0.1:8001/...` works
from the host and nowhere else; the browser reaches the API through the
frontend's `/api` rewrite.

---

## Troubleshooting

### The Dashboard shows no data

**Cause**: the database could not be reached.

```bash
# If using SQLite, check the file
ls -la data/sra_hybrid.db

# Check the logs — startup records "DB backend: ..."
docker compose logs backend
```

With `DB_BACKEND=mysql` and no MariaDB running, the backend tries the SQLite
fallback and fails to start if that file is missing too. Either bring the DB up
with `docker compose --profile mysql up` or switch to `DB_BACKEND=sqlite`.

### Semantic search does nothing / the species toggle is disabled

```bash
curl -s http://localhost:3005/health
# semantic_search: false or semantic_species: [] means no index was loaded
docker compose logs backend | grep -i semantic
```

Common causes:
- The indexes have not been built → see [Building the indexes](#building-the-indexes)
- The index was built with a different model → rebuild it, or set
  `REQUIRE_INDEX_MODEL_MATCH=false` only if you know why
- `MOUSE_FAISS_INDEX_PATH`/`MOUSE_RUN_MAPPING_PATH` are explicitly empty →
  mouse alone is disabled, which is expected ([Turning a species off](#turning-a-species-off))
- A configured index is missing or broken → the server refuses to start and
  says why in the logs (it does not silently drop the species)

### Port conflict

```
Error: Bind for 127.0.0.1:8001 failed: port is already allocated
```

Change the ports in `.env`:
```env
BACKEND_PORT=9001
FRONTEND_PORT=9005
```

### The frontend reports that it cannot reach the server

**Cause**: the backend has not started, or it exited.

```bash
docker compose ps
docker compose logs backend
docker compose restart backend
```

### Search is slow

The database may be missing indexes. Add them with the SQLite CLI:

```bash
sqlite3 data/sra_hybrid.db <<EOF
CREATE INDEX IF NOT EXISTS idx_extended_run ON sra_extended(Run);
CREATE INDEX IF NOT EXISTS idx_extended_field_name ON sra_extended(field_name);
CREATE INDEX IF NOT EXISTS idx_extended_field_value ON sra_extended(field_name, field_value);
CREATE INDEX IF NOT EXISTS idx_core_series ON sra_core(Series);
EOF
```

### No processed (HDF5) download is offered

Either the Stage 3 output is not mounted, or that sample has not been processed.
Check that `PROCESSED_RESULTS` points at the pipeline's `results/success`
directory. Whether the files exist under `<run_id>/cellranger_output/outs/` is what
"processed" means; when they do not, only the raw SRA link is shown.

### Full reset

If problems persist, rebuild the images from scratch:

```bash
docker compose down
docker compose up --build --force-recreate
```

---

## Development

### Running locally (without Docker)

#### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"

# Point at a database (SQLite for local work)
export DB_BACKEND=sqlite
export DB_PATH=/path/to/sra_hybrid.db

# Dev server
uvicorn app.main:app --reload --port 8000
```

#### Frontend

```bash
cd frontend
npm install
npm run dev
# → http://localhost:3000
```

Locally the frontend proxies `/api/*` to `http://localhost:8000` through the
rewrite in `next.config.ts`.

### Tests

```bash
# Backend (Docker)
cd backend
docker build -t genoar-backend .
docker run --rm genoar-backend python -m pytest tests/ -v

# Backend (local venv)
python -m pytest tests/ -q        # last run: 399 passed, 3 skipped

# Frontend unit tests
cd frontend
npm test                          # Jest: 8 suites, 91 tests

# Real-browser check (history Back, series highlight)
# Requires the stack to be up and Chrome running on the CDP port; see the script header.
npm run verify:browser
```

---

## Roadmap

| Phase | Item | Status |
|-------|------|--------|
| Phase 1 | Structured SQL search API | Done |
| Phase 2 | Per-species FAISS semantic / hybrid search, similar samples | Implemented (indexes must be built per deployment) |
| Phase 2+ | Download API (SRA URL + processed HDF5), CSV/JSON export | Done |
| — | Authentication, bookmarks, notifications, By Date tab | Not started |

The tables above are the feature status: the endpoint list under **API Endpoints**
says what is served, **Semantic Search** says what each species index covers, and
the rows here say what is not built yet.
