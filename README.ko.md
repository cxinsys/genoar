> English: [README.md](README.md)

# GENOAR

NCBI GEO 데이터의 자동 수집, UMLS 매칭 분석, 그리고 Single-cell RNA-seq 데이터 처리를 위한 통합 파이프라인입니다.

## 전체 파이프라인 개요

GENOAR는 세 가지 주요 파이프라인이 순차적으로 연결되어 동작합니다:

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                          GENOAR End-to-End Pipeline                              │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  [Stage 1: Crawler]          [Stage 2: Analysis]         [Stage 3: SRR Pipeline] │
│   ┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐    │
│   │  NCBI GEO       │         │  UMLS Matching  │         │  Cell Ranger    │    │
│   │  Web Scraping   │────────▶│  & Annotation   │────────▶│  Processing     │    │
│   │                 │         │                 │         │                 │    │
│   └─────────────────┘         └─────────────────┘         └─────────────────┘    │
│          │                           │                           │               │
│          ▼                           ▼                           ▼               │
│   ┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐    │
│   │ crawl_output/   │         │first_pass_output│         │ results/success │    │
│   │ • META/*.txt    │         │ • cell_type.csv │         │ • BAM files     │    │
│   │ • SMTX/*.gz     │         │ • tissue.csv    │         │ • Gene matrix   │    │
│   │ • SRR/*.txt     │         │ • disease.csv   │         │ • Clusters      │    │
│   └─────────────────┘         └─────────────────┘         └─────────────────┘    │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 데이터 흐름 요약

| 단계 | 구성요소 | 입력 | 출력 | 주요 분석 |
|------|----------|------|------|-----------|
| 1 | genoar_crawler | NCBI GEO 검색 쿼리 | META, SMTX, SRR 파일 | Single-cell 데이터셋 탐지 |
| 2 | genoar_analysis | META 파일 + UMLS DB | 주석이 달린 메타데이터 | 생물의학 개념 매핑 |
| 3 | srr_pipeline_package | SRA 파일 + Reference | Cell Ranger 결과물 | 유전자 발현 분석 |

**현재 end-to-end 지원 범위:** 공개 자동 경로 전체는 사람(`Homo sapiens`)
전사체 데이터를 대상으로 하며, Stage 3은 GRCh38 참조 유전체를 전제로 합니다. 다른
종은 수동 또는 별도 변경이 필요하며, 이 end-to-end 경로의 지원 범위가 아닙니다.

### 파이프라인이 갈라지는 지점

세 스테이지는 요구사항이 서로 다르며 명시적인 인계 단계가 있습니다. Stage 1과
Stage 2는 Docker만 있으면 아키텍처에 무관하게 `make run` 한 번으로 함께 돌아갑니다.
Stage 3에는 Cell Ranger, GRCh38, 내려받은 SRA 입력이 필요합니다. 각 명령을 따로
실행하거나, 아래의 상한 있는 다운로드를 포함한 `make run-full`로 연결할 수 있습니다.

| Stage 3에 필요한 것 | 준비 방법 |
|---------------------|-----------|
| `.sra` 파일 | `make fetch-sra` — Stage 2가 선별한 사람 전사체 액세션을 캐시하고 `make run-fetched-stage3`용 정확한 입력 뷰를 만듭니다 |
| `cellranger/`의 Cell Ranger | 10x Genomics에서 직접 다운로드 (가입 필요) |
| `ref/`의 참조 게놈 | 직접 다운로드, 약 15GB |
| `srr_pipeline_package/configs/example.yaml` | 레포지토리에 포함되어 있습니다. 코어 수·경로를 편집하세요 |
| x86_64 머신 | Cell Ranger는 ARM용으로 배포되지 않습니다 |

`make run-stage3`과 `make run-fetched-stage3`은 실행 전에 위 다섯 항목을 하나씩 확인하고, 빠진 것을 지목하며
멈춥니다. 쓸 수 있는 상태인지까지 확인합니다. 존재하기만 해서는 통과하지 않습니다.
`cellranger/cellranger` 또는 `cellranger/bin/cellranger` 중 하나가 존재하고 실행
가능해야 하며, `ref/`에는 비어 있지 않은 `reference.json`과 `fasta/`, `genes/`,
`star/` 디렉터리가 있어야 합니다. 아키텍처를 가장 먼저 확인합니다. x86_64 실행
파일은 ARM 호스트에서도 존재하고 실행 권한이 있는 것으로 보여 컨테이너의 step 0에
가서야 실패하기 때문입니다. x86_64를 에뮬레이션하는 호스트라면
`GENOAR_ALLOW_NON_X86=1`로 넘어갈 수 있습니다. 실행 파일만이 아니라 Cell Ranger
설치 루트 전체를 마운트합니다. 컨테이너 안의 파이프라인도 step 0에서 같은 검사를 반복하므로,
한쪽이 받아들인 설치는 다른 쪽도 받아들입니다. `make run-full`은 `make run`, 상한이
있는 Stage 2 인계인 `make fetch-sra`, `make run-fetched-stage3` 순서로 실행하며 기본적으로
시료 2건만 내려받습니다. Cell Ranger와 GRCh38이 미리 설치되어 있어야 하고, 인계
중에는 네트워크가 필요합니다. `MAX_SAMPLES`를 높이면 다운로드도 늘어나며,
`MAX_SAMPLES=all`은 매우 큰 저장공간과 전송 시간을 요구할 수 있습니다.

준비 명령은 [2. 환경 구성](#2-환경-구성)을 참고하세요.

---

## 빠른 시작 (make)

```bash
# 1. 클론과 설정
git clone https://github.com/cxinsys/genoar.git && cd genoar
cp .env.example .env         # 필요한 값만 수정

# 2. 최초 설정 (디렉토리 생성 + Docker 이미지 빌드 + 자원 점검)
make setup

# 3. Stage 1+2 실행 (크롤링 + 분석)
make run

# 4. 결과 확인
make status
```

여기까지가 Stage 1+2 전부입니다. 파이프라인 자체는 Docker 안에서 돌고 네트워크가
필요합니다. 호스트에서 도는 것은 컨테이너가 끝난 뒤 출력되는 요약 하나뿐이며, 이때
호스트의 Python 3.9+를 씁니다([사전 요구사항](#1-사전-요구사항) 참고).
`make setup`이 빌드하는 이미지는 빌드 대상 아키텍처에 따라 브라우저를
고릅니다(amd64는 Google Chrome, arm64는 Chromium). 그래서 Apple Silicon Mac에서도
위 세 명령이 그대로 동작합니다. 여기에 따라붙는 Docker 버전 요구사항은
[플랫폼 안내](#1-사전-요구사항)를 보십시오.

`make setup`은 빌드에 앞서 저장소를 Docker에 빌드 컨텍스트로 넘깁니다. 그 컨텍스트가
이제 100MB 미만입니다. RAG 서비스의 로컬 DB, 모델 캐시, 빌드된 프런트엔드가
제외됐습니다. 몇 분씩 "transferring context"에서 멈춘 것처럼 보이던 구간이 금방
지나갑니다.

Stage 3으로 넘어가려면 `make fetch-sra`가 두 반쪽을 이어줍니다. Stage 2의 품질
필터를 통과한 `first_pass_output/HS_*_1st_pass_meta_table.csv`에서 SRR 액세션을
추출하고 중복을 없앤 뒤 `sample_sra/`에 캐시하고, 그 선택만 담은
`sample_sra/.genoar_selected/` 입력 뷰를 만듭니다. 이전 원시 또는 사용자 지정
다운로드는 캐시에 남지만 `make run-fetched-stage3`의 입력이 되지 않습니다. Stage 2
결과가 없거나 적격 SRR이 없으면 중단하며, 원시 크롤 결과로 조용히 대체하지 않습니다.

```bash
# Stage 2가 선별한 SRR 액세션을 .sra 파일로 (기본 2건)
make fetch-sra

# 무엇을 받을지만 확인하고 중단
make fetch-sra DRY_RUN=1

# 더 많이, 더 병렬로
make fetch-sra MAX_SAMPLES=10 MAX_CONCURRENT=8

# Stage 2가 선별한 전부 — 매우 큰 다운로드일 수 있습니다
make fetch-sra MAX_SAMPLES=all

# 선택 사항: 필터되지 않은 Stage 1 원시 목록은 명시적으로만 사용합니다
make fetch-sra SRA_SOURCE=raw-stage1 DRY_RUN=1

# Stage 3 이미지 빌드 후 실행
make build-srr
make run-fetched-stage3

# 또는 sample_sra/에 직접 둔 사용자 관리 입력을 모두 처리
make run-stage3

# Stage 1+2, 기본 2건 다운로드, Stage 3 순서로 실행
# (네트워크와 미리 설치한 Cell Ranger·GRCh38 필요)
make run-full
```

SRA 다운로드는 샘플당 수십 GB에 이를 수 있어 `make fetch-sra`에는 상한이 있습니다.
`make run-full`도 별도 값을 주지 않으면 기본 2건 상한을 유지합니다.
`MAX_SAMPLES=all`은 이 상한을 의도적으로 해제하는 요청입니다. `sample_sra/`에 직접
관리하는 `.sra`를 처리하려면 fetch를 생략하고 `make run-stage3`을 실행하십시오.
통합 경로에서는 `make run-fetched-stage3`이 최신 선택만 마운트하므로 이전 캐시가
조용히 섞이지 않습니다.

### 실행 중인 파이프라인 보기

파이프라인 컨테이너에는 고정된 이름이 없습니다. 컨테이너 이름은 호스트 전체에서
하나뿐이고 프로젝트 단위로 나뉘지 않으므로, 이름을 고정하면 두 번째
`docker compose up`이 자기 컨테이너를 띄우는 대신 앞선 실행의 컨테이너를 다시
만들고, 같은 이름의 멈춘 컨테이너 하나가 다음 실행을 아예 막습니다. Compose가
프로젝트 이름을 따 컨테이너 이름을 정합니다.
이름은 Compose에 물어보십시오.

```bash
make logs                                  # 이 프로젝트의 파이프라인 로그 따라가기
docker compose -p genoar logs -f genoar    # 같은 명령을 그대로 쓴 것
docker compose -p genoar ps                # 이 프로젝트의 컨테이너 확인
```

`make logs`는 넘겨받은 프로젝트를 따라가므로, 두 번째 실행은
`make logs COMPOSE_PROJECT_NAME=second`입니다. Ctrl+C는 보기를 멈춥니다. 실행은
계속됩니다.

`genoar_crawler/`에서 시작한 병렬 크롤은 다릅니다. 워커가 각각 별도 컨테이너이며
이름은 `genoar-worker-<run_id>-<n>`, 라벨은 `genoar.run=<run_id>`입니다. 고를 때는
반드시 라벨로 고르십시오. `docker ps --filter label=genoar.run=<run_id>`입니다.
`--filter name=`은 쓰지 마십시오. 부분 문자열 일치라 남의 실행까지 잡습니다.
자세한 내용은 [병렬 크롤링](genoar_crawler/README.md#parallel-crawling)에 있습니다.

> **기존 체크아웃을 올릴 때.** 이 변경 이후 첫 `make run`에서 Compose가 남아 있던
> `genoar-pipeline` 컨테이너를 자기 라벨로 알아보고 `<project>-genoar-1`로
> 교체합니다. 한 번뿐인 이행이며 잃는 것은 없습니다. 이름이 고정돼 있던 옛
> `genoar-network`는 `make docker-clean`으로 정리됩니다.

### .env 설정

`.env`는 Docker Compose와 Makefile이 함께 읽습니다. 따라서 모든 줄이 따옴표 없는
평범한 `KEY=value`여야 하고, 주석은 별도의 줄에 써야 합니다 — Make는 그 외의 형식을
해석하지 못하며, 읽지 못하는 줄이 하나라도 있으면 모든 `make` 호출이 실패합니다.
값 뒤에 붙인 주석은 앞의 공백까지 값의 일부로 읽힙니다. 명령줄에서 준 값이 항상
우선합니다(`make run PAGES=50`).

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `PAGES` | 100 | 크롤링할 GEO 페이지 수 |
| `WORKERS` | 1 | 병렬 크롤 워커 수 |
| `SELENIUM_HEADLESS` | true | Chrome headless 모드 |
| `MODE` | complete | 실행 모드: `complete`, `crawl`, `analyze`, `test` |
| `LOG_LEVEL` | INFO | 로그 수준: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MAX_SAMPLES` | 2 | `make fetch-sra`가 내려받을 SRA 수, 또는 `all` |
| `MAX_CONCURRENT` | 4 | `make fetch-sra`의 동시 다운로드 수 |
| `SRA_SOURCE` | stage2 | `make fetch-sra`의 액세션 출처. 필터되지 않은 원시 목록은 `raw-stage1`을 명시할 때만 사용 |
| `CPU_LIMIT` | 4 | Docker CPU 코어 상한 |
| `MEMORY_LIMIT` | 8G | Docker 메모리 상한 |
| `COMPOSE_PROJECT_NAME` | genoar | 이 실행이 속할 Compose 프로젝트. 프로젝트는 자기 컨테이너와 네트워크, **그리고 결과 디렉터리**를 소유합니다. 기본 프로젝트는 체크아웃 루트에 씁니다. 다른 프로젝트 이름은 `projects/<name>/` 아래에 씁니다 |

GEO가 가진 것보다 많은 페이지를 요청해도 오류가 아닙니다. 크롤러가 실제 총
페이지 수를 파악해 범위를 잘라내고, 경고를 남긴 뒤 존재하는 만큼만 크롤링합니다.

`WORKERS`의 기본값은 Makefile과 `docker-compose.yml` 양쪽 모두 `1`입니다. 워커마다
`crawl_output/runs/<run_id>/workers/worker-<n>/` 아래 자기 디렉터리에 기록합니다.
출력 디렉터리, `checkpoint.json`, `crawl_manifest.json`, 브라우저 다운로드 공간을
공유하면 서로의 결과와 완료 증거를 덮어쓰기 때문입니다. 워커가 모두 종료한 뒤
애그리게이터 한 곳만 전체 범위 manifest를 씁니다. 그럼에도
기본값은 워커 하나입니다. 단일 크롤러에는 이 장치가 아예 필요 없고, 파이프라인이
실제로 검증되는 경로도 그쪽이기 때문입니다. `WORKERS`는 의도적으로 올리십시오.
그때 생기는 디렉터리 구조는
[병렬 크롤링](genoar_crawler/README.md#parallel-crawling)에 있습니다.
`.env.example`도 `WORKERS=1`이라 그대로 복사해도 달라지는 것은 없습니다. 거기에 값을
적으면 기본값을 이기고, 명령행은 둘 다 이깁니다.

`COMPOSE_PROJECT_NAME`은 Compose 프로젝트의 이름이며, 그에 따라 이 실행의
컨테이너(`<project>-genoar-<n>`), 네트워크(`<project>_genoar-network`), 결과 위치가
정해집니다. 기본 프로젝트는 체크아웃 루트에 `crawl_output/`, `first_pass_output/`,
`logs/`, `workflow_output/`을 씁니다. 지금까지와 같습니다. 다른 프로젝트는 같은 네
디렉터리를 `projects/<name>/` 아래에 씁니다. 따라서 한 호스트에서 파이프라인 둘을
동시에 돌릴 수 있습니다. 두 실행은 서로의 run 디렉터리, manifest, 분석 테이블을
덮어쓰지 않습니다.

```bash
make run                              # 프로젝트 genoar  -> ./crawl_output/ 등
make run COMPOSE_PROJECT_NAME=second  # 프로젝트 second  -> ./projects/second/
```

**그 디렉터리를 읽는 타깃에도 같은 이름이 필요합니다.** 이름을 주지 않으면 기본
프로젝트의 결과를 이 프로젝트의 것으로 보고합니다.

```bash
make status COMPOSE_PROJECT_NAME=second
make logs   COMPOSE_PROJECT_NAME=second
make clean  COMPOSE_PROJECT_NAME=second
```

**같은 프로젝트 이름으로 두 번째 실행을 시작하는 것은 거부합니다.** 첫 실행의
컨테이너와 출력 트리 전체를 공유하게 되고, Compose가 돌고 있는 컨테이너를 다시
만들기 때문입니다. `make run`은 이를 알리고 0이 아닌 값으로 종료합니다.

**Stage 3은 이 방식으로 분리되지 않습니다.** `make run-stage3`은 프로젝트 이름과
무관하게 체크아웃 루트의 `sample_sra/`를 읽고 `results/`와 `logs/`에 씁니다.
`make fetch-sra`도 같은 `sample_sra/`에 다운로드를 캐시하고 그 아래 숨김 입력 뷰에
최신 선택을 따로 만듭니다. 따라서 Stage 1+2 프로젝트가 몇 개 돌고 있든 Stage 3은
체크아웃당 한 번에 하나씩만 실행됩니다.

`PAGES`와 마찬가지로 `.env`에 적거나 명령줄에 주면 됩니다. Compose도 `.env`를 직접
읽으므로 양쪽 값이 어긋날 일은 없습니다.

---

## 전체 실행

Stage 1 → Stage 2 → Stage 3를 자동으로 연결하여 실행하는 통합 테스트 프레임워크입니다.

### 개요

Cycle Test는 GENOAR의 세 가지 파이프라인을 순차적으로 실행합니다:

```
Stage 1 (Crawler)     →     Stage 2 (Analysis)     →     Stage 3 (SRR Pipeline)
  NCBI GEO 크롤링           UMLS 매칭 분석              SRA 다운로드 + Cell Ranger
  META/SMTX/SRR 수집        CSV 테이블 생성              scRNA-seq 분석
```

---

### 1. 사전 요구사항

#### 시스템 요구사항

| 항목 | 최소 사양 | 권장 사양 |
|------|----------|----------|
| CPU | 8 cores | 16+ cores |
| RAM | 64GB | 128GB+ |
| 디스크 | 100GB | 500GB+ |
| OS | Linux (Ubuntu 20.04+) | Linux (Ubuntu 22.04) |
| 아키텍처 | **Stage 3는 AMD64 필수** (Linux / Windows) | — |

> **플랫폼 안내.** Stage 1(크롤러)과 Stage 2(분석)는 두 아키텍처 모두에서 실행됩니다. `make setup`·`make run`·`docker compose up`이 쓰는 루트 `Dockerfile`은 **amd64에 Google Chrome**을, **arm64에 Chromium과 `chromium-driver`**를 설치하고 크롤러가 설치된 쪽을 보도록 지정합니다. 이 선택은 빌드의 `TARGETARCH`로 이뤄지므로 **BuildKit이 필요합니다.** Docker 23 이상은 기본으로 켜져 있고, 그보다 낮은 버전에서는 직접 켜야(`DOCKER_BUILDKIT=1`) 합니다. 켜지 않으면 스테이지 이름을 해석하는 단계에서 빌드가 실패합니다. 크롤러만 단독으로 돌릴 때 쓰는 `genoar_crawler/Dockerfile.amd64`, `Dockerfile.arm64`는 그대로 남아 있습니다. **Stage 3는 Cell Ranger가 ARM을 지원하지 않아 AMD64 전용**입니다. `--run-stage3`는 Linux 또는 Windows x86_64 서버에서 실행하세요. ARM Mac에서는 Stage 1+2까지의 개발/검증만 가능합니다.

#### 소프트웨어 의존성

```bash
# Docker 설치 확인
docker --version  # Docker 23+ 필요 (위 플랫폼 안내의 BuildKit)

# Python 확인
python3 --version  # Python 3.9+ 필요
```

---

### 2. 환경 구성

#### 2.1 Cell Ranger 설치 (Stage 3 필요시)

```bash
# 1. Cell Ranger 다운로드 (10x Genomics 웹사이트)
# https://www.10xgenomics.com/support/software/cell-ranger/downloads

# 2. 압축 해제
tar -xzvf cellranger-8.0.1.tar.gz

# 3. 프로젝트 루트의 cellranger/로 이동
mv cellranger-8.0.1 cellranger/
```

`make run-stage3`은 `cellranger/`를 `/opt/cellranger`로 마운트하고, cycle_test는
프로젝트 루트의 `cellranger/`를 자동 감지합니다. 다른 위치에 두려면
`--cellranger-path`로 직접 지정하세요.

#### 2.2 참조 게놈 다운로드 (Stage 3 필요시)

```bash
# 1. 10x Genomics 참조 게놈 다운로드
wget https://cf.10xgenomics.com/supp/cell-exp/refdata-gex-GRCh38-2024-A.tar.gz

# 2. 압축 해제 (약 15GB)
tar -xzvf refdata-gex-GRCh38-2024-A.tar.gz

# 3. 프로젝트 루트의 ref/로 이동
mv refdata-gex-GRCh38-2024-A ref/

# 4. 위치 확인
ls ref/
# 출력: fasta/  genes/  reference.json  star/
```

##### 대안: STARsolo

Cell Ranger는 Linux x86_64로만 배포되어, 해당 환경이 없는 그룹에는 진입 장벽이
될 수 있습니다. [STARsolo](https://github.com/alexdobin/STAR/blob/master/docs/STARsolo.md)는
같은 10x 리드에서 비슷한 수준의 count matrix를 만들어내는 대안이며, 빌드 가능한
플랫폼 범위가 더 넓습니다.

GENOAR의 Stage 3은 Cell Ranger를 전제로 구성되어 있고 STARsolo 경로를 제공하지는
않습니다. 따라서 STARsolo를 쓴다면 정량화 단계를 직접 수행하고 그 결과 matrix를
넣어주어야 합니다. Linux x86_64 클러스터를 쓸 수 있는 환경이라면 기본 제공되는
Cell Ranger 경로를 그대로 사용하는 것을 권장합니다.

#### 2.3 UMLS 데이터 준비

Stage 2는 필드별 UMLS(Unified Medical Language System) 테이블 3개로 메타데이터 문자열을 대조합니다. **이 테이블은 레포지토리의 [`all_query_results/`](all_query_results/)에 포함되어 있어** 파이프라인을 그대로 실행할 수 있습니다.

| 파일 | 내용 |
|------|------|
| `umls_celltype_df.csv` | 세포 유형 개념 (CUI, STR, SAB, STY) |
| `umls_tissue_df.csv` | 조직 개념 |
| `umls_disease_df.csv` | 질병 개념 |

각 행은 개념(`CUI`) 하나의 영어 명칭(`STR`) 하나를 출처 어휘(`SAB`)와 의미 유형(`STY`)과 함께 담습니다. 테이블은 [UMLS Metathesaurus](https://www.nlm.nih.gov/research/umls/index.html)(릴리스 2024AB)에서 UTS REST API로 조회해 파이프라인에 맞게 가공한 것으로, Metathesaurus 전체가 아니라 Stage 2가 필요로 하는 세포 유형·조직·질병 개념만 담습니다. UMLS 고지는 [데이터 출처와 고지](#데이터-출처와-고지)를 참고하십시오.

**확장 (선택):** 개념 커버리지를 넓히거나 자체 연구 범위에 맞게 재쿼리하려면 [UTS (UMLS Terminology Services)](https://uts.nlm.nih.gov/uts/)에서 무료 계정을 만든 뒤 [UMLS REST API](https://documentation.uts.nlm.nih.gov/rest/home.html) 또는 MetamorphoSys로 조회하고, `CUI`, `STR`, `SAB`, `STY` 컬럼을 유지한 CSV로 `all_query_results/`의 파일을 교체하십시오. 사용한 UMLS 릴리스를 기록해 두십시오.

```bash
# 포함 여부 확인
ls all_query_results/umls_*_df.csv
# umls_celltype_df.csv  umls_disease_df.csv  umls_tissue_df.csv
```

> `cycle_test/run_cycle_test.py` 실행 시 프로젝트 루트의 `all_query_results/` 디렉토리는 자동으로 감지되어 Stage 2 컨테이너에 마운트됩니다 — 별도 플래그 불필요.

---

### 3. Docker 이미지 빌드

```bash
cd /path/to/genoar

# Stage 1: GEO Crawler
docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/

# Stage 2: UMLS Analysis
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .

# Stage 3: SRR Pipeline (Cell Ranger)
docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .

# 빌드 확인
docker images | grep genoar
# 출력 예시:
# genoar-crawler    amd64     xxx   1.18GB
# genoar-analysis   latest    xxx   514MB
# genoar-srr        step9     xxx   666MB
```

---

### 4. Cycle Test 실행

#### 4.1 기본 실행 (Stage 1 + Stage 2만)

```bash
cd /path/to/genoar

# 기본 테스트: 1 사이클, 5페이지 크롤링
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --output-dir test_output
```

#### 4.2 전체 파이프라인 실행 (Stage 1 → 2 → 3)

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --output-dir test_full_pipeline \
  --run-stage3 \
  --cellranger-cores 8 \
  --cellranger-mem 64
```

#### 4.3 Dry Run (실행 계획만 확인)

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --run-stage3 \
  --dry-run
```

#### 4.4 HPC(Singularity)에서 Stage 3 실행

같은 코드에 결과도 같습니다. 실행 방법만 다릅니다. Docker를 금지하는 HPC에서는
Stage 3(Cell Ranger)를 Singularity/Apptainer로 실행합니다. 추가로 필요한 작업은
Docker 이미지를 `.sif`로 한 번 변환하는 것뿐입니다.

```bash
# 1. Docker 이미지를 .sif로 변환 (최초 1회, Singularity/Apptainer 있는 Linux에서)
srr_pipeline_package/singularity/build_sif.sh --from daemon --image genoar-srr:step9

# 2a. Singularity로 직접 실행 (make run-stage3와 동일 동작)
srr_pipeline_package/singularity/run_singularity_pipeline.sh \
  --sif genoar-srr_step9.sif --sra ./sample_sra/.genoar_selected --config <config.yaml> \
  --ref ./ref --cellranger ./cellranger

# 2b. 또는 cycle_test로, 같은 명령에 runtime만 지정
python cycle_test/run_cycle_test.py --cycles 1 --run-stage3 --runtime singularity --sif-dir /경로/sifs
```

직접 실행 명령은 `make fetch-sra`가 만든 정확한 제한 선택본을 사용합니다. 입력 전체를
직접 준비하고 관리하는 경우에만 `--sra ./sample_sra`를 사용하십시오.

원격 HPC로 보내 실행(업로드, 제출, 상태확인, 회수)하려면 `hpc_config.yaml`을 채우고
핸드오프를 사용합니다.

```bash
# 반자동: 전송/제출/회수 번들 생성
python cycle_test/run_cycle_test.py --cycles 1 --run-stage3 --stage3-hpc hpc_config.yaml
# 완전자동(SSH): SSH나 스케줄러가 안 되면 반자동 번들로 폴백
python cycle_test/run_cycle_test.py --cycles 1 --run-stage3 --stage3-hpc hpc_config.yaml --stage3-hpc-execute
```

이 경로에서 손으로 고치는 파일은 `hpc_config.yaml` 하나뿐입니다. 번들이 싣고 가는
파이프라인 설정은 여기서 생성되므로, 생성된 쪽을 고치면 다음 번들에서 사라집니다.
한 job에 다 담기지 않는 코퍼스는 `plan_batches.py`로 먼저 배치를 나눈 뒤 배열 작업
하나로 제출합니다. [hpc/README.md](srr_pipeline_package/hpc/README.md#running-a-corpus)
를 보십시오.

스케줄러는 `hpc_config.yaml`의 `scheduler` 키가 정합니다. `slurm`, `pbspro`,
`torque` 중 하나를 받고, 키가 없으면 `slurm`입니다. Slurm에서는 `sbatch --test-only`로
먼저 검사한 뒤 `sbatch`로 제출하고, `squeue`로 상태를 확인하며, 종료 상태와 소요 시간과
최대 사용 메모리는 `sacct`에서 읽습니다. PBS에서는 `qsub`으로 제출하고 `qstat`으로 상태를
확인하며, 종료 상태는 `qstat -x -f`에서 읽습니다.

번들 하나에는 실행 ID가 하나씩 붙습니다. 파이프라인은 이미 기록이 있는 실행 ID를
거부합니다. 같은 번들을 두 번 제출하면 두 번째에서 실패합니다. 다시 실행하려면 번들을
새로 생성하세요. 배열 번들에서는 각 태스크가 그 ID 뒤에 배치 번호를 붙여
(`<run id>-b1`, `-b2` …) 자기 ID를 갖습니다. ID를 공유하면 태스크들이 서로의 기록을
거부하게 되어, 배열 전체가 한 번의 실행만 남기고 나머지를 잃습니다.

스테이지끼리는 파일로 연결되므로 런타임을 섞어도 됩니다. 예를 들어 크롤과 분석은
Docker(로컬), Cell Ranger는 Singularity(HPC)로 실행할 수 있습니다. 자세한 내용은
[singularity/](srr_pipeline_package/singularity/README.md),
[hpc/](srr_pipeline_package/hpc/README.md)를 참고하세요.

---

### 5. CLI 옵션

#### 기본 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--cycles N` | 실행할 사이클 수 | (필수) |
| `--pages-per-cycle N` | 사이클당 크롤링할 GEO 페이지 수 | 100 |
| `--start-page N` | 시작 페이지 번호 | 1 |
| `--wait-minutes N` | 사이클 간 대기 시간 (분) | 10 |
| `--output-dir PATH` | 결과 저장 디렉토리 | test_cycles |
| `--dry-run` | 실행 계획만 출력 (실제 실행 안함) | - |
| `--skip-stage1` | Stage 1 건너뛰기 (기존 데이터 사용) | - |
| `--runtime {docker,singularity}` | 스테이지 컨테이너 런타임 (Docker 그대로 / Singularity는 `--sif-dir`의 `.sif` 사용) | docker |
| `--sif-dir PATH` | `--runtime singularity`일 때 `.sif` 이미지가 있는 디렉토리 | `.` |

#### Stage 3 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--run-stage3` | Stage 3 파이프라인 실행 | False |
| `--stage3-only` | Stage 3만 실행 (기존 Stage 2 결과 사용) | False |
| `--download-only` | SRA 다운로드만 (Cell Ranger 스킵) | False |
| `--max-samples N` | 다운로드할 최대 샘플 수 | 전체 |
| `--max-downloads N` | 동시 다운로드 수 | 4 |
| `--cellranger-path` | Cell Ranger 설치 경로 | `./cellranger/` (자동 감지됨) |
| `--ref-genome-path` | 참조 게놈 경로 | `./ref/` (자동 감지됨) |
| `--cellranger-cores N` | Cell Ranger CPU 코어 수 | 16 |
| `--cellranger-mem N` | Cell Ranger 메모리 (GB) | 100 |
| `--stage3-hpc HPC_CONFIG` | Stage 3를 원격 HPC로: 로컬 Cell Ranger 대신 `hpc_config.yaml` 기반 전송/제출/회수 핸드오프 번들 생성 | - |
| `--stage3-hpc-execute` | `--stage3-hpc`와 함께: SSH로 end-to-end 실행(실패 시 번들로 자동 폴백) | False |

> 자동 감지는 프로젝트 루트의 `cellranger/`, `ref/` 디렉토리를 탐색합니다 (위 §2 환경 구성의 경로 규약과 일치). 다른 위치에 설치했다면 해당 플래그로 직접 지정하세요. 감지에 실패하면 dry-run 로그에 `(auto-detect failed — pass --cellranger-path)` 힌트가 출력되므로 실제 실행 전에 확인할 수 있습니다.

---

### 6. 실행 예시

```bash
# 예시 1: Dry run (실행 계획 확인)
python cycle_test/run_cycle_test.py --cycles 1 --pages-per-cycle 5 --run-stage3 --dry-run

# 예시 2: Stage 1 + 2만 실행 (빠른 테스트)
python cycle_test/run_cycle_test.py --cycles 1 --pages-per-cycle 5

# 예시 3: 전체 파이프라인 (소규모 테스트)
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --run-stage3 \
  --max-samples 2

# 예시 4: 기존 Stage 2 결과로 Stage 3만 실행
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --stage3-only \
  --output-dir existing_output

# 예시 5: SRA 다운로드만 (Cell Ranger 없이)
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --run-stage3 \
  --download-only
```

---

### 6.5 예상 소요 시간

| 단계 | 작업 | 일반적인 소요 시간 |
|------|------|-------------------|
| Stage 1 | GEO 크롤링 (5페이지) | 60–90분 |
| Stage 2 | UMLS 매칭 | 1–2분 |
| Stage 3a | SRA 다운로드 (샘플당) | 3–10분 |
| Stage 3b | Cell Ranger (샘플당) | 1–4시간 |

전체 파이프라인 소규모 테스트(2샘플)는 대개 약 3–10시간 소요됩니다. Cell Ranger 단계는 길게 돌기 때문에 30분 이상 "Stage 3에 머물러 있는 것처럼 보이는" 상황은 정상입니다 — 중단하기 전에 `cycle_*/stage3/logs/docker_stdout.log`를 먼저 확인하세요.

---

### 7. 결과 확인

#### 7.1 출력 디렉토리 구조

```
test_full_pipeline/
├── master_YYYYMMDD_HHMMSS.log      # 전체 실행 로그
├── final_report_*.json             # 최종 결과 리포트
│
└── cycle_001_pages_0001-0005/      # 사이클별 디렉토리
    ├── stage1/                     # Stage 1 결과
    │   ├── META/                   # GSE 메타데이터
    │   ├── SMTX/                   # Series Matrix 파일
    │   └── SRR/                    # SRR ID 목록
    │
    ├── stage2/                     # Stage 2 결과
    │   ├── HS_cell_type_1st_pass_meta_table.csv
    │   ├── HS_tissue_1st_pass_meta_table.csv
    │   └── HS_disease_1st_pass_meta_table.csv
    │
    ├── stage3/                     # Stage 3 결과
    │   ├── srr_list.txt            # 추출된 SRR ID 목록
    │   ├── gse_srr_mapping.json    # GSE-SRR 매핑
    │   ├── sra/                    # 다운로드된 SRA 파일
    │   │   └── SRRxxxxxx/SRRxxxxxx.sra
    │   ├── results/                # Cell Ranger 결과
    │   │   └── success/SRRxxxxxx/cellranger_output/
    │   └── logs/                   # 파이프라인 로그
    │
    ├── logs/                       # 스테이지별 로그
    └── summary.json                # 사이클 요약
```

#### 7.2 실행 로그 모니터링

```bash
# 실시간 로그 확인
tail -f test_full_pipeline/master_*.log

# Stage 3 다운로드 진행률 확인
cat test_full_pipeline/cycle_*/stage3/sra/download_progress.json

# Cell Ranger 로그 확인
tail -f test_full_pipeline/cycle_*/stage3/logs/docker_stdout.log
```

#### 7.3 결과 요약 확인

```bash
# 사이클 요약 확인
cat test_full_pipeline/cycle_001_pages_0001-0005/summary.json | python -m json.tool

# 최종 리포트 확인
cat test_full_pipeline/final_report_*.json | python -m json.tool
```

#### 7.4 Stage 2 결과 테이블 확인

```bash
# CSV 테이블 미리보기
head test_full_pipeline/cycle_*/stage2/HS_tissue_1st_pass_meta_table.csv

# 샘플 수 확인
wc -l test_full_pipeline/cycle_*/stage2/HS_*_1st_pass_meta_table.csv
```

#### 7.5 Stage 3 Cell Ranger 결과 확인

```bash
# Cell Ranger 성공 샘플 확인
ls test_full_pipeline/cycle_*/stage3/results/success/

# 유전자 발현 매트릭스 확인
ls test_full_pipeline/cycle_*/stage3/results/success/*/cellranger_output/outs/filtered_feature_bc_matrix/

# 품질 리포트 확인 (웹 브라우저로 열기)
open test_full_pipeline/cycle_*/stage3/results/success/*/cellranger_output/outs/web_summary.html
```

---

### 8. 문제 해결

#### Docker 이미지 관련

```bash
# 이미지 존재 확인
docker images | grep genoar

# 이미지가 없으면 빌드
docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .
docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .
```

#### Stage 2 실패 시

```bash
# UMLS 테이블 확인
ls all_query_results/umls_*_df.csv

# Docker 로그 확인
cat test_full_pipeline/cycle_*/logs/stage2.log
```

#### Stage 3 다운로드 실패 시

```bash
# 실패한 SRR 목록 확인
cat test_full_pipeline/cycle_*/stage3/sra/download_failed.txt

# 네트워크 연결 확인
curl -I https://sra-pub-run-odp.s3.amazonaws.com/sra/SRR000001/SRR000001
```

#### Cell Ranger 실패 시

```bash
# Docker 로그 확인
cat test_full_pipeline/cycle_*/stage3/logs/docker_stderr.log

# Cell Ranger와 참조 게놈 확인 — step 0이 보는 것과 같습니다
ls -l cellranger/cellranger cellranger/bin/cellranger 2>/dev/null
# 둘 중 하나가 존재하고 실행 가능해야 합니다
ls ref/reference.json ; ls -d ref/fasta/ ref/genes/ ref/star/
```

설치가 없거나 불완전하면 step 1 전에 `2`로 종료하며, 문제를 한 줄에 하나씩
나열합니다. 고쳐야 할 오류입니다. 빈 결과가 아닙니다.

R1과 R2가 서로 다른 레인에서 온 샘플은 분석할 수 없습니다. Snakemake가 시작하기 전에
사유와 함께 제외됩니다.
[Cell Ranger 처리 자격](srr_pipeline_package/README.ko.md#cell-ranger-처리-자격)을
참고하세요.

#### Stage 3이 끝났는데 아무것도 분석하지 않은 경우

실행 자체는 유효하고 완주했지만 이번 실행이 처리하기로 한 샘플 중 Cell Ranger까지
도달한 것이 하나도 없으면 Stage 3은 `CELL RANGER PROCESSED 0 SAMPLES`를 출력하고
`3`으로 종료합니다. 이유를 담은 명세는 `results/success/` 아래에 남습니다.

```bash
# 자격 여부와 사유가 모든 샘플에 대해 기록됩니다 (step 8 작성)
cat results/success/cellranger_eligibility.tsv

# 그보다 앞서 step 6·step 7이 걸러낸 샘플
cat results/success/cellranger_ineligible.tsv
```

가장 흔한 사유는 FASTQ가 1개뿐인 샘플입니다. Cell Ranger는 R1/R2 쌍을 요구하므로
다운로드가 아무리 잘 끝나도 단일 FASTQ 샘플은 처리할 수 없습니다. step 7은 안전하게
리네임할 수 없는 FASTQ 배치를 만나면 절반만 바꾸는 대신 거부하고 그 사실을
기록하며, step 8은 R1/R2 쌍이 없는 샘플을 건너뜁니다.

단계별 결과는 `results/reports/`의 센티널 파일로 남습니다. 제 할 일을 했으면
`step<N>.ok`, 실패했으면 `step<N>.err`, 완주했지만 할 일을 하지 못했으면
`step<N>.warn`입니다. `.warn`은 `.ok`가 아니므로, `.ok`를 찾는 도구는 이 경우
아무것도 찾지 못합니다.

#### Stage 3 실행이 "했다"고 주장하는 것

`results/`는 영속 디렉터리이므로 "디스크에 BAM이 있다"는 사실만으로는 어느 실행이
만든 것인지 알 수 없습니다. 그래서 Stage 3의 모든 실행은 step 1이 파일을 옮기기
전에 자기가 처리할 샘플 집합을 고정하고, 오직 그 집합에 대해서만 결과를 보고합니다.

```bash
cat results/runs/LATEST                        # 가장 최근 실행의 id
cat results/runs/<run_id>/expected_samples.tsv # 이번 실행이 맡은 샘플
cat results/runs/<run_id>/outcome.json         # 샘플별로 무엇을 이뤘는지
ls  results/runs/<run_id>/cellranger/          # 이번 실행의 Cell Ranger가 돌린 샘플
```

이번 실행이 만들었거나 채택한 샘플에는 `results/success/<sample>/.genoar_cellranger.json`
영수증이 함께 남아, BAM을 그 입력과 그것을 만든 실행에 묶습니다. 산출물을 이번 실행이
한 일로 세는 것은 Cell Ranger 규칙이 그 샘플의 완료 기록을 남긴 경우뿐입니다.
`results/runs/<run_id>/cellranger/<sample>.json`이 그것이며, Cell Ranger를 실제로
돌린 작업이 자기가 만든 파일을 적어 남깁니다. 샘플을 **fresh**로 만드는 것은 그
기록뿐입니다. 실행 시작보다 나중에 쓰인 BAM이라는 사실은 어떤 입력이 그것을 만들었는지
말해 주지 않습니다. 같은 입력, 같은 파일에 대해 이미 보증하는 영수증이 있고 그
영수증이 검증된 작업을 기록한 산출물은 **cache hit**로 보고합니다. 영수증에
`"provenance": "fresh"`가 적히는 것은 그것을 쓴 실행이 직접 Cell Ranger를 돌린
경우뿐입니다. 진짜 재개이며, 완료로는 세되 새로 한 일로는 세지 않습니다. 채택을
기록한 영수증은 다시 **adopted**로 보고합니다. 둘 중 어느 것도 기록하지 않은
영수증은 이번 실행이 판단에 쓸 수 없으므로 그 샘플은 **unverified**입니다. 완료
기록도 영수증도 없는 산출물 역시 **unverified**로 보고하고 완료로 **세지 않습니다.**
의도적으로 인정하려면 `GENOAR_ADOPT_PRIOR_RESULTS=1`로 다시 실행하십시오.
채택(adopted)으로 기록됩니다.

**채택은 실행을 완료시키지 않습니다.** 채택된 산출물은 운영자의 지시로 받아들인
것이며, 이번 실행의 입력과 묶어 주는 근거가 없습니다. 배너는 이를 별도의 줄
`adopted, NOT verified and NOT counted as complete: N`으로 출력합니다. 검증된 완료와
exit 0 판정에서 모두 제외되므로, 완료라고 할 것이 채택뿐인 실행은 `4`로 종료합니다.

다른 실행에 속한 샘플은 목록에만 올리고 건드리지 않으며, 이번 실행의 성과로 세지
않습니다.

---

## 실행 id와 실행 기록

병렬 크롤과 Stage 3 실행은 각각 하나의 run id 아래에서 일어나고, 그 id 아래에 자기
기록을 남깁니다. 크롤은 `crawl_output/runs/<id>/`, Stage 3는 `results/runs/<id>/`
입니다. 그냥 두면 실행마다 타임스탬프가 붙은 id를 스스로 만듭니다. 직접 이름을
붙이려면 `GENOAR_RUN_ID`를 쓰십시오.

```bash
cd genoar_crawler
GENOAR_RUN_ID=pilot-2026-08 ./run_parallel_crawl.sh 4 40
```

id는 디렉터리 이름이 되므로 디렉터리 이름으로 쓸 수 있어야 합니다.
`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`입니다. 첫 글자는 영문자나 숫자, 그다음은 영문자,
숫자, `.`, `_`, `-`이며 최대 64자입니다. 앞뒤 공백은 잘라내며, 설정하지 않은 경우와
빈 값, 공백뿐인 값은 모두 "id를 주지 않았다"는 뜻이라 새로 하나를 만듭니다.
id *안쪽*의 공백은 거부합니다. 조용히 다른 id로 바꾸지 않습니다. `LATEST`는 예약어입니다.
Stage 3가 `runs/LATEST`를 가장 최근 실행을 가리키는 포인터로 쓰기 때문입니다. 비교는
대소문자를 구분하므로 `latest`는 그대로 쓸 수 있습니다. 이 규칙을 어기면 아무것도
만들기 전에 `2`로 종료합니다.

**실행 기록은 절대 덮어쓰지 않습니다.** `crawl_output/runs/<id>/run.json`이 이미 있는
id는 다시 쓸 수 없습니다. 그 파일은 그 실행이 무엇을 하겠다고 했는지를 적은 것이고
애그리게이터가 실행을 판정하는 기준이므로, 런처는 `2`로 종료하고 기록을 그대로 둡니다.
Stage 3도 `results/runs/<id>/`가 이미 있는 id를 같은 이유로 같은 방식으로 거부합니다.

**id 선점은 원자적이며, 파이프라인의 양쪽 절반이 모두 그렇습니다.** 각각 run
디렉터리를 단일 `mkdir` 호출로 확보합니다. 커널이 이 호출을 직렬화합니다. 같은 id로
두 실행을 같은 순간에 시작하면 하나가 진행하고, 다른 하나는 `2`로 종료합니다.

`1`은 별개의 대답입니다. run 디렉터리를 아예 만들지 못했다는 뜻입니다. 읽기 전용
트리, 디스크 가득 참, 사라진 마운트가 여기에 해당합니다. id가 선점됐다는 뜻은 아닙니다.
양쪽 절반 모두 둘 중 어느 쪽인지 밝힙니다. 문제가 아니었던 run id를 고치러 보내면
탐색 전체를 낭비하게 됩니다.

id를 다시 쓰는 방법은 하나뿐이며, `run_parallel_crawl.sh`에만 있습니다.

```bash
GENOAR_RUN_ID=pilot-2026-08 GENOAR_RESUME=1 ./run_parallel_crawl.sh 4 40
```

그러면 각 워커가 자기 체크포인트에서 이어갑니다. 원래 `run.json`은 쓰인 그대로
남습니다. 재개가 그 실행이 한 약속을 다시 정의해서는 안 되기 때문입니다. 이어간
기록은 `runs/<id>/resumes.jsonl`에 한 줄씩 덧붙습니다.

**재개는 자신이 들어가는 계획에 매입니다.** 워커 수나 요청 끝 페이지가 다르면 두 값을
모두 밝히며 `2`로 종료합니다. 페이지 분배는 이 두 값으로 계산하고, 그 분배가 체크포인트를
페이지 범위에 묶습니다. 다른 값으로 재개하면 워커 2가 51-100 페이지용으로 쓴 체크포인트를
열어 21-40 페이지를 이어가게 되고, 실행의 manifest는 아무도 크롤링하지 않은 페이지를
자기 것이라고 주장하게 됩니다. GEO를 다시 조회하지 않는 이유도 같습니다. 오늘 더 큰
corpus가 나오면 같은 경계가 움직이므로, 계획 자신의 분배가 기준으로 섭니다. 각 워커가
기록한 `assignment.json`도 다시 계산한 구간과 대조합니다. 워커를 자기가 시작하지 않은
페이지로 옮기게 되는 재개도 거부합니다.

이 기록에는 한계가 있습니다. 실행 단위로는 최초 실행과 이후 재개를 구분할 수
있습니다. 다만 재개한 워커는 `workers/worker-<n>/` 아래 자기
`crawl_manifest.json`과 `exit_code`를 덮어씁니다. 따라서 어느 재개가 그 워커의
구간을 만들었는지는 남지 않습니다. 남는 manifest는 그 워커가 배정받은 구간 전체를
다룹니다. `completed_pages`는 배정받은 구간 중 끝난 만큼이며, 재개 지점 앞의
페이지는 이번 호출이 이어받은 체크포인트를 쓴 호출들이 크롤링한 것입니다. 마지막
호출이 실제로 걸은 페이지는 `crawled_pages`에 적히고, 완료 판정은 여기서 하지
않습니다. `run_crawl.sh`와 `run_docker_parallel.sh`는 재개 자체가 불가능하며, 새
id를 쓰는 것이 유일한 길입니다.

그 두 런처는 컨테이너를 띄우므로, 컨테이너가 아직 호스트에 남아 있는 id도 거부합니다.
돌고 있는 컨테이너든, 기록을 남기지 않은 실행이 남긴 멈춘 컨테이너든 마찬가지입니다.
후자의 경우 `GENOAR_RECLAIM_RUN=1`이 남은 컨테이너를 정리해 그 id를 다시 쓸 수 있게
합니다. 지우는 것은 컨테이너뿐이고 실행 기록은 절대 건드리지 않으며, 그 실행 자신의
라벨이 붙은 컨테이너만 대상으로 합니다.

---

## 실행 결과 읽기

실행의 결과는 둘이 아니라 셋입니다. 일을 했거나, 올바르게 아무것도 하지 않았거나,
실패했거나입니다. GENOAR는 이 셋을 모든 지점에서 구분합니다. GEO에 더 이상 없는
페이지 범위를 크롤링한 경우나, Cell Ranger 자격을 갖춘 샘플이 하나도 없던 Stage 3
실행이 성공으로 보고되는 일은 없습니다.

`0`, `1`, `2`, `3`은 어디서나 같은 뜻입니다.

| 코드 | 의미 |
|------|------|
| `0` | 실제로 일을 했음 |
| `1` | 실패 |
| `2` | 잘못된 인자 또는 잘못된 페이지 범위 |
| `3` | 유효하게 완주했으나 할 일이 없었음 |
| `130` | 중단됨 (Ctrl+C) |

`4`의 의미와 `2`의 쓰임은 구성요소마다 다르며, `make` 타깃은 그중 무엇도 그대로
전달하지 않습니다. 진입점별로 실제로 반환하는 값은 다음과 같습니다.

| 진입점 | 반환하는 코드 |
|--------|---------------|
| `genoar_crawler.py` (단일 크롤러 또는 워커 하나) | `0` `1` `2` `3` `130` |
| `run_parallel_crawl.sh`, `run_docker_parallel.sh`, `run_crawl.sh` | `0` `1` `2` `3` `4`. `2`는 쓸 수 없는 run id, 이미 쓴 run id, 지금 다른 실행이 쥐고 있는 run id를 포함합니다. `1`은 run 디렉터리를 아예 만들지 못한 경우도 포함합니다. `4`는 모든 워커가 정상 종료했는데 *이번 실행이* 수집한 파일이 0건 |
| 파이프라인 컨테이너 (`docker compose up`, `genoar:latest`) | `0` `1` `2` `3`. 병렬 크롤의 `4`는 "결과가 나오지 않음"으로 보아 `3`으로 기록 |
| Stage 3 (`genoar-srr:step9`) | `0` `1` `2` `3` `4`. `2`는 설치 구성 오류 또는 쓸 수 없는 run id입니다. `1`은 run 디렉터리를 아예 만들지 못한 경우도 포함합니다. `4`는 부분 완료입니다. 기대 샘플 전부에 대해 검증된 산출물을 보이지 못하고, 이번 실행이 설명할 수 있는 산출물을 가진 기대 샘플이 있는 경우로, 그 산출물은 검증된 것이거나 채택된 것입니다 |
| `run_cycle_test.py` | `0` 모든 사이클 성공, `130` 중단, `1` 그 외 |
| `make run`, `make run-stage3`, `make run-full` | **성공 `0`, 실패는 0이 아닌 값. 정확한 코드는 그대로 전달하지 않음** |

`make run`은 Compose를 `--abort-on-container-exit --exit-code-from genoar`로
실행하므로 컨테이너가 실패하면 명령도 실패합니다. 다만 GNU Make는 실패한 recipe의
상태를 자기 것으로 바꾸므로, `make`는
성공/실패 구분까지만 보장합니다. 컨테이너의 실제 코드가 사라지지는 않습니다.
`make run`이 멈추기 전에 `the genoar container exited <n>`을 출력하고, Make 자신의
`*** [...] Error <n>` 줄에도 같은 숫자가 실립니다. **CI나 스케줄러에서 정확한
코드가 필요하다면 `make`를 거치지 마십시오. 스크립트나 컨테이너를 직접
호출하십시오.**

"할 일이 없었음"(`3`)의 의미는 스테이지마다 다릅니다.

- **크롤러.** GEO가 실제로 가진 페이지로 범위를 자르고 나니 요청 범위에 남은
  페이지가 없었습니다. 시작 페이지나 워커 수를 낮추세요.
- **병렬 크롤.** 어떤 워커도 처리할 페이지를 받지 못했습니다. 종료 코드 `4`는 더
  강한 경고입니다. 워커는 정상적으로 돌았는데 이번 실행이 아무것도 수집하지 못했다면
  원인은 그 위쪽에 있습니다. 요약은 두 숫자를 함께 출력합니다. 이번 실행이 수집한
  파일 수와, 출력 디렉터리에 있는 전체 실행의 파일 수입니다. 종료 코드를 결정하는
  것은 앞의 숫자뿐입니다.
- **Stage 3.** 이번 실행이 처리하기로 한 샘플 중 Cell Ranger까지 도달한 것이
  없습니다. `cellranger/`나 `ref/`가 없거나 쓸 수 없는 상태인 경우는 여기에 **해당하지
  않습니다.** 그것은 구성 오류이며, 어떤 단계도 시작하기 전에 `2`로 종료합니다.

`cycle_test`는 같은 구분을 종료 코드가 아니라 사이클 단위로 보고합니다. 사이클
상태는 `success`, `no_data`, `partial_success`, `failed_stage1`, `failed_stage3`,
`interrupted` 중 하나이며, `no_data` 사이클에는 Stage 1이 페이지를 못 찾은
것인지 Stage 3이 자격 샘플을 못 찾은 것인지 밝히는 `no_data_reason`이 함께
붙습니다. Stage 1은 요청 범위가 GEO의 실제 분량으로 잘린 경우 `capped`를 따로
기록합니다. 이는 존재하는 페이지를 모두 크롤링한 완주이므로 사이클은 성공으로
집계됩니다. 기대 샘플 중 일부만 분석된 Stage 3은 `partial_success`입니다. 설치가
쓸 수 없어 아예 돌지 못한 Stage 3은 `failed_stage3`입니다. `no_data`가 아닙니다.

`no_data`가 `1`인 것은 의도된 것입니다. `0`이 되는 일은 없습니다. 자동화가 요청한 일이
일어나지 않았고, 예약 실행이 빈 사이클을 계속 찍어내는 것을 눈치채지 못하면
안 되기 때문입니다.

---

---

## 테스트

```bash
pip install -r requirements.txt
pytest                    # 아래 전부
pytest cycle_test         # 파이프라인: 설정, 핸드오프, 종료코드, 출처 기록
pytest genoar_crawler     # 실행 격리, 페이지네이션, 재개, 완료 주장
```

`pytest.ini`가 루트 실행 범위를 `requirements.txt`가 덮는 트리로 한정합니다.
`genoar_rag/backend`는 자기 의존성을 따로 선언하므로 그 디렉터리에서 실행합니다.

목 테스트가 아닙니다. `cycle_test/tests/conftest.py`가 `ssh`, `scp`, `rsync`,
`sbatch`, `squeue`, `sacct`, `qsub`, `qstat`의 실행 가능한 대역을 `PATH`에 얹고,
가짜 `ssh`는 래퍼가 만든 명령 문자열을 실제로 실행합니다. 명령이나 스케줄러 출력
형식이 바뀌면 테스트가 깨집니다. 대부분은 코드를 import해 우회하지 않고 출하되는 셸
스크립트를 `subprocess`로 직접 돌립니다.

7개는 Docker와 Stage 3 이미지가 필요해 없으면 이유를 밝히고 skip합니다. 그 밖에
네트워크나 클러스터, 갖고 있지 않은 데이터를 요구하는 테스트는 없습니다.

## 프로젝트 구조

```
genoar/
├── genoar_crawler/              # Stage 1: 데이터 수집
│   ├── genoar_crawler.py        # Selenium 기반 크롤러
│   ├── integration.py           # 데이터 검증 및 리포트
│   ├── Dockerfile.amd64         # x86_64 Docker (Intel/AMD)
│   ├── Dockerfile.arm64         # ARM64 Docker (Apple Silicon)
│   └── README.md                # Stage 1 상세 문서
│
├── genoar_analysis/             # Stage 2: UMLS 매칭 분석
│   ├── core/data.py             # GenoarData 클래스
│   ├── io/                      # META/UMLS 파일 로드
│   ├── preprocessing/           # 필터링, 필드 통합
│   ├── pipelines/               # 분석 워크플로우
│   ├── docker/Dockerfile        # Stage 2 Docker
│   └── README.md                # Stage 2 상세 문서
│
├── srr_pipeline_package/        # Stage 3: Cell Ranger 처리
│   ├── pipeline_next/           # 9단계 스크립트 (기본값)
│   ├── pipeline/                # 비교 기준으로 남겨둔 이전 트리
│   │                            #   (설정의 legacy_pipeline: true)
│   ├── docker/Dockerfile        # 멀티스테이지 Docker
│   ├── configs/example.yaml     # 파이프라인 설정
│   └── README.md                # Stage 3 상세 문서
│
├── cycle_test/                  # 통합 테스트 프레임워크
│   ├── run_cycle_test.py        # 테스트 실행기
│   ├── utils/                   # 스테이지별 러너
│   └── README.md                # Cycle Test 사용 가이드
│
├── scripts/
│   └── fetch_sra.py             # Stage 2 → Stage 3 브리지 (make fetch-sra)
│
├── crawl_output/                # Stage 1 결과물
├── all_query_results/           # UMLS 테이블 (Stage 2 입력)
├── first_pass_output/           # Stage 2 결과물
└── README.md                    # 이 문서
```

---

## 각 파이프라인 상세 문서

각 파이프라인의 상세 설명, 실행 방법, 문제 해결 가이드는 해당 폴더의 README.md를 참조하세요:

- **[Stage 1: GEO Crawler](genoar_crawler/README.md)** - NCBI GEO 데이터 수집
- **[Stage 2: UMLS Analysis](genoar_analysis/README.md)** - UMLS 매칭 및 주석
- **[Stage 3: SRR Pipeline](srr_pipeline_package/README.md)** - Cell Ranger 처리
- **[테스트 시나리오 가이드](docs/SCENARIO_GUIDE.ko.md)** - 스테이지 조합별 실행 시나리오 A~E
- **[Cycle Test](cycle_test/README.ko.md)** - 전체 파이프라인 통합 실행 도구
- **[RAG 웹 서비스](genoar_rag/README.ko.md)** - 수집된 메타데이터 검색 웹 애플리케이션 (FastAPI + Next.js)

---

## 인용

GENOAR는 *Nucleic Acids Research*에 심사 중인 논문에 기술되어 있습니다.

> Paik H, Ko TL, Kim D, Shin D, Sirota M, Oskotsky B, Oskotsky T, Lee T, Lee H, Lee D. GENOAR: Global Engine for Navigating the Omnicell space via Agent-based Research. *Nucleic Acids Research*, submitted (2026).

같은 내용이 [`CITATION.cff`](CITATION.cff)에 기계 판독 형식으로 들어 있으며, GitHub의 "Cite this repository"가 이 파일을 읽습니다. Zenodo DOI가 붙은 태그 릴리스가 뒤따를 예정이고, DOI는 여기와 파일에 추가됩니다.

---

## 라이선스

GENOAR는 [MIT 라이선스](LICENSE)로 배포됩니다.

---

## 데이터 출처와 고지

- **샘플 메타데이터**는 NCBI GEO와 SRA에서 가져옵니다. GENOAR는 이를 색인하고 아카이브의 원본 레코드 페이지로 연결할 뿐, 기탁된 데이터를 재배포하지 않습니다.
- **개념 주석**(Stage 2가 붙이는 `CUI`·매칭 용어 컬럼)은 미국 국립의학도서관(NLM)의 Unified Medical Language System(UMLS) Metathesaurus 릴리스 2024AB를 기준으로 만듭니다([2.3 UMLS 데이터 준비](#23-umls-데이터-준비)). 테이블을 재생성했다면 사용한 릴리스를 기록해 두십시오. UMLS 라이선스는 NLM을 출처로 밝히고 릴리스를 함께 인용하도록 요구합니다.

  Some material in the UMLS Metathesaurus is from copyrighted sources of the respective copyright holders. Users of the UMLS Metathesaurus are solely responsible for compliance with any copyright, patent or trademark restrictions and are referred to the copyright, patent or trademark notices appearing in the original sources, all of which are hereby incorporated by reference.

  주석이 어느 출처 어휘에서 왔는지는 테이블(`SAB` 컬럼)과 `genoar_analysis/io/umls_readers.py`의 필드별 우선순위가 정합니다. 주석을 공개 서비스로 제공한다면 그 어휘 목록과 각 제공처가 요구하는 고지문을 함께 표시하십시오.
- **Cell Ranger**와 10x Genomics 참조 게놈은 사용자가 10x Genomics 약관에 따라 직접 내려받으며, 여기서 재배포하지 않습니다.

---

## 요구사항

| 구성요소 | Python | 메모리 | 디스크 | 추가 요구사항 |
|----------|--------|--------|--------|---------------|
| Crawler | 3.9+ | 4GB+ | 10-100MB/GSE | Chrome, 인터넷 |
| Analysis | 3.9+ | 4GB+ | 수 MB | UMLS 테이블 (포함) |
| SRR Pipeline | 3.11+ | 128GB+ | 수십 GB/샘플 | Docker, Cell Ranger |
