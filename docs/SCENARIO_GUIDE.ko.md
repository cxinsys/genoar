# GENOAR 파이프라인 테스트 시나리오 가이드

GENOAR 파이프라인의 모든 스테이지 조합을 검증하는 5개 테스트 시나리오에 대한 단계별 안내서입니다.

[English](SCENARIO_GUIDE.md)

> **`make` 경로가 아닙니다.** 아래 시나리오는 스테이지를 이어 돌리고 결과를
> 검사하는 하네스인 `cycle_test/run_cycle_test.py`를 씁니다. README의
> `make setup` / `make run`은 파이프라인을 **쓰는** 지원 경로이고, 이 문서는
> 파이프라인을 **시험하는** 경로입니다. 둘은 이미지를 공유하지 않습니다:
>
> | | `make` | 이 문서 |
> |---|---|---|
> | Stage 1+2 | `make setup`이 빌드하고 Compose로 실행하는 `genoar:latest` | 아래에서 직접 빌드하는 `genoar-crawler:amd64`, `genoar-analysis:latest` |
> | Stage 3 | `make build-srr`가 빌드하는 `genoar-srr:step9` | 같은 이미지, 같은 빌드 명령 |
>
> 즉 `make setup`은 크롤러·분석 시나리오가 필요로 하는 이미지를 하나도 만들지
> 않습니다. 아래 세 개를 먼저 빌드하세요. 공유되는 것은 Stage 3뿐이고,
> `make build-srr`과 2단계의 명령은 같은 결과물을 만듭니다.

---

## 시나리오 개요

| 시나리오 | 스테이지 | 크롤링 페이지 | 목적 | 예상 소요 시간 |
|---------|---------|-------------|------|--------------|
| **A** | 1 → 2 | 5 | 크롤링 + 분석 동작 확인 | 7~8시간 (아래 참고) |
| **B** | 1 → 2 | 100 | 대규모 데이터 수집 + 분석 | 수일 |
| **C** | 3만 | — | 기존 분석 결과로 SRA 다운로드 + Cell Ranger 실행 | 수시간 |
| **D** | 1 → 2 → 3 | 5 | 전체 파이프라인 소규모 통합 테스트 | 수시간 |
| **E** | 1 → 2 → 3 | 100 | 전체 파이프라인 대규모 실행 | 수일 |

> **크롤링 페이지 크기 안내.** 크롤러는 페이지당 500건을 요청하고 한 건씩 순차
> 처리합니다. 따라서 "5페이지"는 수십 건이 아니라 최대 2,500건입니다. 실측
> 처리 속도는 건당 약 11초입니다.
>
> Stage 1에는 2시간 타임아웃이 하드코딩되어 있고(`stage1_runner.py`) CLI로
> 노출되지 않습니다. 그래서 5페이지 크롤링은 완료 전에 타임아웃에 걸리며, 실행은
> Stage 1 실패로 종료됩니다. 이때 Stage 2는 실행되지 않습니다. 범위를 완주하지
> 못한 크롤링은 분석의 근거가 될 수 없으므로, 부분 수집분을 분석하는 대신
> 파이프라인이 멈춥니다.
>
> 페이지 크기를 설정할 수 있게 되기 전까지 시나리오 A의 완주는 기대하지 마십시오.
> Stage 1이 멈추기 전까지 모아둔 데이터로 Stage 2를 확인하려면 META 디렉터리를
> 직접 지정해 따로 실행하십시오.
>
> ```bash
> python3 cycle_test/utils/stage2_runner.py \
>   <출력디렉터리>/cycle_001_pages_0001-0005/stage1/META \
>   all_query_results \
>   <출력디렉터리>/cycle_001_pages_0001-0005/stage2
> ```
>
> 해당 사이클의 `stage2/`에 쓰면 시나리오 C가 그대로 이어집니다. `--stage3-only`가
> 그 위치를 입력으로 읽기 때문입니다. 또한 두 질문이 분리됩니다. 크롤링이 요청
> 범위를 완주했는가, 그리고 수집된 데이터로 분석이 동작하는가.

```
시나리오 A/B:  [Stage 1: 크롤러] → [Stage 2: UMLS 분석]
시나리오 C:                                              [Stage 3: Cell Ranger]
시나리오 D/E:  [Stage 1: 크롤러] → [Stage 2: UMLS 분석] → [Stage 3: Cell Ranger]
```

---

## 사전 준비 (최초 1회)

### 1. 시스템 요구사항

| 항목 | 최소 사양 | 권장 사양 |
|------|----------|----------|
| CPU | 8코어 | 16코어 이상 |
| RAM | 8GB (A/B), 64GB (C/D/E) | 128GB 이상 |
| 디스크 | 10GB (A/B), 100GB 이상 (C/D/E) | 500GB 이상 |
| OS | Linux (Ubuntu 20.04 이상) | Ubuntu 22.04 |
| Docker | 20.10 이상 | 최신 버전 |
| Python | 3.9 이상 | 3.11 |

### 2. Docker 이미지 빌드

모든 시나리오는 Docker 컨테이너를 사용합니다. 프로젝트 루트에서 필요한 이미지를 빌드합니다:

```bash
cd /path/to/genoar

# Stage 1: 크롤러 (시나리오 A, B, D, E에 필요)
docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/

# Stage 2: 분석 (모든 시나리오에 필요)
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .

# Stage 3: SRR 파이프라인 (시나리오 C, D, E에 필요)
docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .
```

빌드 확인:

```bash
docker images | grep genoar   # Windows PowerShell: docker images | Select-String genoar
# 예상 출력:
#   genoar-crawler    amd64     ...   ~1.2GB
#   genoar-analysis   latest    ...   ~500MB
#   genoar-srr        step9     ...   ~700MB
```

### 3. UMLS 데이터 확인 (Stage 2 입력)

UMLS 테이블은 레포지토리에 포함되어 있으며 모든 시나리오에 필요합니다:

```bash
ls all_query_results/umls_*_df.csv
# 예상 출력: umls_celltype_df.csv  umls_disease_df.csv  umls_tissue_df.csv
```

### 4. Stage 3 외부 의존성 (시나리오 C, D, E만 해당)

Stage 3는 레포지토리에 포함할 수 없는 두 가지 대용량 외부 컴포넌트가 필요합니다:

#### Cell Ranger

[10x Genomics](https://www.10xgenomics.com/support/software/cell-ranger/downloads)에서 다운로드 (가입 필요):

```bash
tar -xzvf cellranger-8.0.1.tar.gz
mv cellranger-8.0.1 cellranger/
```

설치 확인:
```bash
ls cellranger/cellranger
```

#### 참조 게놈

```bash
wget https://cf.10xgenomics.com/supp/cell-exp/refdata-gex-GRCh38-2024-A.tar.gz
tar -xzvf refdata-gex-GRCh38-2024-A.tar.gz
mv refdata-gex-GRCh38-2024-A ref/
```

설치 확인:
```bash
ls ref/fasta/ ref/genes/
```

프로젝트 루트의 `cellranger/`와 `ref/`는 `make run-stage3`이 마운트하고
`run_cycle_test.py`가 자동 감지하는 경로입니다. 다른 곳에 설치하면 실행할 때마다
`--cellranger-path`와 `--ref-genome-path`를 넘겨야 합니다.

> **참고**: 참조 게놈은 압축 상태 약 20GB, 압축 해제 시 50GB 이상입니다. 다운로드 전 디스크 공간을 확인하세요.

---

## 시나리오 A: Stage 1→2 빠른 테스트

**목적**: 크롤러가 GEO 데이터를 수집하고, 분석 파이프라인이 UMLS 어노테이션 테이블을 생성하는지 확인합니다. Stage 1과 2의 동작을 가장 빠르게 확인하는 방법입니다.

**필요 Docker 이미지**: `genoar-crawler:amd64`, `genoar-analysis:latest`

**예상 소요 시간**: 5페이지 전체는 7~8시간이 걸리며 Stage 1의 2시간 타임아웃을
넘깁니다. 개요의 페이지 크기 안내를 참고하십시오.

### Dry Run (선택사항)

실제 실행 없이 계획만 확인합니다:

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --output-dir test_scenario_A \
  --dry-run
```

### 실행

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --output-dir test_scenario_A
```

### 결과 확인

현재 페이지 크기에서는 이 실행이 Stage 1 타임아웃에서 멈춥니다. 따라서
`summary.json`의 상태는 `failed_stage1`이고 `stage2/`는 비어 있습니다. 이는 별개의
고장이 아니라 예상된 결과입니다. 범위를 완주하지 못한 크롤링은 분석하지 않습니다.

```bash
# 사이클 요약 확인. 페이지 크기를 설정할 수 있게 되기 전까지 failed_stage1이 정상입니다.
cat test_scenario_A/cycle_001_pages_0001-0005/summary.json | python3 -m json.tool

# Stage 1 결과 (멈추기 전까지 수집된 메타데이터 파일)
ls test_scenario_A/cycle_001_pages_0001-0005/stage1/META/
```

수집된 데이터로 분석이 동작하는지 확인하려면 Stage 2를 따로 실행하십시오.

해당 사이클의 `stage2/`에 씁니다. 시나리오 C가 이 위치를 입력으로 읽습니다.

```bash
python3 cycle_test/utils/stage2_runner.py \
  test_scenario_A/cycle_001_pages_0001-0005/stage1/META \
  all_query_results \
  test_scenario_A/cycle_001_pages_0001-0005/stage2

ls test_scenario_A/cycle_001_pages_0001-0005/stage2/HS_*_1st_pass_meta_table.csv
head test_scenario_A/cycle_001_pages_0001-0005/stage2/HS_tissue_1st_pass_meta_table.csv
```

### 예상 출력 구조

```
test_scenario_A/
├── master_YYYYMMDD_HHMMSS.log
└── cycle_001_pages_0001-0005/
    ├── stage1/
    │   ├── META/*.txt          # GSE 메타데이터 파일
    │   ├── SMTX/*.gz           # Series Matrix 파일
    │   └── SRR/*.txt           # SRR ID 목록
    ├── stage2/                   # Stage 1이 타임아웃으로 멈추면 비어 있음
    │   ├── HS_cell_type_1st_pass_meta_table.csv
    │   ├── HS_tissue_1st_pass_meta_table.csv
    │   └── HS_disease_1st_pass_meta_table.csv
    ├── logs/
    │   ├── stage1.log
    │   └── stage2.log            # Stage 2가 실행된 경우에만 생성
    └── summary.json
```

---

## 시나리오 B: Stage 1→2 대규모 실행

**목적**: 100페이지 규모로 데이터를 수집하여 파이프라인의 안정성을 확인하고, 후속 분석에 사용할 수 있는 풍부한 데이터셋을 생성합니다.

**필요 Docker 이미지**: `genoar-crawler:amd64`, `genoar-analysis:latest`

**예상 소요 시간**: 수일. 페이지당 500건에 건당 약 11초이므로 100페이지는
Stage 1의 2시간 타임아웃을 크게 넘습니다. 개요의 페이지 크기 안내를 참고하십시오.

### 실행

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 100 \
  --output-dir test_scenario_B
```

### 진행 상황 모니터링

```bash
# 실시간 로그 확인
tail -f test_scenario_B/master_*.log
```

### 결과 확인

시나리오 A와 마찬가지로 Stage 1 타임아웃에서 멈추므로 사이클은 `failed_stage1`이고
`stage2/`는 비어 있습니다.

```bash
# 사이클 요약 확인. 현재 페이지 크기에서는 failed_stage1이 정상입니다.
cat test_scenario_B/cycle_001_pages_0001-0100/summary.json | python3 -m json.tool

# 수집된 데이터셋 수 확인
ls test_scenario_B/cycle_001_pages_0001-0100/stage1/META/ | wc -l
```

그 다음 시나리오 A와 같은 방식으로 수집된 데이터에 분석을 돌립니다.

```bash
python3 cycle_test/utils/stage2_runner.py \
  test_scenario_B/cycle_001_pages_0001-0100/stage1/META \
  all_query_results \
  test_scenario_B/cycle_001_pages_0001-0100/stage2

wc -l test_scenario_B/cycle_001_pages_0001-0100/stage2/HS_*_1st_pass_meta_table.csv
```

---

## 시나리오 C: Stage 3만 실행

**목적**: 이전에 완료된 Stage 2의 결과에서 SRR ID를 추출하여 SRA 다운로드 및 Cell Ranger 처리를 테스트합니다.

**필요 Docker 이미지**: `genoar-analysis:latest`, `genoar-srr:step9`

**전제조건**: 내용이 있는 Stage 2 테이블이 있어야 합니다. 현재 페이지 크기에서는
시나리오 A와 B가 완주하지 못하므로, 시나리오 A에 나온 stage2_runner 단독 실행으로
테이블을 만든 뒤 그 디렉터리를 지정하십시오.

**예상 소요 시간**: SRA 다운로드(수분~수시간) + Cell Ranger(샘플당 수시간)

### 중요 사항

- `--stage3-only`는 이전 실행의 출력 디렉토리를 재사용합니다.
- `--pages-per-cycle`과 `--start-page`이 이전 실행과 동일해야 올바른 사이클 디렉토리를 찾을 수 있습니다.
- `--max-samples`로 다운로드할 SRA 파일 수를 제한하는 것을 권장합니다 (초기 테스트 시).

### 대안: `make fetch-sra`

cycle_test 바깥에도 `.sra` 파일을 얻는 경로가 하나 더 있습니다. `make fetch-sra`는
`make run`이 `first_pass_output/`에 남긴 품질 필터 적용 Stage 2 테이블에서 중복 없는
액세션 목록을 뽑습니다. 다운로드는 `sample_sra/`에 캐시하고 현재 선택만 담은 입력
뷰를 만들므로, `make run-fetched-stage3`에는 이전 캐시가 섞이지 않습니다.

```bash
make fetch-sra DRY_RUN=1          # 받을 목록만 출력하고 중단
make fetch-sra MAX_SAMPLES=3      # 3건 다운로드 (기본값 2)
make run-fetched-stage3           # 바로 그 3건만 처리
```

두 경로는 서로 대체되지 않습니다. cycle_test는 `<output-dir>/cycle_XXX/stage2`를
읽어 `<cycle>/stage3/sra/`에 쓰며 기본 상한이 없습니다. Make 경로는 cycle_test가
아니라 `make run`이 채우는 `first_pass_output/`을 읽어 `sample_sra/`에 캐시하고, 별도
지정이 없으면 2건에서 멈춥니다. Stage 1+2를 어느 쪽으로 돌렸는지에 맞춰 고르십시오.

### 실행 (시나리오 A의 결과를 재사용하는 경우)

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --stage3-only \
  --max-samples 3 \
  --cellranger-cores 8 \
  --cellranger-mem 64 \
  --output-dir test_scenario_A
```

`--cellranger-path`와 `--ref-genome-path`는 Cell Ranger와 참조 게놈이
`cellranger/`, `ref/`가 아닌 곳에 있을 때만 필요합니다.

### 다운로드만 실행 (Cell Ranger 제외)

Cell Ranger 없이 SRA 다운로드 단계만 테스트하려면:

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --stage3-only \
  --download-only \
  --max-samples 3 \
  --output-dir test_scenario_A
```

### 결과 확인

```bash
# 추출된 SRR ID 확인
cat test_scenario_A/cycle_001_pages_0001-0005/stage3/srr_list.txt

# 다운로드된 SRA 파일 확인
ls test_scenario_A/cycle_001_pages_0001-0005/stage3/sra/

# Cell Ranger 결과 확인 (--download-only가 아닌 경우)
ls test_scenario_A/cycle_001_pages_0001-0005/stage3/results/success/
```

---

## 시나리오 D: 소규모 전체 파이프라인 (1→2→3)

**목적**: 5페이지만 크롤링하여 전체 파이프라인을 처음부터 끝까지 실행합니다. 크롤링 → 분석 테이블 생성 → SRA 다운로드 → Cell Ranger 처리까지 전체 흐름을 확인합니다.

**필요 Docker 이미지**: `genoar-crawler:amd64`, `genoar-analysis:latest`, `genoar-srr:step9`

**예상 소요 시간**: 현재 페이지 크기에서는 Stage 1+2가 완주하지 못하므로(시나리오 A 참고)
Stage 3까지 도달하지 않습니다. Stage 3를 확인하려면 기존 Stage 2 결과로 시나리오 C를
사용하십시오.

### 실행

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --run-stage3 \
  --max-samples 2 \
  --cellranger-cores 8 \
  --cellranger-mem 64 \
  --output-dir test_scenario_D
```

### 진행 상황 모니터링

```bash
# 전체 로그 (모든 스테이지)
tail -f test_scenario_D/master_*.log

# Stage 3 Docker 파이프라인 로그
tail -f test_scenario_D/cycle_001_pages_0001-0005/stage3/logs/docker_stdout.log
```

### 결과 확인

```bash
# 전체 요약
cat test_scenario_D/cycle_001_pages_0001-0005/summary.json | python -m json.tool

# Stage 1 결과
ls test_scenario_D/cycle_001_pages_0001-0005/stage1/META/

# Stage 2 결과
ls test_scenario_D/cycle_001_pages_0001-0005/stage2/HS_*_1st_pass_meta_table.csv

# Stage 3 결과
ls test_scenario_D/cycle_001_pages_0001-0005/stage3/results/success/
```

---

## 시나리오 E: 대규모 전체 파이프라인 (1→2→3)

**목적**: 실제 운영 규모로 실행합니다. 100페이지를 크롤링하고, 포괄적인 분석 테이블을 생성하며, 발견된 모든 SRR 샘플을 Cell Ranger로 처리합니다.

**필요 Docker 이미지**: `genoar-crawler:amd64`, `genoar-analysis:latest`, `genoar-srr:step9`

**예상 소요 시간**: 수시간~수일 (발견되는 SRR 샘플 수에 비례)

### 실행

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 100 \
  --run-stage3 \
  --cellranger-cores 16 \
  --cellranger-mem 100 \
  --output-dir test_scenario_E
```

### 진행 상황 모니터링

```bash
# 전체 로그
tail -f test_scenario_E/master_*.log

# Stage 3 다운로드 진행률 확인
cat test_scenario_E/cycle_001_pages_0001-0100/stage3/sra/download_progress.json | python -m json.tool

# Cell Ranger 로그
tail -f test_scenario_E/cycle_001_pages_0001-0100/stage3/logs/docker_stdout.log
```

### 결과 확인

```bash
# 최종 리포트
cat test_scenario_E/final_report_*.json | python -m json.tool

# Cell Ranger 성공 샘플
ls test_scenario_E/cycle_001_pages_0001-0100/stage3/results/success/

# 유전자 발현 매트릭스
ls test_scenario_E/cycle_001_pages_0001-0100/stage3/results/success/*/cellranger_output/outs/filtered_feature_bc_matrix/
```

---

## 실행 결과 읽기

모든 시나리오의 결말은 둘이 아니라 셋입니다. 일을 했거나, 올바르게 아무것도 하지
않았거나, 실패했거나입니다. `cat summary.json`만으로는 이 셋이 구분되지 않습니다.

### 종료 코드

```bash
python3 cycle_test/run_cycle_test.py --cycles 1 --pages-per-cycle 5; echo "exit=$?"
```

| 종료 코드 | 의미 |
|-----------|------|
| `0` | 모든 사이클이 성공 |
| `130` | 실행이 중단됨(Ctrl+C). 사이클 사이에서 중단된 경우도 포함 |
| `1` | 그 외 전부. 올바르게 할 일이 없었던 사이클도 여기에 포함 |

`no_data`가 `1`인 것은 의도된 것입니다. 자동화가 요청한 일이 일어나지 않았고,
예약 실행이 빈 사이클을 계속 찍어내는 것을 눈치채지 못하면 안 되기 때문입니다.
둘 중 어느 쪽이었는지는 종료 코드가 아니라 사이클 상태에서 읽습니다.

이 세 코드는 `run_cycle_test.py`만의 것입니다. 크롤러, 병렬 크롤 스크립트, 파이프라인
컨테이너, Stage 3은 각자 더 세밀한 코드를 씁니다. `make` 타깃은 더 거칩니다. 진입점별
표는 루트 README의
[실행 결과 읽기](../README.ko.md#실행-결과-읽기)에 있습니다.

### 사이클 상태

`summary.json`과 `final_report_*.json`은 사이클마다 상태를 기록합니다.

| 상태 | 의미 |
|------|------|
| `success` | 요청한 모든 스테이지가 제 할 일을 마침 |
| `partial_success` | 일부 샘플이나 테이블이 빠진 채로 완료. Stage 3에서는 이번 실행이 맡은 샘플 중 일부만 검증된 Cell Ranger 산출물을 가진 경우. `partial_reason`이 어느 쪽인지 밝힘 |
| `no_data` | 유효하게 완주했으나 할 일이 없었음. `no_data_reason`이 사유를 밝힘 |
| `failed_stage1` | 크롤링 실패 |
| `failed_stage3` | SRA 다운로드 또는 Cell Ranger 실패, Cell Ranger 설치가 쓸 수 없는 상태, 또는 실행이 자기 기록을 남기지 않은 경우. `failed_reason`이 어느 쪽인지 밝힘 |
| `interrupted` | 사이클 도중 Ctrl+C로 중단 |

사유를 담는 필드는 셋이고, 사유가 있는 상태마다 하나씩 대응합니다. `no_data_reason`은
아무 일도 일어나지 않은 갈래를 구분합니다. GEO가 실제로 가진 분량으로 범위를 자르고
나니 요청 범위에 페이지가 남지 않았거나, Cell Ranger 자격을 갖춘 샘플이 없었거나입니다.
`partial_reason`은 기대한 작업 중 어디까지가 남았는지 말합니다. `failed_reason`은 실패
내용을 그대로 싣습니다. 셋 다 `summary.json`에 있습니다. `final_report_*.json`에는
`no_data_reason`만 다시 실리고, Stage 3 결과 전체는 `stage3_pipeline` 또는
`stage3_handoff` 아래에 들어갑니다. 콘솔에도 상태와 함께 출력됩니다.

Cell Ranger 설치가 없거나 불완전한 경우는 `no_data`가 아닙니다. 실행이 우회할 수 없는
구성 오류이므로 `failed_stage3`이 됩니다.

### Stage 3 결과와 사이클 상태

Stage 3는 자기 실행을 스스로 채점하고 그 등급을 `stage3_pipeline.status`에 남깁니다.
각 등급은 그 실행에 대해 참인 사이클 상태를 받습니다.

| Stage 3 결과 | 사이클 상태 | 의미 |
|--------------|-------------|------|
| `success` | `success` | 기대한 샘플 전부가 검증된 Cell Ranger 산출물을 가짐 |
| `partial_success` | `partial_success` | 기대한 샘플 중 일부는 분석되었고 일부는 아님 |
| `nothing_processed` | `no_data` | 유효하게 완주했고 아무것도 분석하지 않음 |
| `config_error` | `failed_stage3` | Cell Ranger나 reference가 쓸 수 없는 상태라 애초에 분석할 수 없었음 |
| `failed` | `failed_stage3` | 실행이 깨졌거나, 집계가 서로 어긋나거나, 자기 기록 없이 돌아옴 |

표에 없는 등급은 `failed_stage3`입니다. `success` 등급을 내세우면서 동시에 성공을
부정하는 결과도 마찬가지입니다.

기록을 남기지 않은 실행은 아무것도 증명하지 못하므로 `partial_success`가 될 수
없습니다. 증거가 뒷받침하지 않는 작업을 인정해주는 셈이기 때문입니다. 그 경우는
`failed_stage3`이고, 성공도 보고하지 않고 부분 작업도 주장하지 않는 Stage 3 실행
역시 같습니다.

Stage 3의 판정은 사이클을 더 나쁜 상태로 올릴 수 있습니다. 앞선 스테이지가 이미 정한
상태를 낮추지는 않습니다. "Stage 3가 올바르게 아무것도 하지 않았다"가 "Stage 2가
깨졌다"를 덮어쓸 수 없다는 뜻입니다. 좋은 쪽에서 나쁜 쪽으로 늘어놓은 순서는 다음과
같습니다.

```
running < success < no_data < partial_success < failed_stage3 < failed_stage1 < interrupted
```

`--stage3-hpc`를 쓰면 Stage 3는 `run_hpc_stage3.py`의 종료 코드로 채점됩니다.
`--stage3-hpc-execute`까지 붙이면 `0`은 `success`, `3`은 `no_data`, `4`는
`partial_success`, `1`과 `2`는 `failed_stage3`입니다. 붙이지 않으면 사이클이 요청한
것은 분석이 아니라 전송용 번들이므로, 번들을 쓴 것 자체가 `success`입니다.
[../srr_pipeline_package/hpc/README.md](../srr_pipeline_package/hpc/README.md#what-the-runner-reports)
참고.

### Stage 1 상태

Stage 1은 자체 결과를 `stage1.status`에 남깁니다.

| 상태 | 의미 |
|------|------|
| `success` | 요청 범위를 전부 크롤링 |
| `capped` | GEO가 실제로 가진 페이지로 범위가 잘렸고, 그 범위는 전부 크롤링 |
| `no_pages_in_range` | 요청 범위에 페이지가 없었음. 사이클은 `no_data`가 됨 |
| `interrupted` | Ctrl+C |
| `failed` | 그 외 |

GEO가 가진 것보다 많은 페이지를 요청하는 것은 **오류가 아닙니다.** 크롤러가 실제 총
페이지 수를 파악해 경고를 남기고 범위를 잘라낸 뒤 존재하는 만큼 크롤링합니다.
`capped`는 성공으로 집계됩니다. 존재하는 페이지를 전부 크롤링한 완주이기 때문입니다.
실제로 다룬 범위는 `stage1.capped_pages`에 기록되고, 콘솔에는
`(capped at page X of the Y requested)`로 출력됩니다.

---

## 빠른 참조

### 시나리오별 필요 Docker 이미지

| 이미지 | 빌드 명령어 | A | B | C | D | E |
|-------|-----------|---|---|---|---|---|
| `genoar-crawler:amd64` | `docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/` | O | O | — | O | O |
| `genoar-analysis:latest` | `docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .` | O | O | O | O | O |
| `genoar-srr:step9` | `docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .` | — | — | O | O | O |

### 시나리오별 외부 의존성

| 의존성 | A | B | C | D | E |
|-------|---|---|---|---|---|
| UMLS 테이블 (`all_query_results/`, 포함) | O | O | O | O | O |
| Cell Ranger 바이너리 | — | — | O | O | O |
| 참조 게놈 | — | — | O | O | O |
| 이전 Stage 2 실행 결과 | — | — | O | — | — |
| 인터넷 (GEO 크롤링) | O | O | — | O | O |
| 인터넷 (SRA 다운로드) | — | — | O | O | O |

### 주요 CLI 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--cycles N` | 실행할 사이클 수 (필수) | — |
| `--pages-per-cycle N` | 사이클당 GEO 페이지 수 | 100 |
| `--output-dir PATH` | 출력 디렉토리 | `test_cycles` |
| `--run-stage3` | Stage 3 활성화 (다운로드 + Cell Ranger) | 비활성 |
| `--stage3-only` | 기존 데이터로 Stage 3만 실행 | 비활성 |
| `--download-only` | SRA 다운로드만 실행 (Cell Ranger 제외) | 비활성 |
| `--max-samples N` | SRA 샘플 수 제한 | 전체 |
| `--cellranger-cores N` | Cell Ranger CPU 코어 수 | 16 |
| `--cellranger-mem N` | Cell Ranger 메모리 (GB) | 100 |
| `--cellranger-path PATH` | Cell Ranger 설치 경로 | 자동 감지 |
| `--ref-genome-path PATH` | 참조 게놈 경로 | `--species` 기준 자동 감지 |
| `--species {human,mouse}` | 이 실행이 다루는 종. 어떤 레퍼런스를 쓸지 정하고, 설치된 레퍼런스가 밝히는 게놈과 대조합니다 | `human` |
| `--dry-run` | 실행 계획만 출력 | 비활성 |

### Make 변수

`make` 진입점은 설정을 `.env`나 명령줄에서 읽고, 명령줄이 우선합니다. `.env`의 모든
줄은 따옴표 없는 평범한 `KEY=value`여야 합니다. Make도 이 파일을 파싱하므로, 읽지
못하는 줄이 하나라도 있으면 모든 `make` 호출이 실패합니다.

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `PAGES` | 크롤링할 GEO 페이지 수 (`make run`) | 100 |
| `WORKERS` | 병렬 크롤 워커 수 | 1 |
| `MAX_SAMPLES` | `make fetch-sra`가 내려받을 SRA 수, 또는 `all` | 2 |
| `MAX_CONCURRENT` | `make fetch-sra`의 동시 다운로드 수 | 4 |
| `SRA_SOURCE` | 액세션 출처: 필터 적용 `stage2`, 또는 명시적인 무필터 `raw-stage1` | stage2 |
| `DRY_RUN` | `make fetch-sra`가 목록만 출력하고 중단 | 비활성 |
| `STAGE3_RESULTS` | `make status`가 Stage 3 실행을 읽어 오는 결과 디렉터리. 손으로 띄운 컨테이너를 다른 곳에 쓰게 했다면 이 값으로 지목하기 전까지 `Latest run: none recorded`로 보고됩니다 | results |
| `COMPOSE_PROJECT_NAME` | `make run`이 쓰는 Compose 프로젝트. 동시에 도는 두 번째 실행에는 별도 이름을 줍니다. 결과가 어디에 쌓이는지도 이 값이 정합니다. 기본 프로젝트는 체크아웃 루트에, 다른 이름은 `projects/<name>/`에 씁니다. `make status`, `make logs`, `make clean`에도 같은 이름을 넘기십시오 | genoar |

기본값이 `WORKERS=1`인 것은, 단일 크롤러에는 병렬 크롤에 필요한 run 격리 장치가 아예
필요 없기 때문입니다. 값을 올리는 것도 지원됩니다. 워커마다
`crawl_output/runs/<run_id>/` 아래 자기 디렉터리를 받습니다. 다만 의도적으로
올리십시오.

돌고 있는 `make run`을 지켜보려면 `make logs`를 쓰십시오.
`docker compose -p <project> logs -f genoar`가 실제로 실행되는 명령입니다.
`docker logs`에 넘길 고정된 컨테이너 이름은 이제 없습니다. Compose가 프로젝트마다
`<project>-genoar-<n>`으로 이름을 붙이므로, 두 실행이 서로를 다시 만드는 일이
생기지 않습니다.

`make` 타깃은 성공을 `0`, 실패를 Make 자신의 0이 아닌 코드로 보고합니다. 컨테이너의
정확한 코드를 그대로 전달하지는 않습니다. 그 값이 필요하면 스크립트나 컨테이너를
직접 호출하십시오.

---

## 문제 해결

### Docker 이미지를 찾을 수 없음

```bash
# 기존 이미지 확인
docker images | grep genoar   # Windows PowerShell: docker images | Select-String genoar

# 누락된 이미지 빌드 (프로젝트 루트에서)
docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .
docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .
```

### Stage 1 실패 (크롤러)

```bash
# 크롤러 로그 확인
cat <output-dir>/cycle_*/logs/stage1.log

# 일반적인 원인:
# - Docker 이미지가 빌드되지 않음
# - 네트워크 문제 (GEO 웹사이트 접근 불가)
# - 공유 메모리 부족 (Docker --shm-size)
```

### 사이클이 `no_data`로 보고됨

실패가 아닙니다. 실행 자체는 유효하게 완주했고 할 일이 없었을 뿐입니다. 어느 쪽인지는
`no_data_reason`에서 확인합니다.

```bash
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('status'), '-', d.get('no_data_reason'))" \
  <output-dir>/cycle_*/summary.json
```

- *요청 범위에 페이지가 없음* — `--start-page`가 이 쿼리의 GEO 결과 끝을 넘어섰습니다.
  시작 페이지나 사이클 수를 낮추십시오.
- *SRR ID 없음* — Stage 2 테이블에 SRA Run Selector 목록을 가진 시리즈가 없었습니다.
- *Cell Ranger가 아무것도 분석하지 않음* — 아래 항목을 참고하십시오.

### Stage 2 실패 (분석)

```bash
# 분석 로그 확인
cat <output-dir>/cycle_*/logs/stage2.log

# 일반적인 원인:
# - all_query_results/에 UMLS 테이블이 없음
# - Stage 1에서 META 파일을 수집하지 못함 (데이터셋을 0개 찾은 경우)
```

### Stage 3 SRA 다운로드 실패

```bash
# 실패한 다운로드 확인
cat <output-dir>/cycle_*/stage3/sra/download_failed.txt

# SRA 네트워크 연결 테스트
curl -I https://sra-pub-run-odp.s3.amazonaws.com/sra/SRR000001/SRR000001

# 일반적인 원인:
# - 네트워크 타임아웃
# - SRR ID가 더 이상 사용 불가
# - 디스크 공간 부족
```

### Stage 3 Cell Ranger 실패

```bash
# Docker 파이프라인 로그 확인
cat <output-dir>/cycle_*/stage3/logs/docker_stderr.log

# 일반적인 원인:
# - 지정된 경로에 Cell Ranger 바이너리가 없음
# - 지정된 경로에 참조 게놈이 없음
# - 메모리 부족 (Cell Ranger는 대부분의 샘플에 64GB 이상 필요)
# - FASTQ 파일이 너무 작거나 손상됨
```

### Stage 3은 돌았는데 분석된 것이 없음

파이프라인이 완주했는데 이번 실행이 맡은 샘플 중 Cell Ranger 산출물이 단 하나도
나오지 않으면 Stage 3은 `3`으로 종료하고 사이클은 `no_data`가 됩니다. 이유를 담은
명세는 결과 옆에 남습니다.

```bash
# 자격 여부와 사유가 모든 샘플에 대해 기록됩니다 (step 8)
cat <output-dir>/cycle_*/stage3/results/success/cellranger_eligibility.tsv

# 그보다 앞서 step 6·step 7이 걸러낸 샘플
cat <output-dir>/cycle_*/stage3/results/success/cellranger_ineligible.tsv
```

일반적인 원인:

- 샘플의 FASTQ가 1개뿐입니다. Cell Ranger는 R1/R2 쌍을 요구하므로 이 샘플은 처리될
  수 없습니다.
- 이미지를 1–7단계 대상으로 빌드해 Cell Ranger 단계 자체가 들어 있지 않습니다.
  `--target step9`로 빌드하십시오.

`cellranger/`나 `ref/`가 없는 경우는 별개의 결과입니다.
파이프라인이 step 0에서 `2`로 종료하고 사이클은 `failed_stage3`이 됩니다. 일부 샘플만
검증된 경우는 또 다른 결과입니다. 종료 코드 `4`, 사이클 `partial_success`이며 샘플별
사유는 `<output-dir>/cycle_*/stage3/results/runs/<run_id>/outcome.json`에 있습니다.
`GENOAR_ADOPT_PRIOR_RESULTS=1`로 채택한 샘플은 따로 보고하며 이 판정 어디에도
포함되지 않습니다.

쓸 수 있는 `outcome.json` 없이 돌아온 실행은 네 번째 결과이고 사이클은
`failed_stage3`이 됩니다. 그 실행은 무엇을 처리했는지 아무 말도 하지 않았고, 결과
볼륨은 계속 남는 저장소이므로, 이번 실행의 기록이 달리 말하지 않는 한 거기 있는
산출물은 앞선 실행들의 것입니다. 기록 없는 실행이 나오는 경로는 둘입니다. Stage 3
이미지가 실행 기록 도입 이전 버전이면 `make build-srr`로 다시 빌드한 뒤 재실행하십시오.
기록을 쓰기 전에 실행이 끝난 경우라면, 이번 사이클의 Stage 3 로그 디렉터리에 있는
`docker_stdout.log`가 어디까지 갔는지 알려줍니다.
