# GENOAR RAG Backend

[English](README.md)

6,199개 SRA 샘플에 대한 검색 API. SQL 구조화 필터, FAISS 기반 종별 시맨틱 검색,
샘플 메타데이터·통계·다운로드·내보내기를 제공합니다.
FastAPI 위에서 MySQL/MariaDB 또는 SQLite로 동작합니다.

## 아키텍처

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
│  │   커넥션 풀                    │           │                   │
│  │   SQLite (WAL) 또는 MySQL      │           │                   │
│  └────────────────────┬──────────┘           │                   │
└───────────────────────┼──────────────────────┼───────────────────┘
                        │                      │
         ┌──────────────┴─────┐   ┌────────────┴──────────────┐
         │ sra_hybrid.db      │   │ faiss_human.bin  (500 MB) │
         │ 또는 MariaDB        │   │ faiss_mouse.bin  ( 36 MB) │
         │ (읽기 전용, 931MB)   │   │ results/success/ (HDF5)   │
         └────────────────────┘   └───────────────────────────┘
```

### 레이어 구조

| 레이어 | 역할 | 파일 |
|--------|------|------|
| **Router** | HTTP 요청/응답, 파라미터 검증 | `app/routers/*.py` |
| **Service** | 비즈니스 로직, 캐싱, N+1 방지 배치 처리, 벡터 라우팅 | `app/services/*.py` |
| **Repository** | SQL 쿼리, 데이터 접근 추상화 | `app/repositories/*.py` |
| **DB** | 커넥션 풀, WAL 모드, MySQL 어댑터 | `app/db/connection.py` |
| **Model** | Pydantic 스키마, 유효성 검증 | `app/models/*.py` |
| **Util** | 정규화, 동적 SQL 빌더 | `app/utils/*.py` |
| **Scripts** | 인덱스 빌드, SQLite → MariaDB 이전 | `scripts/*.py` |

### 설계 패턴

- **Strategy**: `SearchStrategy` 구현체가 셋 — `SQLSearchStrategy`,
  `SemanticSearchStrategy`, `HybridSearchStrategy` — 요청의 `search_mode`로 선택
- **데이터셋**: `DatasetRegistry`가 설정된 데이터셋마다 커넥션 풀과
  `VectorRouter`를 하나씩 소유 (`DATASETS` + `DATASET_<NAME>_*`; 미설정이면
  top-level 설정으로 이름 없는 하나). 요청의 `?dataset=` 의존성이 연결
  시점에 데이터셋을 고르므로 repository·service는 데이터셋의 존재를 모름 —
  이미 올바른 corpus를 가리키는 연결을 받을 뿐. 배포 방법은 상위 README의
  "다중 데이터셋" 참조
- **종별 라우팅**: `VectorRouter`가 종마다 `VectorService`를 하나씩 들고,
  각자 고유한 임베딩 모델과 FAISS 인덱스를 가지며 `organism` 필터로 선택됨
- **Fail-closed 로딩**: 설정된 벡터 백엔드는 반드시 로드에 성공해야 하며,
  실패하면 사유를 남기고 기동이 거부됨 — 인덱스가 없거나 깨졌는데 그 종만
  조용히 사라지는 일은 없음. 한 종 없이 서빙하려면 그 종의 index+mapping
  쌍을 명시적으로 비워야 하고, 키워드 검색은 어느 쪽이든 영향받지 않음
- **Repository**: `sra_core`/`sra_extended` 분리, EAV 쿼리 최적화
- **커넥션 풀**: 기본 5개, 읽기 전용 DB에서도 동작
- **In-memory 캐시**: 필터 옵션·통계, 1시간 TTL
- **DI**: FastAPI `Depends()`로 요청 스코프 DB 커넥션 관리

---

## 데이터베이스 스키마

아래 컬럼명은 코드가 실제로 조회하는 이름입니다. 소문자 관례가 아니라 `Run`,
`Sample_Name`, `STR_tis` 처럼 파이프라인이 만드는 DB에 존재하는 그대로입니다.

### sra_core (6,199 rows)

| 컬럼 | 타입 | 설명 | 용도 |
|------|------|------|------|
| Run | TEXT | SRA Run ID | 조회 키, 정렬 (`sort_by=run_id`) |
| Series | TEXT | GEO Series ID | `series` 필터 |
| BioSample | TEXT | BioSample ID | — |
| Sample_Name | TEXT | 제출자가 붙인 샘플 이름 | 상세 응답 |
| tissue | TEXT | 조직명 (86.5% 채워짐) | `tissue` 필터, 키워드 검색 |
| cell_type | TEXT | 세포 유형 (46.7% 채워짐) | `cell_type` 필터, 키워드 검색 |
| disease_state_modified | TEXT | 정규화된 질병 상태 | API에서는 `disease`로 노출 |
| treatment, sex, Age, strain, genotype | TEXT | 제출자 자유 텍스트 | `treatment`·`genotype`은 키워드 검색 대상 |
| Platform | TEXT | 시퀀싱 플랫폼 | `platform` 필터 |
| Instrument | TEXT | 장비 모델 | — |
| size | REAL | 파일 크기 | 상세 응답 |
| STR_tis, STR_dis, STR_cell | TEXT | 표준화 개념 문자열 | — |
| CUI_tis, CUI_dis, CUI_cell | TEXT | UMLS 개념 코드 | — |
| created_at | TIMESTAMP | 행 생성 시각 | 정렬 (`sort_by=release_date`) |

API 이름과 컬럼이 다른 경우가 둘 있습니다. `disease`는
`disease_state_modified`를 읽고, `sort_by=release_date`는 `created_at`으로
정렬합니다 (`app/utils/query_builder.py`의 `SORT_COLUMN_MAP`).

### sra_extended (126,646 rows) — EAV 구조

| 컬럼 | 타입 | 설명 |
|------|------|------|
| Run | TEXT | FK → sra_core.Run |
| field_name | TEXT | 메타데이터 필드명 (38종) |
| field_value | TEXT | 필드 값 |
| data_type | TEXT | 데이터 타입 (text, numeric) |

**API 필터와 EAV 매핑**:

| API 필터 | EAV field_name | 데이터 규모 |
|----------|---------------|------------|
| `organism` | `Organism` | Homo sapiens 3,114, Mus musculus 3,085 |
| `assay_type` | `Assay Type` | RNA-Seq 5,981, OTHER 218 |
| `library_source` | `LibrarySource` | TRANSCRIPTOMIC 4,791, TRANSCRIPTOMIC SINGLE CELL 1,408 |
| `disease` | `disease_resolved` | 적재 시 run당 하나로 정해 둔 값. UMLS 개념 우선, 없으면 제출자 원문 |

DB에는 `sra_vectors` 테이블도 있고 이전 스크립트가 함께 옮기지만, API는 읽지
않습니다. 벡터 검색은 디스크의 FAISS 인덱스를 씁니다.

---

## API 엔드포인트

### `GET /api/v1/samples`

페이지네이션으로 샘플 목록 조회.

| 파라미터 | 타입 | 기본값 | 설명 |
|---------|------|--------|------|
| `offset` | int | 0 | 시작 위치 (≥0) |
| `limit` | int | 20 | 페이지 크기 (1~100) |

**응답**: `PaginatedResponse[SampleSummary]`

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

개별 샘플 상세 정보 (core + extended 메타데이터).

`{accession}`에는 SRA run id(`SRR...`)와 GEO sample id(`GSM...`)를 모두 넣을 수
있습니다. GSM은 `sra_extended`의 `GEO_Accession (exp)` 필드로 run id를 찾아
해석합니다. 다만 이 필드는 전체 샘플의 일부에만 있으므로, GSM으로 안 찾힌다고
해서 샘플이 없는 것은 아닙니다.

**응답**: `SampleDetail`

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

**에러**: `404` — `SampleNotFoundError`

### `GET /api/v1/samples/{accession}/series`

해당 샘플과 같은 시리즈의 모든 샘플.

**응답**: `PaginatedResponse[SampleSummary]`

### `GET /api/v1/samples/{accession}/similar`

시맨틱 유사 샘플. 해당 run을 담고 있는 인덱스에서 그 샘플 자신의 벡터를 질의로
씁니다. 따라서 텍스트 인코딩이 없고 이 호출로는 모델이 로드되지 않습니다.

| 파라미터 | 타입 | 기본값 | 설명 |
|---------|------|--------|------|
| `limit` | int | 10 | 이웃 개수 (1~100) |

각 항목은 `similarity_score`와 함께 DB에서 조인한 tissue, cell type, disease,
organism, assay type을 담습니다.

**에러**: `503` — 로드된 벡터 백엔드가 없음. 샘플은 있지만 어느 인덱스에도 없는
run은 에러가 아니라 빈 목록을 반환합니다.

### `GET /api/v1/samples/{accession}/download`

해당 샘플의 데이터를 어디서 받을 수 있는지 알려줍니다. **API key가 필요 없습니다.**

원본 read는 여기서 제공하지 않습니다. 저자들이 GEO와 SRA에 그 아카이브의 약관
아래 기탁한 것이라, 파일이 아니라 해당 run의 아카이브 레코드로 연결합니다 —
GSM이 기록된 run은 GEO로, 나머지는 SRA로.

Cell Ranger 산출물(HDF5)은 `PROCESSED_RESULTS_DIR`이 가리키는 디렉터리에서 읽습니다.
Stage 3이 `<디렉터리>/<run_id>/cellranger_output/outs/` 구조로 쌓으므로
(`srr_pipeline_package/pipeline/Snakefile_hs.smk`의 `cellranger_count` 규칙),
디렉터리를 훑으면 어느 run의 산출물이 어디 있는지는 알 수 있습니다.

파일이 디스크에 있다는 것은 질문의 시작입니다. results 디렉터리는 개별 실행보다 오래
남으므로, 거기 놓인 매트릭스 하나만으로는 어떤 입력에서 나온 것인지 알 수 없습니다.
Stage 3은 각 run의 산출물 옆에 영수증(`.genoar_cellranger.json`)을 남겨, 그것을 만든
실행이 무엇이고 그 실행이 검증했는지를 기록합니다. 이 엔드포인트는 파일을 내주기 전에
같은 영수증을 읽습니다.

| `provenance.status` | 영수증이 말하는 것 | 제공 여부 |
|---------------------|--------------------|-----------|
| `verified` | 어떤 실행의 Cell Ranger가 직접 만들고 기록함 | 항상 제공 |
| `adopted` | 운영자가 실행에게 아무도 책임질 수 없는 산출물을 재사용하라고 지시함 (`GENOAR_ADOPT_PRIOR_RESULTS=1`) | 제공 안 함 |
| `unreadable` | 영수증은 있는데 이 서비스가 처리할 수 없음. 파싱되지 않거나, 서비스가 모르는 provenance를 적고 있음 | 제공 안 함 |
| `unrecorded` | 영수증이 없음. provenance 기록 도입 이전 산출물이거나, 이 파이프라인 밖에서 만들어짐 | `PROCESSED_RESULTS_REQUIRE_RECEIPT`가 true가 아니면 제공 |

`PROCESSED_RESULTS_REQUIRE_RECEIPT`가 결정하는 것은 `unrecorded` 행 하나뿐입니다.
기본값은 false이며, 영수증 도입 이전 결과를 가진 배포가 그것을 계속 제공하도록
두기 위해서입니다. adopted와 unreadable 산출물은 이 설정과 무관하게 제공하지 않습니다.
둘 다 이미 영수증을 쓰는 파이프라인에서만 나올 수 있으므로, 영수증 이전 배포에는
아예 존재하지 않습니다.

응답에는 `provenance` 객체가 실립니다. 산출물을 제공하든 하지 않든 실리므로, 호출자는
보류된 샘플과 처리되지 않은 샘플을 구분할 수 있습니다. `status` 외에 `verified`,
`offered`, `receipt_run_id`, `recorded_at`, 그리고 배포가 무엇을 했고 무엇을 하면 되는지
알려주는 `note`가 함께 들어갑니다. 각 처리 결과 source도 자기 `provenance` 필드에 같은
status를 싣습니다. `sources`만 읽는 클라이언트도 검증된 산출물을 가려낼 수 있습니다.

처리되지 않은 run은 `available: false`와 그 이유를 반환하며, `provenance`는 없습니다.

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

보류한 산출물은 이름은 밝히고 파일은 내주지 않습니다. `processed_h5` source 하나가
`available: false`로, provenance status와 사유를 담은 `note`와 함께 돌아옵니다.

```json
{"kind": "processed_h5", "available": false, "provenance": "adopted",
 "note": "This output was adopted on an operator's instruction ..."}
```

응답에는 `processing`도 실립니다 — Cell Ranger 버전, 감지된 chemistry, 매트릭스가
정렬된 reference. 별도로 기록해둔 값이 아니라 매트릭스 파일에서 직접 읽으므로
파일과 어긋날 수 없습니다. 처리된 매트릭스가 없는 run에서는 생략되고, 서비스가 보류한
산출물에서도 생략됩니다. 내주지 않을 파일이 어떻게 만들어졌는지 설명하는 것은 같은
주장을 더 조용한 자리에서 하는 일이기 때문입니다.

다른 호스트에서 서빙하는 파일(`PROCESSED_H5_URL_TEMPLATE`)에는 `provenance`가 없습니다.
여기서 확인할 수 있는 것이 없으므로 아무것도 주장하지 않으며,
`PROCESSED_RESULTS_REQUIRE_RECEIPT`도 영향을 주지 않습니다.

`action`은 파일과 링크를 구분합니다. `download`는 GENOAR가 직접 서빙하는 것 —
정리한 메타데이터와 파이프라인이 만든 매트릭스입니다. `visit`은 원본 read가
있는 아카이브의 레코드 페이지로, 저자들이 그 아카이브의 약관 아래 기탁한
것이라 GENOAR가 재배포하지도, 자기 다운로드인 것처럼 표시하지도 않습니다.
GEO accession이 있는 run은 `geo_record`, 나머지는 `sra_record`입니다.

**에러**: `404` — 해당 accession의 샘플이 없음

### `GET /api/v1/samples/{accession}/files/{filename}`

처리 결과 파일을 내려받습니다. 파이프라인이 만드는 알려진 파일명만 해석되므로
results 디렉터리 밖으로 경로를 유도할 수 없습니다.

| filename | 크기(대략) | 용도 |
|----------|-----------|------|
| `filtered_feature_bc_matrix.h5` | 20 MB | 다운스트림 분석의 기본 입력 |
| `raw_feature_bc_matrix.h5` | 31 MB | 비필터 행렬 |
| `molecule_info.h5` | 265 MB | 검출된 분자 하나당 한 줄, 유전자별로 합산하기 전 |

**에러**: `404` — 알려지지 않은 파일명이거나, 해당 run이 처리되지 않았거나, 이 배포가
보류하는 산출물이거나, `PROCESSED_RESULTS_DIR`이 설정되지 않음

이 경로도 다운로드 목록과 같은 provenance 검사를 거쳐 파일을 해석합니다. 보류된 샘플은
파일명을 직접 입력해도 받을 수 없습니다.

### `GET /api/v1/search`

구조화 검색. 다중 필터 조합과 키워드, 그리고 벡터 백엔드가 로드된 경우 시맨틱·
하이브리드 모드를 지원합니다.

**필터 파라미터** (모두 Query Parameter):

| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `organism` | list[str] | 생물종 (다중 선택). 시맨틱 인덱스 선택에도 쓰임 |
| `tissue` | list[str] | 조직명 (다중 선택, 대소문자 무시) |
| `cell_type` | list[str] | 세포 유형 (다중 선택) |
| `assay_type` | list[str] | 분석 유형 (다중 선택) |
| `library_source` | list[str] | 라이브러리 소스 (다중 선택) |
| `disease` | list[str] | 질병 (다중 선택, `disease_resolved` 단일 필드) |
| `platform` | list[str] | 플랫폼 (다중 선택) |
| `series` | str | GEO Series ID (단일 값) |
| `keyword` | str | 텍스트 검색 (tissue, cell_type, treatment, genotype 대상) |
| `search_mode` | str | `sql`(기본), `semantic`, `hybrid` |
| `offset` | int | 시작 위치 (기본 0) |
| `limit` | int | 페이지 크기 (기본 20, 최대 100) |
| `sort_by` | str | `run_id` 또는 `release_date` |
| `sort_order` | str | `asc` 또는 `desc` |

`semantic` 모드는 키워드를 인코딩해 벡터 인덱스와 대조하며, 키워드가 비어 있으면
전체가 아니라 빈 결과를 돌려줍니다. `hybrid` 모드는 시맨틱 후보군
(`SEMANTIC_POOL_SIZE`, 500건)을 SQL 필터와 교집합합니다. 둘 다 벡터 백엔드가
없으면 `503`입니다.

**요청 예시**:
```
GET /api/v1/search?tissue=bone+marrow&organism=Homo+sapiens&limit=3
```

**응답**: `SearchResponse`

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

필터에 걸린 샘플들의 core 메타데이터를 CSV 또는 JSON으로 내보냅니다.
필터 파라미터는 `/api/v1/search`와 동일하고, API key가 필요 없습니다.

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `format` | `csv` | `csv` 또는 `json` |
| `max_rows` | 250000 | 페이지 크기가 아니라 상한. 기본값이 코퍼스보다 크므로 그냥 두면 필터에 걸린 전부를 반환 |
| `accession` | — | SRR/GSM 단건 내보내기. 지정하면 다른 필터는 무시됨 |

컬럼은 명시적 allowlist입니다. 논문이 저작권을 이유로 GEO 제출 메타데이터를
download section에서 제외한다고 기술하므로, 식별자와 아카이브 기술 필드,
그리고 GENOAR가 정규화한 tissue/cell_type/disease와 UMLS 코드만 나갑니다.
제출자가 직접 쓴 자유 텍스트(`Sample_Name`, `treatment`, `sex`, `Age`, `strain`,
`genotype`)와 `sra_extended` 전체는 내보내지 않습니다. CSV는 스트리밍하고,
JSON은 한 문서여야 하므로 메모리에 구성합니다.

### `GET /api/v1/filters`

필터 옵션 목록 (값 + 건수). 1시간 캐시됨. 7개 카테고리 — tissue, cell_type,
platform, organism, assay_type, library_source, disease.

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

대시보드 통계. 1시간 캐시됨.

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

헬스 체크이자, 시맨틱 검색이 무엇을 서빙할 수 있는지에 대한 유일한 근거입니다.

```json
{"status": "ok", "datasets": ["default"], "semantic_search": true, "semantic_species": ["human", "mouse"]}
```

설정된 백엔드가 로드에 실패하면 제외되는 대신 기동 자체가 거부되므로, 떠
있는 서버의 `semantic_species`는 설정이자 곧 실제로 동작하는 종 목록입니다.

### `GET /api/v1/datasets`

이 배포가 서빙하는 데이터셋 목록 — 클라이언트가 `?dataset=`을 넘기기 전에
필요한 정보입니다.

```json
{"items": [{"name": "main", "label": "Curated", "is_default": true},
           {"name": "atlas", "label": "Atlas", "is_default": false}],
 "default": "main", "is_split": true}
```

모든 `/api/v1` 엔드포인트가 `?dataset=<이름>`을 받습니다. 생략하면 기본
데이터셋이고, 없는 이름은 폴백 없이 404입니다. 단일 데이터셋 배포는 항목
하나와 `is_split: false`를 돌려주며 URL에 dataset이 붙지 않습니다.

---

## 시맨틱 검색

논문(GENOAR) 설계대로 종마다 임베딩 모델 하나와 FAISS 인덱스 하나를 씁니다.

| 종 | 모델 | 차원 | 벡터 수 | 인덱스 크기 |
|----|------|------|---------|------------|
| human | `cambridgeltl/SapBERT-from-PubMedBERT-fulltext` | 768 | 3,114 | 9.1 MB |
| mouse | `sentence-transformers/all-MiniLM-L6-v2` | 384 | 3,085 | 4.5 MB |

### 라우팅

`VectorRouter`는 요청의 `organism` 필터로 백엔드를 고릅니다
(`ORGANISM_TO_SPECIES`). organism이 없거나 매핑되지 않은 값이면
로드된 모든 종을 검색합니다(화면의 `All`이 보내는 상태). 여러 종에 걸치는 질의는 각각 검색한 뒤
**순위로 번갈아 병합합니다.** 종마다 모델이 달라(human SapBERT, mouse MiniLM)
코사인 점수가 같은 척도가 아니기 때문입니다. 합친 뒤 점수로 정렬하던 때는
어느 종이 상위를 차지하는지가 관련성이 아니라 그 질의에서 어느 모델이 높은
점수를 내느냐로 결정됐습니다 — `tumor microenvironment`는 human 3,114건 중
한 건도 나오지 않고 mouse만 100건, `covid-19 samples`는 mouse 최고점이 더
높은데도 human 98 · mouse 2였습니다. 순위는 비교 가능하므로 라운드마다 각 종
하나씩 가져오고, 같은 라운드 안에서는 점수가 높은 쪽을 먼저 놓습니다.

organism을 지정한 요청(논문이 평가한 경로)은 단일 인덱스를 그대로 쓰므로 이
병합을 거치지 않습니다.

`/similar`는 라우팅 방식이 다릅니다. 각 백엔드에 해당 run id를 갖고 있는지 물어
찾으므로 organism 힌트가 필요 없습니다.

### 빌드 검증

`scripts/build_faiss_index.py`는 인덱스를 만들 때 어떤 모델로 빌드했는지를
`<index>.meta.json` sidecar에 기록합니다. 빌드된 모델과 다른 모델로 인덱스를
질의하면 에러 없이 틀린 결과가 나오므로, 서비스가 로드 시점에 검사합니다.

| sidecar | 모델 | 동작 |
|---------|------|------|
| 있음 | 일치 | 로드 |
| 있음 | 불일치 | 로드 거부 (예외) |
| 없음 | 검증 불가 | `REQUIRE_INDEX_MODEL_MATCH=true`(기본)면 거부, 아니면 경고 후 로드 |

백엔드가 로드를 거부하면 불일치 내용을 오류로 남기고 기동이 중단됩니다. 그 종
없이 서빙하려면 index와 mapping 설정을 비우세요 — 명시적으로 없는 백엔드는
설정이고, 로드에 실패하는 백엔드는 결함입니다.

```bash
python scripts/build_faiss_index.py --db ../data/sra_hybrid.db \
  --model cambridgeltl/SapBERT-from-PubMedBERT-fulltext \
  --organism "Homo sapiens" \
  --out-index ../data/faiss_human.bin \
  --out-mapping ../data/run_mapping_human.json
```

그 밖의 옵션: `--cache-dir`, `--batch-size`(기본 64), `--limit`(테스트용 행 수 제한).

임베딩 모델은 첫 텍스트 질의 때 지연 로드됩니다. 서비스 기동만으로는 그 비용을
치르지 않고, `/similar`는 아예 치르지 않습니다.

---

## 프로젝트 구조

```
backend/
├── pyproject.toml              # 의존성 및 프로젝트 설정
├── Dockerfile                  # Python 3.12 기반 이미지
├── app/
│   ├── main.py                 # 앱 팩토리, lifespan(DB 풀 + 벡터 라우터), CORS, 예외 핸들러
│   ├── config.py               # pydantic-settings 기반 환경 설정
│   ├── dependencies.py         # DI (DB 풀, 벡터 서비스)
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
│   │   ├── samples.py          # /api/v1/samples/*  (상세, series, similar, download, files)
│   │   ├── search.py           # /api/v1/search
│   │   ├── filters.py          # /api/v1/filters
│   │   ├── stats.py            # /api/v1/stats
│   │   └── export.py           # /api/v1/export
│   ├── services/
│   │   ├── sample_service.py   # 샘플 상세 조회, N+1 방지 배치 처리
│   │   ├── search_service.py   # SQL / Semantic / Hybrid 전략
│   │   ├── vector_router.py    # 벡터 백엔드 종별 라우팅
│   │   ├── vector_service.py   # FAISS 인덱스 + 모델 하나, 빌드 검증 포함
│   │   ├── accession.py        # SRR / GSM 해석
│   │   ├── download_service.py # 원본·처리 결과 소스 탐색
│   │   ├── export_service.py   # 컬럼 allowlist, CSV/JSON 생성
│   │   ├── filter_service.py   # 필터 옵션 + 1h TTL 캐시
│   │   └── stats_service.py    # 대시보드 통계 + 1h TTL 캐시
│   ├── repositories/
│   │   ├── base.py             # BaseRepository (fetchone/fetchall/fetchval)
│   │   ├── sample_repository.py
│   │   ├── extended_repository.py
│   │   └── search_repository.py
│   └── utils/
│       ├── normalization.py    # 대소문자 정규화, LIKE 이스케이프, 질병 필드명 상수
│       └── query_builder.py    # 동적 SQL 빌더 (파라미터화 쿼리)
├── scripts/
│   ├── build_faiss_index.py    # 종별 인덱스 빌드 + meta sidecar 생성
│   └── migrate_sqlite_to_mysql.py
└── tests/
    ├── conftest.py             # 테스트 DB 픽스처 (대표 샘플 + EAV 데이터)
    ├── unit/                   # 테스트 함수 161개
    ├── integration/            # 테스트 함수 35개
    └── api/                    # 테스트 함수 80개
```

---

## 실행 방법

### Docker

```bash
cd genoar_rag/backend

# 이미지 빌드
docker build -t genoar-backend .

# 테스트 실행
docker run --rm -v $(pwd):/app genoar-backend python -m pytest tests/ -v

# 전체 스택(백엔드 + 프론트엔드)은 상위 디렉터리에서 기동
cd .. && docker compose up
# → http://127.0.0.1:8001/health (Swagger UI 없음, 포트는 loopback 전용)
```

이미지는 빌드 시점에 레거시 PubMedBERT 가중치를 내장합니다. 현재 설정된
모델(SapBERT, MiniLM)은 내장돼 있지 않으므로, 캐시가 비어 있다면 첫 텍스트 질의
때 `EMBEDDING_MODEL_CACHE_DIR`로 내려받습니다.

### 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DB_BACKEND` | `mysql` | `mysql` 또는 `sqlite` |
| `DB_PATH` | `/data/genoar/sra_hybrid.db` | SQLite 경로 (`DB_BACKEND=sqlite`, 폴백 대상이기도 함) |
| `DB_HOST` | `localhost` | MySQL/MariaDB 호스트 |
| `DB_PORT` | `3306` | MySQL/MariaDB 포트 |
| `DB_USER` | `genoar` | MySQL/MariaDB 사용자 |
| `DB_PASSWORD` | (빈 값) | MySQL/MariaDB 비밀번호 |
| `DB_NAME` | `genoar` | MySQL/MariaDB 데이터베이스명 |
| `DB_POOL_SIZE` | `5` | 커넥션 풀 크기 |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | 허용 CORS 출처 |
| `CACHE_TTL_SECONDS` | `3600` | 캐시 TTL (초) |
| `SEMANTIC_SEARCH_ENABLED` | `true` | 벡터 검색 전체 on/off |
| `HUMAN_EMBEDDING_MODEL_NAME` | SapBERT | human 임베딩 모델 |
| `HUMAN_FAISS_INDEX_PATH` / `HUMAN_RUN_MAPPING_PATH` | `/data/genoar/faiss_human.bin` / `...json` | human 인덱스·매핑 |
| `MOUSE_EMBEDDING_MODEL_NAME` | all-MiniLM-L6-v2 | mouse 임베딩 모델 |
| `MOUSE_FAISS_INDEX_PATH` / `MOUSE_RUN_MAPPING_PATH` | `/data/genoar/faiss_mouse.bin` / `...json` | mouse 인덱스·매핑 |
| `REQUIRE_INDEX_MODEL_MATCH` | `true` | 모델 일치를 확인할 수 없는 인덱스는 거부 |
| `DEFAULT_SPECIES` | `human` | 과거 값. 지금은 organism 필터가 없으면 로드된 모든 종을 검색합니다 |
| `DEFAULT_SIMILAR_LIMIT` | `10` | 기본 이웃 개수 |
| `EMBEDDING_MODEL_CACHE_DIR` | (미설정) | HuggingFace 캐시 디렉터리 |
| `PROCESSED_RESULTS_DIR` | (빈 값) | Stage 3 success 디렉터리. 비우면 처리 결과 다운로드 비활성화 |
| `PROCESSED_RESULTS_REQUIRE_RECEIPT` | `false` | provenance 영수증이 없는 산출물을 보류. adopted·unreadable은 이 설정과 무관하게 보류. `PROCESSED_RESULTS_DIR`에만 적용 |
| `PROCESSED_H5_URL_TEMPLATE` | (빈 값) | `PROCESSED_RESULTS_DIR`가 없을 때만 사용 |
| `SRA_DOWNLOAD_URL_TEMPLATE` | NCBI ODP 버킷 | 원본 read를 안내할 주소 |

### 데이터베이스 백엔드 (SQLite / MariaDB)

같은 코드가 두 백엔드를 지원합니다. 로컬 개발과 테스트는 파일 기반 SQLite를 쓰고,
서버 배포에서는 `DB_BACKEND=mysql`(기본값)로 MySQL/MariaDB 서버에 연결합니다.
백엔드 선택만 다를 뿐 쿼리 코드는 동일합니다 (SQLite `?` 플레이스홀더를 MySQL `%s`로
변환하는 얇은 어댑터만 추가됨).

`DB_BACKEND=mysql`인데 기동 시 서버에 접속할 수 없으면 `DB_PATH`의 SQLite 파일로
폴백합니다. 그 파일마저 없으면 조용히 빈 서비스를 띄우지 않고 기동에 실패합니다.
`DB_BACKEND=sqlite`는 폴백이 없습니다.

파이프라인이 만드는 `sra_hybrid.db`(SQLite 파일)를 MariaDB로 그대로 옮기려면
`scripts/migrate_sqlite_to_mysql.py`를 씁니다. 4개 테이블(`sra_core`,
`sra_extended`, `sra_vectors`, `sra_series`)의 스키마를 MariaDB에 만들고 모든 행을 id까지 그대로
복사합니다. 마지막에 행 수를 원본과 비교해 검증합니다.

```bash
cd genoar_rag/backend

# 1. MariaDB 기동 (compose의 mysql 프로파일, genoar_rag/ 디렉터리에서)
#    docker compose --profile mysql up -d mariadb

# 2. SQLite -> MariaDB 통째 복사. 갱신에도 --drop 을 쓴다 — --upsert 는 삭제를
#    하지 않아 원본에서 사라진 행이 대상에 남는다(19만 → 6,199 축소가 그 경우다)
python scripts/migrate_sqlite_to_mysql.py \
  --sqlite ../data/sra_hybrid.db \
  --host 127.0.0.1 --port 3306 --user genoar --password <PW> --db genoar \
  --drop

# 3. 백엔드를 MariaDB로 실행
DB_BACKEND=mysql DB_HOST=127.0.0.1 DB_USER=genoar DB_PASSWORD=<PW> DB_NAME=genoar \
  uvicorn app.main:app
```

MariaDB 백엔드는 `pymysql` 드라이버가 필요합니다 (`pyproject.toml`에 포함).
스키마는 텍스트 컬럼을 모두 `TEXT`로 두고 인덱스가 필요한 컬럼(`Run`, `field_name`,
`STR_*` 등)만 prefix 인덱스를 걸어, 값 길이에 관계없이 복사가 실패하지 않게 했습니다.

### 의존성

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

## 테스트

최근 전체 실행: **399 passed, 3 skipped**.

| 카테고리 | 테스트 함수 | 대상 |
|----------|-----------|------|
| Unit | 161 | 모델, 정규화, SQL 빌더, search/filter/stats/sample 서비스, accession, download, export, 벡터 서비스·라우터, 인덱스 빌드 |
| Integration | 35 | DB 커넥션, sample/extended/search 레포지토리, 벡터 통합 |
| API | 80 | samples, search, filters, stats, download, similar, export 엔드포인트 |

```bash
python -m pytest tests/ -q                  # 전체
python -m pytest tests/unit -q              # 카테고리 단위
python -m pytest tests/api/test_search_api.py -q
```

테스트는 `conftest.py`가 만드는 픽스처 DB로 돌기 때문에 운영 데이터나 FAISS
인덱스가 없어도 됩니다.

### 실제 DB로 확인한 수치

```
Total samples: 6,199          Total series: 888
sra_core:      6,199 rows     sra_series: 7,069 run-study 관계
sra_extended:  126,646 rows, field_name 38종
sra_vectors:   6,199 rows     tissue 채움률 100%   cell_type 채움률 35.8%

Filter categories:
  tissue:        295 distinct, top=Lung (406)
  cell_type:     310 distinct, top=CD45+ cells (134)
  platform:        1 distinct, top=ILLUMINA (6,199)
  organism:        2 distinct, top=Homo sapiens (3,114)
  assay_type:      2 distinct, top=RNA-Seq (5,981)
  library_source:  2 distinct, top=TRANSCRIPTOMIC (4,791)
  disease:        55 distinct, top=Control Groups (152)
```

2026-08-04 측정. 큐레이션 filtered 테이블이 지명한 run으로 DB를 줄인 뒤의 값이다.
그 이전 수치는 GEO 크롤 전체(41개 종 197,757 run)를 가리키며, 서비스가 제공하는
모집단이 아니다.

---

## 엣지 케이스 처리

| 케이스 | 처리 방식 |
|--------|----------|
| tissue 대소문자 불일치 (blood/Blood/BLOOD) | `LOWER()` 비교 |
| cell_type 53% NULL | Optional 필드, NULL 제외 |
| disease 데이터 파편화 | 적재 시 `disease_resolved` 하나로 정리 (필터 55종) |
| EAV 쿼리 성능 | `Run IN (SELECT ...)` 서브쿼리 패턴, JOIN 회피 |
| 키워드 SQL injection | 파라미터화 쿼리 only, LIKE 특수문자 이스케이프 |
| 빈 필터 요청 | 전체 샘플 페이지네이션 반환 |
| 존재하지 않는 run id | 404 SampleNotFoundError |
| 해석되지 않는 GSM | 404. 해당 필드가 일부 샘플에만 있어 부재의 증거는 아님 |
| 대량 결과셋 | limit 최대 100, 별도 COUNT 쿼리 |
| N+1 문제 (목록의 extended 필드) | Run ID 배치 수집 후 IN 절 단일 쿼리 |
| DB가 읽기 전용 | WAL/query_only PRAGMA 실패 시 graceful skip |
| MariaDB 접속 불가 | 그 dataset 자신의 SQLite 파일이 있고 serving 테이블을 갖출 때만 폴백. 인증·스키마 오류는 폴백 없이 기동 거부 |
| 다른 모델로 빌드된 인덱스 | 로드 거부. 불일치 사유를 남기고 기동 실패 |
| 벡터 백엔드 없이 시맨틱 질의 | 조용한 빈 결과가 아니라 503 |
| 시맨틱 키워드가 빈 값 | 전체가 아니라 빈 결과 |
| 어느 인덱스에도 없는 run | `/similar`가 에러가 아니라 빈 목록 반환 |
| 파이프라인이 책임질 수 없는 처리 결과 | 다운로드 응답에 `available: false`와 사유로 밝히고, `/files`에서 보류하며, `processing`은 생략 |
| 자기 자신이 이웃에 포함 | `/similar` 결과에서 제외 |

---

## 성능 최적화 (미적용)

DB에 쓰기 권한이 있으면 복합 인덱스를 생성하여 EAV 쿼리 성능을 개선할 수 있습니다:

```sql
CREATE INDEX IF NOT EXISTS idx_extended_field_value
  ON sra_extended(field_name, field_value);
```

**예상 효과**: EAV 필터 쿼리 ~700ms → <50ms

---

## 향후 계획

- **성능 인덱스**: `idx_extended_field_value` 적용
- **종 간 점수 보정**: 여러 종에 걸친 시맨틱 결과를 서로 다른 모델의 원점수로
  병합하고 있어, 보정된 순위 체계가 필요함
- **사용자 기능**: 인증·북마크·알림 미구현. 프론트엔드에 자리만 잡혀 있음
