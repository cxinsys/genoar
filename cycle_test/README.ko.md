# GENOAR Cycle Test

[English](README.md)

Stage 1 → Stage 2 → Stage 3를 자동으로 연결하여 실행하는 통합 테스트 프레임워크입니다.

## 개요

Cycle Test는 GENOAR의 세 가지 파이프라인을 순차적으로 실행합니다:

```
Stage 1 (Crawler)     →     Stage 2 (Analysis)     →     Stage 3 (SRR Pipeline)
  NCBI GEO 크롤링           UMLS 매칭 분석              SRA 다운로드 + Cell Ranger
  META/SMTX/SRR 수집        CSV 테이블 생성              scRNA-seq 분석
```

---

## 1. 사전 요구사항

### 지원 범위

- **아카이브:** **NCBI SRA**의 `SRR`로 시작하는 런만 처리합니다. ENA(`ERR`), DDBJ(`DRR`) 런은 Stage 2 → Stage 3 핸드오프와 다운로더에서 **필터링되어 제외**됩니다.
- **종 / assay:** Stage 2는 Homo sapiens + TRANSCRIPTOMIC 샘플만 유지합니다.
- **Stage 3 chemistry:** 10x Genomics single-cell RNA-seq (Cell Ranger).

### 시스템 요구사항

| 항목 | 최소 사양 | 권장 사양 |
|------|----------|----------|
| CPU | 8 cores | 16+ cores |
| RAM | 64GB | 128GB+ |
| 디스크 | 100GB | 500GB+ |
| OS | Linux (Ubuntu 20.04+) | Linux (Ubuntu 22.04) |
| 아키텍처 | **Stage 3는 AMD64 필수** | Linux / Windows x86_64 |

> **플랫폼 안내.** Stage 1, Stage 2는 아키텍처에 무관하게 실행됩니다 (Apple Silicon ARM64 Mac 포함 — 크롤러는 `Dockerfile.arm64` 사용). **Stage 3는 Cell Ranger가 ARM을 지원하지 않아 AMD64 전용**입니다. `--run-stage3`는 Linux 또는 Windows x86_64 서버에서 실행하세요. ARM Mac에서도 Stage 1+2 경로는 로컬 검증이 가능합니다.

### Docker 호스트 요구사항 (Stage 1)

크롤러 컨테이너는 headless Chrome을 구동하므로 호스트에서 아래 권한을 요구합니다.

- `--cap-add=SYS_ADMIN`
- `--security-opt seccomp=unconfined`
- 컨테이너 내부에서 `--user root`

이 플래그들은 `stage1_runner.py`가 자동으로 부여합니다. **해당 플래그를 허용하지 않는 보안 강화 호스트에서는 Stage 1이 실행되지 않습니다** — 정책을 완화하거나 허용되는 머신에서 크롤러를 실행하세요.

### 소프트웨어 의존성

```bash
# Docker 설치 확인
docker --version  # Docker 20.10+ 필요

# Python 확인
python3 --version  # Python 3.9+ 필요
```

---

## 2. 환경 구성

### 2.1 Cell Ranger 설치 (Stage 3 필요시)

```bash
# 1. Cell Ranger 다운로드 (10x Genomics 웹사이트)
# https://www.10xgenomics.com/support/software/cell-ranger/downloads

# 2. 압축 해제
tar -xzvf cellranger-8.0.1.tar.gz

# 3. 프로젝트 루트의 cellranger/로 이동 (자동 감지 경로)
mv cellranger-8.0.1 cellranger/
```

### 2.2 참조 게놈 다운로드 (Stage 3 필요시)

```bash
# 1. 10x Genomics 참조 게놈 다운로드
wget https://cf.10xgenomics.com/supp/cell-exp/refdata-gex-GRCh38-2024-A.tar.gz

# 2. 압축 해제 (약 15GB)
tar -xzvf refdata-gex-GRCh38-2024-A.tar.gz

# 3. 프로젝트 루트의 ref/로 이동 (자동 감지 경로)
mv refdata-gex-GRCh38-2024-A ref/

# 4. 위치 확인
ls ref/
# 출력: fasta/  genes/  reference.json  star/
```

### 2.3 UMLS 데이터 준비

**포함되어 있습니다.** UMLS 테이블 3개는 레포지토리의 `<genoar>/all_query_results/`에 들어 있어 그대로 실행할 수 있습니다.

```bash
ls all_query_results/umls_*_df.csv
# umls_celltype_df.csv  umls_disease_df.csv  umls_tissue_df.csv
```

개념 집합을 확장하거나 재쿼리하려면 메인 [README.ko.md](../README.ko.md#23-umls-데이터-준비)를 참고하세요.

`run_cycle_test.py`가 프로젝트 루트의 이 디렉토리를 자동 감지하여 Stage 2 컨테이너에 마운트합니다 — 별도 플래그 불필요.

---

## 3. Docker 이미지 빌드

```bash
cd /path/to/genoar

# Stage 1: GEO Crawler
#   AMD64 (Linux / Windows / Intel Mac):
docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/
#   ARM64 (Apple Silicon Mac):
# docker build -t genoar-crawler:arm64 -f genoar_crawler/Dockerfile.arm64 genoar_crawler/

# Stage 2: UMLS Analysis (아키텍처 무관)
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .

# Stage 3: SRR Pipeline (Cell Ranger — AMD64 전용, --run-stage3 사용 시 필수)
docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .

# 빌드 확인
docker images | grep genoar
# 출력 예시:
# genoar-crawler    amd64     xxx   1.18GB
# genoar-analysis   latest    xxx   514MB
# genoar-srr        step9     xxx   666MB
```

---

## 4. Cycle Test 실행

모든 예제는 `--output-dir test_full_pipeline`로 통일합니다. `--output-dir`를 생략하면 스크립트 기본값은 `test_cycles`입니다.

### 4.1 기본 실행 (Stage 1 + Stage 2만)

```bash
cd /path/to/genoar

# 기본 테스트: 1 사이클, 5페이지 크롤링
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --output-dir test_full_pipeline
```

### 4.2 전체 파이프라인 실행 (Stage 1 → 2 → 3)

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --output-dir test_full_pipeline \
  --run-stage3 \
  --cellranger-cores 8 \
  --cellranger-mem 64
```

### 4.3 Dry Run (실행 계획만 확인)

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --output-dir test_full_pipeline \
  --run-stage3 \
  --dry-run
```

> Dry-run은 `--output-dir`를 **생성하지 않고** master 로그 파일도 남기지 않습니다 — 계획만 콘솔로 출력되어 흔적 없이 반복 실행할 수 있습니다.

### 4.4 `--skip-stage1`로 기존 Stage 1 결과 재사용

`--skip-stage1`은 `<output-dir>/cycle_NNN_pages_*/stage1/META/*.txt`가 이미 존재해야 합니다. 스크립트는 META 파일이 비어 있으면 **즉시 에러**로 중단하므로, 반드시 이전에 Stage 1을 완료한 `--output-dir`를 지정하세요.

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline \
  --skip-stage1
```

---

## 5. CLI 옵션

### 기본 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--cycles N` | 실행할 사이클 수 | (필수) |
| `--pages-per-cycle N` | 사이클당 크롤링할 GEO 페이지 수 | 100 |
| `--start-page N` | 시작 페이지 번호 | 1 |
| `--wait-minutes N` | 사이클 간 대기 시간 (분) | 10 |
| `--output-dir PATH` | 결과 저장 디렉토리 | test_cycles |
| `--dry-run` | 실행 계획만 출력 (output-dir / master 로그 생성 안함) | - |
| `--skip-stage1` | Stage 1 건너뛰기 — `--output-dir`에 기존 `stage1/META` 필요 | - |
| `--stage2-timeout SECS` | Stage 2 컨테이너 타임아웃 (초) | 1800 |
| `--runtime {docker,singularity}` | 스테이지 컨테이너 런타임 (Docker 그대로 / Singularity는 `--sif-dir`의 `.sif`) | docker |
| `--sif-dir PATH` | `--runtime singularity`일 때 `.sif` 이미지 디렉토리 | `.` |

### Stage 3 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--run-stage3` | Stage 3 파이프라인 실행 | False |
| `--stage3-only` | Stage 3만 실행 (기존 Stage 2 결과 사용) | False |
| `--download-only` | SRA 다운로드만 (Cell Ranger 스킵) | False |
| `--max-samples N` | 다운로드할 최대 샘플 수 | 전체 |
| `--max-downloads N` | 동시 다운로드 수 | 4 |
| `--cellranger-path` | Cell Ranger 설치 경로 | (자동 감지) |
| `--ref-genome-path` | 참조 게놈 경로 | (자동 감지) |
| `--cellranger-cores N` | Cell Ranger CPU 코어 수 | 16 |
| `--cellranger-mem N` | Cell Ranger 메모리 (GB) | 100 |
| `--stage3-hpc HPC_CONFIG` | Stage 3를 원격 HPC로: `hpc_config.yaml` 기반 전송·제출·회수 번들 생성 | - |
| `--stage3-hpc-execute` | `--stage3-hpc`와 함께 SSH로 end-to-end 실행 (실패 시 번들 폴백) | False |

> GEO가 가진 것보다 많은 페이지를 요청하는 것은 오류가 아닙니다. 크롤러가 범위를 잘라내고 경고를 남긴 뒤 존재하는 만큼 크롤링합니다(Stage 1 상태 `capped`). `--start-page`가 끝을 넘어서면 크롤링할 것이 아예 없어 사이클이 `no_data`로 끝납니다 — [7.2 사이클 상태와 종료 코드](#72-사이클-상태와-종료-코드) 참고.
> `make fetch-sra`는 cycle_test 바깥의 별도 경로입니다. `make run`이 만든 필터 적용 Stage 2 테이블을 읽고 `sample_sra/`에 다운로드를 캐시한 뒤 `make run-fetched-stage3`용 정확한 입력 뷰를 만듭니다. 여기서 설명하는 `stage3/sra/` 다운로드와는 무관합니다. 직접 관리한 `sample_sra/` 입력은 기존처럼 `make run-stage3`으로 처리합니다.
> Singularity를 쓰는 HPC Stage 3 실행: [../srr_pipeline_package/hpc/README.md](../srr_pipeline_package/hpc/README.md), [../srr_pipeline_package/singularity/README.md](../srr_pipeline_package/singularity/README.md) 참고.

---

## 6. 실행 예시

```bash
# 예시 1: Dry run (실행 계획 확인)
python cycle_test/run_cycle_test.py --cycles 1 --pages-per-cycle 5 --run-stage3 --dry-run

# 예시 2: Stage 1 + 2만 실행 (빠른 테스트)
python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline

# 예시 3: 전체 파이프라인 (소규모 테스트)
python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline \
  --run-stage3 --max-samples 2

# 예시 4: 기존 Stage 2 결과로 Stage 3만 실행 (해당 output-dir에 cycle_*/stage2 결과가 이미 있어야 함)
python cycle_test/run_cycle_test.py \
  --cycles 1 --stage3-only \
  --output-dir test_full_pipeline

# 예시 5: SRA 다운로드만 (Cell Ranger 없이)
python cycle_test/run_cycle_test.py \
  --cycles 1 --run-stage3 --download-only \
  --output-dir test_full_pipeline
```

---

## 7. 결과 확인

### 7.1 출력 디렉토리 구조

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

### 7.2 사이클 상태와 종료 코드

실행의 결말은 둘이 아니라 셋입니다. 일을 했거나, 올바르게 아무것도 하지 않았거나, 실패했거나입니다. `summary.json`과 `final_report_*.json`은 사이클마다 `status`를 기록합니다.

| 상태 | 의미 |
|------|------|
| `success` | 요청한 모든 스테이지가 제 할 일을 마침 |
| `partial_success` | 일부 샘플이나 테이블이 빠진 채로 완료. Stage 3에서는 이번 실행이 맡은 샘플 중 일부만 **검증된** Cell Ranger 산출물을 가진 경우. `partial_reason`이 어느 쪽인지 밝힘 |
| `no_data` | 유효하게 완주했으나 할 일이 없었음. `no_data_reason`이 사유를 밝힘 |
| `failed_stage1` | 크롤링 실패 |
| `failed_stage3` | SRA 다운로드 또는 Cell Ranger 실패, Cell Ranger 설치가 쓸 수 없는 상태, 또는 실행이 자기 기록을 남기지 않은 경우. `failed_reason`이 어느 쪽인지 밝힘 |
| `interrupted` | 사이클 도중 Ctrl+C가 도달함 |

사유를 담는 필드는 셋이고, 사유가 있는 상태마다 하나씩 대응합니다. `no_data_reason`은 아무 일도 일어나지 않은 갈래를 구분합니다. 요청 범위에 페이지가 없었거나, Stage 2 테이블에 SRR 액세션이 없었거나, Cell Ranger 자격을 갖춘 샘플이 없었거나입니다. `partial_reason`은 기대한 작업 중 어디까지가 남았는지 말합니다. `failed_reason`은 실패 내용을 그대로 싣습니다. 셋 다 `summary.json`에 있습니다. `final_report_*.json`에는 `no_data_reason`만 다시 실리고, Stage 3 결과 전체는 `stage3_pipeline` 또는 `stage3_handoff` 아래에 들어갑니다.

Cell Ranger 설치가 없거나 불완전한 경우는 `no_data`가 아닙니다. 실행기는 `cellranger/cellranger`가 실행 가능한지, reference에 `reference.json`과 `fasta/`, `genes/`, `star/`가 있는지 확인합니다. 파이프라인이 step 0에서 하는 것과 같은 검사입니다. 여기서 걸리면 고쳐야 할 구성 오류이므로 사이클은 `failed_stage3`이 됩니다.

#### Stage 3의 결과가 사이클 상태를 정합니다

Stage 3는 자기 실행을 스스로 채점하고 그 등급을 `stages.stage3_pipeline.status`에 남깁니다. 각 등급은 그 실행에 대해 참인 사이클 상태를 받습니다.

| Stage 3 결과 | 사이클 상태 | 의미 |
|--------------|-------------|------|
| `success` | `success` | 기대한 샘플 전부가 검증된 Cell Ranger 산출물을 가짐 |
| `partial_success` | `partial_success` | 기대한 샘플 중 일부는 분석되었고 일부는 아님. 양쪽 다 수치로 확인됨 |
| `nothing_processed` | `no_data` | 유효하게 완주했고 아무것도 분석하지 않음 |
| `config_error` | `failed_stage3` | Cell Ranger나 reference가 쓸 수 없는 상태라 애초에 분석할 수 없었음 |
| `failed` | `failed_stage3` | 실행이 깨졌거나, 집계가 서로 어긋나거나, 자기 기록 없이 돌아옴 |

표에 없는 등급은 `failed_stage3`입니다. `success` 등급을 내세우면서 동시에 성공을 부정하는 결과도 마찬가지입니다. 둘 다 참일 수는 없기 때문입니다.

쓸 만한 자기 기록 없이 돌아온 Stage 3 실행은 `partial_success`가 아니라 `failed_stage3`입니다. 사이클이 가리킬 수 있는 작업을 아무것도 하지 않았으므로, `partial_success`로 잡으면 증거가 뒷받침하지 않는 작업을 인정해주는 셈입니다. 성공도 보고하지 않고 부분 작업도 주장하지 않는 Stage 3 실행 역시 같습니다.

FASTQ 변환 실패도 Stage 3가 판정합니다. 전체적으로는 성공했는데 `failed_samples`가 0보다 큰 실행은, 성공한 샘플이 있으면 `partial_success`, 하나도 없으면 `failed_stage3`입니다. FASTQ가 되지 못한 샘플은 산출물을 내지 못했으므로 계산에 들어갑니다.

#### 더 나쁜 상태가 이깁니다

Stage 3의 판정은 사이클을 더 나쁜 상태로 올릴 수 있습니다. 앞선 스테이지가 이미 정한 상태를 낮추지는 않습니다. "Stage 3가 올바르게 아무것도 하지 않았다"가 "Stage 2가 깨졌다"를 덮어쓸 수 없다는 뜻입니다. 좋은 쪽에서 나쁜 쪽으로 늘어놓은 순서는 다음과 같습니다.

```
running < success < no_data < partial_success < failed_stage3 < failed_stage1 < interrupted
```

이 규칙으로 판정이 거부되면 마스터 로그가 그렇게 적습니다. `Cycle stays <status>: an earlier stage already reported a problem`.

#### HPC에서 도는 Stage 3

`--stage3-hpc`를 쓰면 Stage 3는 로컬 파이프라인 실행이 아니라 `run_hpc_stage3.py`의 종료 코드로 채점되고, `stages.stage3_handoff`에 실행기의 결과가 그대로 실립니다. `--stage3-hpc-execute`까지 붙이면 `0`은 `success`, `3`은 `no_data`, `4`는 `partial_success`, `1`과 `2`는 `failed_stage3`입니다. 붙이지 않으면 사이클이 요청한 것은 분석이 아니라 전송용 번들이므로, 번들을 쓴 것 자체가 `success`이고 분석은 그 뒤 클러스터에서 일어납니다. 번들을 쓰지 못하면 `failed_stage3`이고 `failed_reason`이 사유를 밝힙니다. 어느 쪽이든 분석이 무엇을 이뤘는지는 `stages.stage3_handoff.analysis_status`가 보고하며, 작업이 하나도 돌지 않았으면 `not_attempted`입니다. [../srr_pipeline_package/hpc/README.md](../srr_pipeline_package/hpc/README.md#what-the-runner-reports) 참고.

Stage 1은 자체 결과를 `stages.stage1.status`에 남깁니다.

| 상태 | 의미 |
|------|------|
| `success` | 요청 범위를 전부 크롤링 |
| `capped` | GEO가 실제로 가진 페이지로 범위가 잘렸고, 그 범위는 전부 크롤링 |
| `no_pages_in_range` | 요청 범위에 페이지가 없었음. 사이클은 `no_data`가 됨 |
| `interrupted` | Ctrl+C |
| `failed` | 그 외 |

`capped`는 `success` 사이클로 집계됩니다. 존재하는 페이지를 전부 크롤링한 완주이기 때문입니다. 실제로 다룬 범위는 `stages.stage1.capped_pages`에 기록됩니다.

프로세스 종료 코드는 자동화를 위해 이보다 거칩니다.

| 종료 코드 | 의미 |
|-----------|------|
| `0` | 모든 사이클이 성공 |
| `130` | 중단됨. 사이클 사이에서 중단된 경우도 포함 |
| `1` | 그 외 전부 |

`no_data`가 `1`인 것은 의도된 것입니다. 요청한 일이 일어나지 않았고, 예약 실행이 빈 사이클을 계속 찍어내는 것을 눈치채지 못하면 안 되기 때문입니다. 둘 중 어느 쪽이었는지는 종료 코드가 아니라 상태에서 읽습니다.

### 7.3 실행 로그 모니터링

Stage 1, 2, **Stage 3** 모두 Docker 출력이 실시간으로 master 로그에 라인 단위로 흘러갑니다 — Cell Ranger가 끝날 때까지 기다리지 않아도 진행 상황을 바로 볼 수 있습니다.

```bash
# 실시간 master 로그 (모든 stage 포함, Cell Ranger 진행까지)
tail -f test_full_pipeline/master_*.log

# Stage 3 Docker 출력이 영구 저장되는 파일
tail -f test_full_pipeline/cycle_*/stage3/logs/docker_stdout.log

# Stage 3 다운로드 진행률
cat test_full_pipeline/cycle_*/stage3/sra/download_progress.json
```

### 7.4 결과 요약 확인

```bash
# 사이클 요약 확인
cat test_full_pipeline/cycle_001_pages_0001-0005/summary.json | python -m json.tool

# 최종 리포트 확인
cat test_full_pipeline/final_report_*.json | python -m json.tool
```

### 7.5 Stage 2 결과 테이블 확인

```bash
# CSV 테이블 미리보기
head test_full_pipeline/cycle_*/stage2/HS_tissue_1st_pass_meta_table.csv

# 샘플 수 확인
wc -l test_full_pipeline/cycle_*/stage2/HS_*_1st_pass_meta_table.csv
```

### 7.6 Stage 3 Cell Ranger 결과 확인

```bash
# Cell Ranger 성공 샘플 확인
ls test_full_pipeline/cycle_*/stage3/results/success/

# 유전자 발현 매트릭스 확인
ls test_full_pipeline/cycle_*/stage3/results/success/*/cellranger_output/outs/filtered_feature_bc_matrix/

# 품질 리포트 확인 (웹 브라우저로 열기)
open test_full_pipeline/cycle_*/stage3/results/success/*/cellranger_output/outs/web_summary.html
```

---

## 8. 예상 소요 시간

| 단계 | 작업 내용 | 예상 시간 |
|------|----------|----------|
| Stage 1 | GEO 크롤링 (5페이지) | 60-90분 |
| Stage 2 | UMLS 분석 | 1-2분 |
| Stage 3a | SRA 다운로드 (샘플당) | 3-10분 |
| Stage 3b | Cell Ranger (샘플당) | 1-4시간 |

**전체 테스트 (2샘플 기준):** 약 3-10시간

---

## 9. 문제 해결

로그보다 종료 코드와 사이클 `status`를 먼저 보십시오. 진단할 것이 있는지부터 알려줍니다 — [7.2 사이클 상태와 종료 코드](#72-사이클-상태와-종료-코드) 참고.

### Docker 이미지 관련

```bash
# 이미지 존재 확인
docker images | grep genoar

# 이미지가 없으면 빌드
docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .
docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .
```

### Stage 2 실패 시

```bash
# UMLS 테이블 확인
ls all_query_results/umls_*_df.csv

# Docker 로그 확인
cat test_full_pipeline/cycle_*/logs/stage2.log
```

### Stage 3 다운로드 실패 시

```bash
# 실패한 SRR 목록 확인
cat test_full_pipeline/cycle_*/stage3/sra/download_failed.txt

# 네트워크 연결 확인
curl -I https://sra-pub-run-odp.s3.amazonaws.com/sra/SRR000001/SRR000001
```

### Cell Ranger 실패 시

```bash
# Docker 로그 확인
cat test_full_pipeline/cycle_*/stage3/logs/docker_stderr.log

# Cell Ranger 경로 확인
ls -la cellranger/cellranger

# 참조 게놈 확인
ls ref/fasta/ ref/genes/ ref/star/
```

### 자동 감지 실패

dry-run 로그에 `(auto-detect failed — pass --cellranger-path)`가 뜨면 둘 중 하나입니다.
- 기대 경로(`./cellranger/`, `./ref/`)에 설치하거나
- 실행할 때마다 `--cellranger-path` / `--ref-genome-path`를 직접 넘기거나.

### 사이클은 끝났는데 처리된 것이 없음

실패가 아니라 `no_data` 상태입니다. `no_data_reason`부터 확인하십시오.

```bash
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('status'), '-', d.get('no_data_reason'))" \
  test_full_pipeline/cycle_*/summary.json
```

`partial_success` 사이클은 같은 자리에 `partial_reason`을, `failed_stage3` 사이클은 `failed_reason`을 싣습니다.

Cell Ranger 쪽이라면 Stage 3이 `3`으로 종료하고 결과 옆에 명세를 남깁니다.

```bash
cat test_full_pipeline/cycle_*/stage3/results/success/cellranger_eligibility.tsv
cat test_full_pipeline/cycle_*/stage3/results/success/cellranger_ineligible.tsv
```

가장 흔한 사유는 FASTQ가 1개뿐인 샘플입니다. Cell Ranger는 R1/R2 쌍을 요구하므로 이런 샘플은 처리될 수 없습니다.

Stage 3은 결과 볼륨에 무엇이 있든 상관없이, *이번 사이클이* 넘긴 샘플만을 기준으로 자신을 판정합니다. 그 샘플이 무엇이었고 각각 어떻게 됐는지는 run 기록에 있습니다.

```bash
cat test_full_pipeline/cycle_*/stage3/results/runs/<run_id>/outcome.json
```

이전 실행에서 남은 샘플은 그 안의 `other_samples_with_output`에 나타납니다. 보존되며 이번 사이클의 성과로 세지 않습니다. `outcome.json`을 남기지 않은 Stage 3 실행은 실패로 보고합니다. 그 실행은 자기가 무엇을 처리했는지 아무 말도 하지 않았고, 결과 볼륨은 지워지지 않으므로 거기 놓인 산출물은 이번 실행이 무엇을 했든 이전 실행들이 남긴 것입니다. 기록 없는 실행이 나오는 경우는 둘이며, 보고는 둘 다 밝힙니다. Stage 3 이미지가 run 기록보다 오래된 경우로, 이때는 `make build-srr`로 다시 빌드한 뒤 실행하십시오. 또는 실행이 기록을 쓰기 전에 끝난 경우로, 이때는 이번 실행의 logs 디렉터리 아래 `docker_stdout.log`에 담긴 컨테이너 출력이 어디까지 갔는지 알려 줍니다.

요약이 세는 것은 검증된 산출물뿐입니다. `GENOAR_ADOPT_PRIOR_RESULTS=1`로 채택한 샘플은 `Adopted, unverified: N (not counted as completed)` 줄로 따로 보고하며 `cellranger_adopted_samples`에 나열합니다. 어떤 사이클 판정에도 포함되지 않습니다. Stage 3의 완료라고 할 것이 채택뿐인 사이클은 분석한 것이 없으며, 그렇게 보고합니다.

---

## 10. 디렉토리 구조

```
cycle_test/
├── run_cycle_test.py           # 메인 테스트 실행기
├── utils/
│   ├── stage1_runner.py        # Stage 1 Docker 실행
│   ├── stage2_runner.py        # Stage 2 Docker 실행
│   ├── stage3_preparer.py      # Stage 3 SRR 추출
│   ├── stage3_downloader.py    # SRA 다운로드
│   ├── stage3_runner.py        # Stage 3 Docker 실행
│   └── report_generator.py     # summary.json / final_report_*.json / 콘솔 요약
├── README.md                   # 영문판
├── README.ko.md                # 이 문서
├── TUTORIAL.md                 # 첫 실행 안내 (영문)
└── TUTORIAL.ko.md              # 첫 실행 안내 (한국어)
```
