# GENOAR RAG Web UI

[English](README.md)

SRA(Sequence Read Archive) 메타데이터를 키워드·자연어로 검색, 탐색, 조회하고
처리 결과를 내려받을 수 있는 웹 애플리케이션입니다.

---

## 빠른 시작 가이드

### 사전 요구사항

- **Docker** 및 **Docker Compose** 설치
- **메타데이터 DB** — MariaDB 서버(기본) 또는 SQLite 파일(`sra_hybrid.db`). 아래 [데이터베이스 준비](#2단계-데이터베이스-준비) 참조
- *(선택)* **FAISS 인덱스** — 시맨틱 검색을 쓰려는 경우에만. 아래 [시맨틱 검색](#시맨틱-검색-종별-라우팅) 참조

### 1단계: 프로젝트 클론

```bash
git clone <repository-url>
cd genoar_rag
```

### 2단계: 데이터베이스 준비

백엔드는 두 가지 DB 백엔드를 지원하며, `DB_BACKEND` 환경 변수로 고릅니다.

| 백엔드 | 값 | 용도 |
|--------|-----|------|
| MySQL/MariaDB | `mysql` (기본값) | 배포 |
| SQLite | `sqlite` | 개발·테스트, 그리고 MariaDB 접속 실패 시 자동 폴백 |

`DB_BACKEND=mysql`인데 서버에 접속할 수 없으면 `DB_PATH`의 SQLite 파일로 자동
폴백합니다(파일이 없으면 기동 실패). `DB_BACKEND=sqlite`는 폴백이 없습니다.

#### SQLite로 시작하기 (가장 간단)

DB 파일은 용량 문제로 Git 저장소에 포함되어 있지 않으므로 별도로 준비해야 합니다.

```bash
mkdir -p data
cp /path/to/your/sra_hybrid.db data/sra_hybrid.db
```

`.env`에서 백엔드를 SQLite로 지정합니다:

```bash
cp .env.example .env
```

```env
DB_BACKEND=sqlite
DB_FILE=./data/sra_hybrid.db     # 절대 경로도 가능
```

최종 구조:
```
genoar_rag/
├── data/
│   └── sra_hybrid.db    ← 여기에 배치
├── backend/
├── frontend/
├── docker-compose.yml
└── ...
```

#### MariaDB로 시작하기 (기본 배포 구성)

`.env.example`의 `DB_PASSWORD`와 `DB_ROOT_PASSWORD`는 값이 비어 있으니 먼저 직접
정해야 합니다. 이미지는 빈 비밀번호로 초기화되지 않으며, 둘 다 채우기 전까지는
`Database is uninitialized and password option is not specified`를 남기고 재시작을
반복합니다:

```env
DB_PASSWORD=<직접 정한 값>
DB_ROOT_PASSWORD=<직접 정한 다른 값>
```

MariaDB 서비스는 compose profile로 분리돼 있어 명시적으로 켜야 합니다:

```bash
docker compose --profile mysql up
```

기존 SQLite 데이터를 옮기려면 이전 스크립트를 씁니다:

```bash
python backend/scripts/migrate_sqlite_to_mysql.py --drop     # 최초 적재와 갱신 모두
python backend/scripts/migrate_sqlite_to_mysql.py --upsert   # 원본이 대상의 상위집합일 때만
```

접속 정보는 `.env`의 `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`으로
지정합니다.

### 3단계: 실행

```bash
docker compose up --build                    # SQLite 백엔드
docker compose --profile mysql up --build    # MariaDB 포함
```

첫 실행 시 Docker 이미지 빌드에 수 분이 소요될 수 있습니다.

### 4단계: 접속

빌드가 완료되면 브라우저에서 접속합니다:

| 서비스 | URL | 설명 |
|--------|-----|------|
| **웹 UI** | http://localhost:3005 | 메인 웹 애플리케이션 |
| **Health Check** | http://localhost:3005/health | 서버 상태 + 시맨틱 검색 가용 종 |

### 5단계: 정상 작동 확인

1. http://localhost:3005 접속 → Dashboard에 통계 카드와 차트, 샘플 목록이 표시되는지 확인
2. 검색바에 `lung cancer` 입력 → 검색 결과가 표시되는지 확인
3. 아무 샘플의 Run ID 클릭 → 상세 페이지가 표시되는지 확인
4. `curl http://localhost:3005/health` → 시맨틱 검색을 설정했다면
   `{"status":"ok","semantic_search":true,"semantic_species":["human", ...]}`

정상 작동하지 않는 경우 [문제 해결](#문제-해결)을 참조하세요.

---

## 데이터베이스 스키마

애플리케이션이 정상 작동하려면 아래 스키마를 가진 DB가 필요합니다. SQLite와
MariaDB 모두 같은 테이블 구조를 씁니다.

### `sra_core` 테이블

메인 테이블. 각 행이 하나의 SRA 샘플(Run)을 나타냅니다.

```sql
CREATE TABLE sra_core (
    Run                    TEXT,   -- SRA Run ID (예: SRR22351028) — 조회 키
    Series                 TEXT,   -- GEO Series ID 하나 (예: GSE218390) — 아래 `sra_series` 참조
    BioSample              TEXT,   -- BioSample ID (예: SAMN32123456)
    Sample_Name            TEXT,   -- 샘플 이름
    tissue                 TEXT,   -- 조직 (예: Blood, Brain)
    cell_type              TEXT,   -- 세포 유형 (예: T cell, Neuron)
    disease_state_modified TEXT,   -- 질병 상태 — API에서는 `disease`로 노출
    Platform               TEXT,   -- 시퀀싱 플랫폼 (예: ILLUMINA)
    Instrument             TEXT,   -- 장비 모델 (예: Illumina HiSeq 2500)
    sex                    TEXT,   -- 성별
    Age                    TEXT,   -- 나이
    strain                 TEXT,   -- 균주
    genotype               TEXT,   -- 유전형
    treatment              TEXT,   -- 처리 조건
    size                   REAL,   -- 파일 크기 (MB)
    STR_tis                TEXT,   -- Tissue STR (시맨틱 텍스트)
    STR_dis                TEXT,   -- Disease STR
    STR_cell               TEXT,   -- Cell Type STR
    CUI_tis                TEXT,   -- Tissue UMLS CUI
    CUI_dis                TEXT,   -- Disease UMLS CUI
    CUI_cell               TEXT,   -- Cell Type UMLS CUI
    created_at             TIMESTAMP  -- sort_by=release_date의 정렬 기준
);
```

컬럼명이 그대로여야 합니다. API의 `disease`는 `disease_state_modified`를 읽고,
`sort_by=release_date`는 `created_at`으로 정렬합니다. 다른 이름으로 만든 DB는
로드는 되지만 해당 필드가 빈 값으로 나갑니다.

### `sra_series` 테이블

Run과 GEO Series의 관계. **필수입니다** — 이 테이블이 없으면 서버는 연결에는
성공한 뒤 통계·Series 필터·표본 상세의 `all_series`·같은 Series 표본·export의
Series 열에서 table-not-found로 실패합니다.

```sql
CREATE TABLE sra_series (
    Run    TEXT,   -- sra_core.Run 참조
    Series TEXT,   -- GEO Series ID
    UNIQUE(Run, Series)
);
```

`sra_core.Series`가 아니라 여기가 정본입니다. 한 Run이 둘 이상의 GSE에 속할 수
있는데(현재 큐레이션 6,199건 중 870건) `sra_core.Run`은 UNIQUE이고 `Series`는
컬럼 하나라 그중 하나만 담기기 때문입니다. 그래서 `sra_core.Series`만 세면 888개
스터디 중 760개만 보이고, 나머지 128개는 항상 어떤 Run의 두 번째 소속이라 통째로
사라집니다. `sra_core.Series`는 아직 이 컬럼을 읽는 쿼리를 위해 대표값 하나를
남겨둔 것입니다.

### `sra_extended` 테이블

EAV(Entity-Attribute-Value) 구조의 확장 메타데이터 테이블.

```sql
CREATE TABLE sra_extended (
    Run         TEXT,               -- sra_core.Run 참조
    field_name  TEXT,               -- 속성 이름 (예: Organism, Assay Type)
    field_value TEXT,               -- 속성 값
    data_type   TEXT                -- 데이터 타입
);
```

주요 `field_name` 값:
- `Organism` — 생물종 (예: Homo sapiens, Mus musculus)
- `Assay Type` — 실험 유형 (예: RNA-Seq, ChIP-Seq)
- `LibrarySource` — 라이브러리 소스 (예: TRANSCRIPTOMIC, GENOMIC)
- `disease_resolved` — 질병 필터가 읽는 단일 값. 적재 시 nomenclaturist의 UMLS
  개념을 우선하고 없으면 제출자 원문을 쓴다. 원문 필드(`disease_state_modified` 등)도
  함께 보존되며 화면은 둘 다 보여준다

### 권장 인덱스

검색 성능 향상을 위해 아래 인덱스를 추가하는 것을 권장합니다:

```sql
CREATE INDEX idx_extended_run ON sra_extended(Run);
CREATE INDEX idx_extended_field_name ON sra_extended(field_name);
CREATE INDEX idx_extended_field_value ON sra_extended(field_name, field_value);
CREATE INDEX idx_core_series ON sra_core(Series);
CREATE INDEX idx_core_tissue ON sra_core(tissue);
```

---

## 시맨틱 검색 (종별 라우팅)

자연어 질의는 종(species)별로 다른 임베딩 모델과 FAISS 인덱스를 씁니다. 이는
논문(GENOAR)이 확정한 설계이며, 질의는 `organism` 필터로 라우팅됩니다.

| 종 | 모델 | 차원 | 샘플 수 |
|----|------|------|---------|
| human | `cambridgeltl/SapBERT-from-PubMedBERT-fulltext` | 768 | 3,114 |
| mouse | `sentence-transformers/all-MiniLM-L6-v2` | 384 | 3,085 |

차원이 달라도 인덱스가 종별로 분리돼 있어 문제되지 않습니다. 논문은 mouse 모델을
"MiniLLM"으로 표기하지만, 실제 평가한 모델은 문장 임베딩 모델인
all-MiniLM-L6-v2입니다.

### 인덱스 빌드

**저장소에 있던 `faiss_index.bin`은 PubMedBERT로 빌드된 것이라 위 두 모델과 맞지
않습니다.** 백엔드는 인덱스의 빌드 모델이 설정된 모델과 다르면 로드를 거부하므로
(`REQUIRE_INDEX_MODEL_MATCH=true`), 두 인덱스를 각각 새로 빌드해야 합니다.

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

빌드 시 인덱스 옆에 `<index>.meta.json` sidecar가 함께 생성되며, 백엔드는 이 파일로
빌드 모델을 검증합니다. `docker-compose.yml`이 sidecar까지 마운트합니다.

### 한 종만 끄기

설정된 종은 반드시 로드에 성공해야 하며, 실패하면 서버가 사유를 남기고 기동을
거부합니다 — 인덱스가 없거나 깨졌는데 조용히 그 종만 사라지는 일은 없습니다.
한 종 없이 서빙하려면 명시적으로 꺼야 합니다: Compose 배포는 `.env`에서 그 종의
컨테이너 경로 두 개를 빈 값으로 지정합니다.

```dotenv
MOUSE_FAISS_INDEX_PATH=
MOUSE_RUN_MAPPING_PATH=
```

둘 중 하나만 비우면 설정 실수로 보고 기동을 거부합니다. 종을 끄면 그 종의
시맨틱 검색만 꺼지고 키워드 검색과 다른 종은 영향받지 않습니다. `/health`의
`semantic_species`가 실제로 서빙 중인 종 목록이며, 프론트엔드의 종 토글은 이
값을 보고 사용 가능한 종만 활성화합니다. 시맨틱 검색 전체를 끄려면
`SEMANTIC_SEARCH_ENABLED=false`.

---

## 다중 데이터셋

배포는 보통 데이터 본체 하나를 서빙하며, 아무것도 설정하지 않고 URL에
dataset 이름이 나타나지 않습니다 — 위의 모든 설명이 그 경우이고 그대로
유지됩니다. 여러 개를 서빙할 수도 있습니다: 대시보드는 한 corpus를 설명하고
검색 페이지는 다른 corpus를 검색하는 구성입니다.

### 데이터셋 이름 붙이기

```dotenv
# .env
DATASETS=main,atlas          # 첫 이름이 primary
DASHBOARD_DATASET=atlas      # 대시보드가 설명할 데이터셋 (빌드 인자)
```

primary(`main`)는 평소의 top-level 설정을 읽습니다 — 배포가 늘 서빙하던
corpus이기 때문입니다. 두 번째부터는 데이터가 어디 있는지 반드시 명시해야
합니다. top-level 경로로 폴백하지 않습니다 — 상속했다면 primary의 데이터를
자기 이름으로 서빙하게 되기 때문입니다. 이름은 환경 변수 키와 URL에 쓰이므로
`[a-z0-9_]+`로 제한됩니다.

### 보조 데이터셋의 설정 — `datasets.env`

데이터셋별 설정은 `docker-compose.yml` 옆의 `datasets.env`에 씁니다 (`.env`가
아닙니다: Compose는 `.env`를 치환용으로만 읽고 컨테이너로 전달하지 않습니다).
이 파일은 선택 사항이고 gitignore되며, 전달에는 Compose v2.24+가 필요합니다.

```dotenv
# datasets.env
DATASET_ATLAS_DB_NAME=genoar_atlas                                # MariaDB 스키마
DATASET_ATLAS_DB_PATH=/data/genoar/atlas.db                       # SQLite 파일 / 폴백
DATASET_ATLAS_HUMAN_FAISS_INDEX_PATH=/data/genoar/atlas_human.bin
DATASET_ATLAS_HUMAN_RUN_MAPPING_PATH=/data/genoar/atlas_human.json
DATASET_ATLAS_LABEL=Atlas
```

기본 MariaDB 백엔드에서는 보조 데이터셋마다 자기 `DB_NAME`이, SQLite에서는
자기 `DB_PATH`가 필수입니다. 종별 시맨틱 검색은 index+mapping 쌍을 준 종에만
있습니다 — 안 주면 그 종은 꺼짐이고, 반쪽만 주면 기동이 거부됩니다. label은
페이지가 어떤 corpus를 보여주는지 말할 때 쓰는 이름입니다.

### 파일·스키마·검증

- **마운트**: `docker-compose.override.datasets.example`을
  `docker-compose.override.yml`로 복사해 그 데이터셋의 DB·인덱스·매핑·
  `.meta.json` sidecar를 가리키게 합니다.
- **MariaDB 스키마**: `mariadb-init/create-second-dataset.sql.example`로
  스키마와 읽기 전용 grant를 만들고, root로
  `scripts/migrate_sqlite_to_mysql.py --sqlite <파일> --db <스키마>`를
  데이터셋마다 한 번씩 실행해 적재합니다.
- **기동 검사**: 설정한 산출물은 전부 실재해야 합니다. DB나 serving 테이블이
  없거나, 인덱스가 깨졌거나, 인덱스의 run이 그 데이터셋 DB에 전부 있지
  않으면 사유를 남기고 기동이 거부됩니다. 두 데이터셋이 같은 DB를 가리키는
  것도 거부됩니다.
- **워밍업**: 임베딩 모델은 첫 시맨틱 쿼리에서 로드되므로, 배포 후 트래픽을
  열기 전에 데이터셋·종마다 시맨틱 쿼리를 한 번씩 실행하세요.

### 요청이 데이터셋을 고르는 방법

모든 API 엔드포인트가 `?dataset=<이름>`을 받습니다. 생략하면 첫 번째
(primary)를 뜻하므로 기존 URL의 의미가 그대로 유지됩니다. 없는 이름은 폴백
없이 404입니다. `GET /api/v1/datasets`가 설정된 데이터셋 목록과 label을
돌려주고, 페이지는 검색·상세·시리즈·유사·내보내기·파일 다운로드까지
dataset을 그대로 전달합니다 — 한 데이터셋에서 시작한 요청은 끝까지 그
데이터셋에 머뭅니다.

---

## 환경 설정

### 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DB_BACKEND` | `mysql` | `mysql` 또는 `sqlite` |
| `DB_FILE` | `./data/sra_hybrid.db` | 호스트의 SQLite DB 파일 경로 |
| `DB_HOST` / `DB_PORT` | `mariadb` / `3306` | MariaDB 접속 정보 |
| `DB_USER` / `DB_PASSWORD` / `DB_NAME` | `genoar` / — / `genoar` | MariaDB 계정 |
| `BACKEND_PORT` | `8001` | 호스트 loopback에 바인딩되는 백엔드 API 포트 |
| `FRONTEND_PORT` | `3005` | 프론트엔드 웹 외부 포트 |
| `SEMANTIC_SEARCH_ENABLED` | `true` | 시맨틱 검색 전체 on/off |
| `HUMAN_FAISS_INDEX` / `HUMAN_RUN_MAPPING` | `./data/faiss_human.bin` / `...json` | human 인덱스 경로 |
| `MOUSE_FAISS_INDEX` / `MOUSE_RUN_MAPPING` | (비어 있음) | mouse 인덱스의 호스트 파일 위치(mount용) |
| `MOUSE_FAISS_INDEX_PATH` / `MOUSE_RUN_MAPPING_PATH` | 컨테이너 기본 경로 | 두 개를 빈 값으로 명시하면 mouse만 비활성화 ([한 종만 끄기](#한-종만-끄기)) |
| `REQUIRE_INDEX_MODEL_MATCH` | `true` | 인덱스 빌드 모델 불일치 시 로드 거부 |
| `PROCESSED_RESULTS` | `../results/success` | Stage 3 산출물(HDF5) 디렉터리. 비우면 처리 결과 다운로드 비활성화 |
| `PROCESSED_H5_URL_TEMPLATE` | (비어 있음) | 파일을 다른 호스트에서 서빙할 때만 사용 |

전체 목록과 주석은 [.env.example](.env.example)을 참조하세요.

### 포트 변경

기본 포트가 이미 사용 중인 경우 `.env` 파일에서 변경할 수 있습니다:

```env
BACKEND_PORT=9001
FRONTEND_PORT=9005
```

---

## 프로젝트 구조

```
genoar_rag/
├── docker-compose.yml              # 전체 서비스 실행 (백엔드 + 프론트엔드 + MariaDB profile)
├── .env.example                    # 환경 변수 템플릿
├── data/                           # DB 파일 + FAISS 인덱스 (Git 미포함)
│   ├── sra_hybrid.db
│   ├── faiss_human.bin(+.meta.json)
│   └── run_mapping_human.json
│
├── backend/                        # FastAPI 백엔드 API
│   ├── app/
│   │   ├── main.py                 # FastAPI 앱 팩토리 + lifespan (DB 풀, 벡터 라우터)
│   │   ├── config.py               # 환경 설정 (DB 백엔드, 종별 모델/인덱스, 다운로드)
│   │   ├── models/                 # Pydantic 스키마
│   │   ├── routers/                # API 엔드포인트 (samples, search, filters, stats, export)
│   │   ├── services/               # 비즈니스 로직 (검색 Strategy, vector_router, download, export)
│   │   ├── repositories/           # SQL 쿼리 레이어
│   │   └── db/                     # SQLite / MySQL 커넥션 풀
│   ├── scripts/
│   │   ├── build_faiss_index.py    # 종별 FAISS 인덱스 빌드
│   │   └── migrate_sqlite_to_mysql.py
│   ├── tests/                      # unit / api / integration
│   ├── Dockerfile
│   └── pyproject.toml
│
├── frontend/                       # Next.js 프론트엔드
│   ├── src/
│   │   ├── app/                    # 페이지 (Dashboard, Search, Sample Detail)
│   │   ├── components/             # 공유 컴포넌트
│   │   ├── hooks/                  # 커스텀 훅 (API 연동)
│   │   ├── lib/                    # API 클라이언트
│   │   ├── types/                  # TypeScript 타입
│   │   └── __tests__/              # Jest 테스트
│   ├── scripts/verify-browser-history.mjs   # 실제 브라우저 확인 스크립트
│   ├── Dockerfile
│   └── package.json
│
└── README.md                       # 영문 문서
```

## 페이지 구성

| 경로 | 페이지 | 설명 |
|------|--------|------|
| `/` | Dashboard | 통계·분포 요약(Data Overview) + Metadata Explorer(사이드바 필터, By Tissue/Assay/Series 탭, Grid/List, 페이지네이션) |
| `/search` | Search | Keyword(SQL) / Semantic 모드, 종 토글, 예시 질의, 6개 필터 드롭다운, CSV 내보내기 |
| `/sample/{accession}` | Sample Detail | 메타데이터, 동일 시리즈 표, 확장 필드, Download Data, Similar Samples |
| `/browse` | (리다이렉트) | Dashboard(`/`)로 병합됨. 쿼리 문자열을 유지한 채 리다이렉트 |

`{accession}`에는 SRA Run ID(`SRR...`)와 GEO Sample ID(`GSM...`)를 모두 넣을 수 있습니다.

## 기술 스택

| 구성 | 기술 |
|------|------|
| Frontend | Next.js 16, React 19, TypeScript 5, Tailwind CSS 4 |
| Backend | FastAPI, Python 3.10+, Pydantic, FAISS, sentence-transformers |
| Database | MySQL/MariaDB (기본) 또는 SQLite |
| 배포 | Docker, Docker Compose |

---

## API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET | `/health` | 서버 상태 + 시맨틱 검색 가용 종(`semantic_species`) |
| GET | `/api/v1/datasets` | 이 배포가 서빙하는 데이터셋 목록·label·기본값 |
| GET | `/api/v1/stats` | 대시보드 통계 (샘플수, 시리즈수, 분포) |
| GET | `/api/v1/filters` | 필터 옵션 목록 (7개 카테고리) |
| GET | `/api/v1/samples` | 전체 샘플 목록 (페이지네이션) |
| GET | `/api/v1/samples/{accession}` | 샘플 상세 + 확장 메타데이터 (SRR/GSM 모두 허용) |
| GET | `/api/v1/samples/{accession}/series` | 동일 시리즈 샘플 목록 |
| GET | `/api/v1/samples/{accession}/similar` | 시맨틱 유사 샘플 (종별 벡터 인덱스) |
| GET | `/api/v1/samples/{accession}/download` | 데이터 위치 (SRA 공개 URL + 처리 결과 HDF5). API key 불필요 |
| GET | `/api/v1/samples/{accession}/files/{filename}` | 마운트된 Cell Ranger 산출물 스트리밍 |
| GET | `/api/v1/search` | 다중 필터 + 키워드/시맨틱/하이브리드 검색 (`search_mode`) |
| GET | `/api/v1/export` | core 메타데이터 CSV/JSON 내보내기 (GEO 제출 필드 제외) |

`search_mode`는 `sql`(기본), `semantic`, `hybrid` 중 하나입니다. `semantic`/`hybrid`는
벡터 백엔드가 로드되지 않았으면 503을 반환합니다.

[다중 데이터셋](#다중-데이터셋)을 서빙하는 배포에서는 모든 `/api/v1`
엔드포인트가 `?dataset=<이름>`도 받습니다. 생략하면 primary이고, 없는 이름은
404입니다.

Swagger UI·ReDoc·OpenAPI 스키마는 제공하지 않습니다. 라우트는 이 문서와 사이트의
`/api-docs` 페이지에 정리돼 있습니다. 백엔드 포트는 호스트의 loopback에만
바인딩되므로 `curl http://127.0.0.1:8001/...`은 호스트에서만 되고, 브라우저는
프론트엔드의 `/api` rewrite를 통해 API에 닿습니다.

---

## 문제 해결

### Dashboard에 데이터가 표시되지 않음

**원인**: DB에 연결하지 못했습니다.

```bash
# SQLite를 쓰는 경우 파일 존재 확인
ls -la data/sra_hybrid.db

# Docker 로그 확인 (기동 시 "DB backend: ..." 로그가 남습니다)
docker compose logs backend
```

`DB_BACKEND=mysql`인데 MariaDB가 안 떠 있으면 SQLite 폴백을 시도하고, 그 파일도
없으면 기동에 실패합니다. `docker compose --profile mysql up`으로 DB 서비스를 함께
띄우거나 `DB_BACKEND=sqlite`로 전환하세요.

### 시맨틱 검색이 동작하지 않음 / 종 토글이 비활성화됨

```bash
curl -s http://localhost:3005/health
# semantic_search: false 또는 semantic_species: [] 이면 인덱스가 로드되지 않은 것
docker compose logs backend | grep -i semantic
```

흔한 원인:
- 인덱스를 아직 빌드하지 않음 → [인덱스 빌드](#인덱스-빌드) 참조
- 인덱스 빌드 모델이 설정된 모델과 다름 → 재빌드하거나, 사정을 알고 쓰는 경우에만
  `REQUIRE_INDEX_MODEL_MATCH=false`
- `MOUSE_FAISS_INDEX_PATH`/`MOUSE_RUN_MAPPING_PATH`를 빈 값으로 명시함 →
  mouse만 비활성화되는 정상 동작 ([한 종만 끄기](#한-종만-끄기))
- 인덱스가 없거나 깨졌는데 경로가 설정돼 있음 → 서버가 기동을 거부하며 로그에
  사유를 남김 (조용히 꺼지지 않음)

### 포트 충돌 에러

```
Error: Bind for 127.0.0.1:8001 failed: port is already allocated
```

`.env` 파일에서 포트를 변경하세요:
```env
BACKEND_PORT=9001
FRONTEND_PORT=9005
```

### 프론트엔드에서 "서버에 연결할 수 없습니다" 표시

**원인**: 백엔드가 아직 시작되지 않았거나 비정상 종료됨.

```bash
docker compose ps
docker compose logs backend
docker compose restart backend
```

### 검색 속도가 느린 경우

데이터베이스에 인덱스가 없을 수 있습니다. SQLite CLI로 인덱스를 추가하세요:

```bash
sqlite3 data/sra_hybrid.db <<EOF
CREATE INDEX IF NOT EXISTS idx_extended_run ON sra_extended(Run);
CREATE INDEX IF NOT EXISTS idx_extended_field_name ON sra_extended(field_name);
CREATE INDEX IF NOT EXISTS idx_extended_field_value ON sra_extended(field_name, field_value);
CREATE INDEX IF NOT EXISTS idx_core_series ON sra_core(Series);
EOF
```

### 처리 결과(HDF5) 다운로드가 보이지 않음

Stage 3 산출물이 마운트되지 않았거나 해당 샘플이 아직 처리되지 않은 경우입니다.
`PROCESSED_RESULTS`가 파이프라인의 `results/success` 디렉터리를 가리키는지 확인하세요.
해당 Run의 `<run_id>/cellranger_output/outs/` 아래 파일이 실제로 있는지가 "처리됨"의
기준이며, 없으면 원본 SRA 링크만 표시됩니다.

### 전체 초기화

문제가 지속되면 Docker 이미지를 완전히 재빌드합니다:

```bash
docker compose down
docker compose up --build --force-recreate
```

---

## 개발 가이드

### 로컬 개발 (Docker 없이)

#### 백엔드

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"

# DB 지정 (SQLite로 개발하는 경우)
export DB_BACKEND=sqlite
export DB_PATH=/path/to/sra_hybrid.db

# 개발 서버 실행
uvicorn app.main:app --reload --port 8000
```

#### 프론트엔드

```bash
cd frontend
npm install
npm run dev
# → http://localhost:3000
```

로컬 개발 시 프론트엔드는 `next.config.ts`의 rewrite 설정으로 `/api/*` 요청을 `http://localhost:8000`으로 프록시합니다.

### 테스트

```bash
# 백엔드 (Docker)
cd backend
docker build -t genoar-backend .
docker run --rm genoar-backend python -m pytest tests/ -v

# 백엔드 (로컬 venv)
python -m pytest tests/ -q        # 최근 실행 기준 399 passed, 3 skipped

# 프론트엔드 단위 테스트
cd frontend
npm test                          # Jest 8 suites, 91 tests

# 실제 브라우저 확인 (history 뒤로가기, series 하이라이트)
# 사전에 스택이 떠 있고 CDP 포트로 Chrome이 실행돼 있어야 합니다. 헤더 주석 참조.
npm run verify:browser
```

---

## 향후 계획

| Phase | 내용 | 상태 |
|-------|------|------|
| Phase 1 | SQL 구조화 검색 API | 완료 |
| Phase 2 | 종별 FAISS 시맨틱 / 하이브리드 검색, 유사 샘플 | 구현 완료 (배포처에서 인덱스 빌드 필요) |
| Phase 2+ | 다운로드 API (SRA URL + 처리 결과 HDF5), CSV/JSON 내보내기 | 완료 |
| — | 사용자 인증, 북마크, 알림, By Date 탭 | 미착수 |

기능 현황은 위 표들이 그대로 말해 줍니다. **API 엔드포인트** 절이 제공 중인 기능을,
**시맨틱 검색** 절이 종별 인덱스가 덮는 범위를, 이 표가 아직 만들지 않은 것을 담습니다.
