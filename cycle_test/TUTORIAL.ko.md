# GENOAR 전체 파이프라인 튜토리얼 (Linux 서버)

본 문서는 처음 GENOAR를 사용하는 사용자가 **Linux 서버에서** `cycle_test`를 통해 **Stage 1 → Stage 2 → Stage 3** 전체 파이프라인을 단계적으로 검증하며 실행하는 가이드입니다.

총 4단계로 구성되어 있으며, 각 단계는 이전 단계의 성공을 전제로 합니다. 중간에 문제가 생기면 다음 단계로 넘어가지 말고 [트러블슈팅](#트러블슈팅)을 먼저 참고하세요.

```
[0] 사전 준비 → [1] Dry run → [2] Stage 1+2만 실행 → [3] 소규모 end-to-end
```

- **예상 총 소요 시간:** 3~12시간 (Stage 3 소규모 실행 포함)
- **필요한 접근 권한:** Docker를 실행할 수 있는 사용자 (docker group 소속 또는 sudo)
- **대상 경로:** 본 문서는 GENOAR 저장소가 `/path/to/genoar`에 있다고 가정합니다. 다른 경로라면 해당 경로로 바꿔 읽으세요.

---

## 0. 사전 준비

### 0.1 시스템 요구사항 확인

튜토리얼을 시작하기 전에 서버가 아래 조건을 만족하는지 확인합니다.

```bash
# 아키텍처 (Stage 3는 AMD64 필수)
uname -m            # → x86_64 이어야 함

# CPU 코어
nproc               # → 권장 16+

# 메모리
free -g | awk 'NR==2{print $2" GB"}'   # → 권장 128GB+, 최소 64GB

# 디스크 여유 공간 (소규모 테스트 기준 최소 100GB)
df -h /path/to/genoar   # Available 컬럼 확인

# Docker
docker --version    # Docker 20.10 이상

# Python
python3 --version   # Python 3.9 이상
```

> **주의.** `uname -m`이 `aarch64`/`arm64`라면 Stage 3(Cell Ranger)는 실행할 수 없습니다. 이 경우 [1]~[2]까지만 진행 가능합니다.

### 0.2 다운로드 도구 설치 (선택이지만 권장)

Stage 3a는 AWS S3에서 대용량 SRA 파일을 병렬 다운로드합니다. `aria2c`가 있으면 자동으로 사용하며 속도가 크게 향상됩니다.

```bash
sudo apt update && sudo apt install -y aria2
which aria2c        # → /usr/bin/aria2c
```

`aria2c`를 설치할 수 없는 환경이라면 시스템에 기본 설치된 `wget` 또는 `curl`로 자동 폴백합니다.

### 0.3 Docker 이미지 3종 빌드

`cycle_test`는 Docker 이미지 3개에 의존합니다. 각 이미지를 저장소 루트에서 빌드합니다.

```bash
cd /path/to/genoar

# Stage 1: GEO 크롤러 (AMD64)
docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/

# Stage 2: UMLS 분석
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .

# Stage 3: SRR 파이프라인 (AMD64 전용)
docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .
```

**예상 빌드 시간:** Stage 1 약 5~10분, Stage 2 약 3~5분, Stage 3 약 10~15분.

**검증:**

```bash
docker images | grep -E "genoar-crawler|genoar-analysis|genoar-srr"
```

세 줄이 모두 보여야 합니다. 예:
```
genoar-crawler    amd64     ...   1.2GB
genoar-analysis   latest    ...   500MB
genoar-srr        step9     ...   700MB
```

### 0.4 UMLS 테이블 확인 (다운로드 불필요)

UMLS 3종 CSV는 저장소에 이미 포함되어 있습니다. 파일명이 아래와 정확히 일치하는지 확인하세요.

```bash
ls /path/to/genoar/all_query_results/umls_*_df.csv
# 예상 출력:
#   umls_celltype_df.csv
#   umls_disease_df.csv
#   umls_tissue_df.csv
```

세 파일 중 하나라도 없으면 Stage 2가 실패합니다.

### 0.5 Cell Ranger 설치 (Stage 3 전용)

`[3]` 소규모 end-to-end 단계에서만 필요합니다. `[1]`~`[2]`까지만 실행할 계획이라면 이 단계는 건너뛰세요.

1. [10x Genomics 다운로드 페이지](https://www.10xgenomics.com/support/software/cell-ranger/downloads)에서 Cell Ranger 8.x 이상 tarball을 받습니다 (무료 계정 필요).
2. 저장소 루트에 `cellranger/` 이름으로 압축을 풉니다.

```bash
cd /path/to/genoar

# 받은 tarball을 저장소 루트로 옮긴 뒤:
tar -xzvf cellranger-8.0.1.tar.gz
mv cellranger-8.0.1 cellranger

# 검증: 바이너리가 아래 경로에 있어야 함
ls -l /path/to/genoar/cellranger/cellranger
```

### 0.6 Reference genome 설치 (Stage 3 전용)

10x Genomics 공개 GRCh38 레퍼런스를 다운로드하여 저장소 루트에 `ref/`로 배치합니다.

```bash
cd /path/to/genoar

wget https://cf.10xgenomics.com/supp/cell-exp/refdata-gex-GRCh38-2024-A.tar.gz
tar -xzvf refdata-gex-GRCh38-2024-A.tar.gz
mv refdata-gex-GRCh38-2024-A ref

# 검증: 다음 4개가 보여야 함
ls /path/to/genoar/ref/
# → fasta/  genes/  reference.json  star/
```

> **디스크 참고.** `refdata-gex-GRCh38-2024-A.tar.gz`는 압축 해제 후 약 16GB입니다. 다운로드 tarball은 삭제해도 됩니다.

### 0.7 사전 준비 최종 체크리스트

`[1]` 단계로 넘어가기 전에 아래 항목을 전부 확인합니다.

- [ ] `docker images | grep genoar` → 3줄 출력
- [ ] `/path/to/genoar/all_query_results/` → `umls_*_df.csv` 3개 존재
- [ ] `/path/to/genoar/cellranger/cellranger` 존재 (Stage 3 사용 시)
- [ ] `/path/to/genoar/ref/fasta/` 존재 (Stage 3 사용 시)
- [ ] `which aria2c || which wget || which curl` → 하나 이상 출력

---

## 1단계: Dry run — 실행 계획만 확인 (부작용 없음)

실제 실행 전에 `--dry-run`으로 **경로 자동 감지**와 사이클 계획을 확인합니다. 이 단계는 어떤 파일도 생성하지 않으며, 디렉터리도 만들지 않습니다. 안전하게 여러 번 반복 실행해도 됩니다.

```bash
cd /path/to/genoar

python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline \
  --run-stage3 --dry-run
```

### 확인해야 할 로그

출력에서 아래 4줄을 반드시 확인하세요.

```
Stage 3 Configuration:
  ...
  Cell Ranger:     /path/to/genoar/cellranger
  Ref Genome:      /path/to/genoar/ref
```

`(auto-detect failed — pass --cellranger-path)` 메시지가 뜨면 `[0.5]` 또는 `[0.6]`에서 경로 설치가 잘못된 것입니다. 해당 단계로 돌아가 수정 후 다시 실행하세요.

Cycle 계획도 다음과 유사한 형태로 출력됩니다.

```
[DRY RUN MODE - No actual execution]
  Cycle 1: Pages 1-5 -> cycle_001_pages_0001-0005/
           Stage 1 -> Stage 2 -> Stage 3 (Download + Pipeline)
[DRY RUN COMPLETE]
```

**Stage 3를 사용하지 않을 계획이면** `--run-stage3`를 빼고 dry-run을 돌립니다:

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline --dry-run
```

---

## 2단계: Stage 1+2만 실행 — 크롤러/분석 검증 (약 60~90분)

Cell Ranger 없이 Stage 1(크롤링)과 Stage 2(UMLS 매칭)만 먼저 실행하여 GEO 크롤러와 UMLS 분석 파이프라인이 정상 작동하는지 확인합니다.

```bash
cd /path/to/genoar

python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline
```

### 실행 중 진행 상황 모니터링

별도 터미널에서 master 로그를 tail 하면 Stage 1 크롤러의 진행 상황이 실시간으로 스트리밍됩니다.

```bash
tail -f /path/to/genoar/test_full_pipeline/master_*.log
```

### 예상 결과

정상 종료 시 콘솔에 다음과 유사한 요약이 출력됩니다.

```
CYCLE TEST FINAL REPORT
============================================================
Total Cycles: 1

Success Rates:
  Stage 1: 100.0%
  Stage 2: 100.0%
  Overall: 100.0%

Totals:
  META files:         15~30개
  SMTX files:         유사
  SRR files:          유사
  Total SRR IDs:      100~500개
  Cell Type samples:  수십 개
  ...
```

### 산출물 검증

```bash
cd /path/to/genoar/test_full_pipeline

# 1) 디렉터리 구조 확인
ls cycle_001_pages_0001-0005/
# → stage1/  stage2/  stage3/  logs/  summary.json

# 2) Stage 1: META 파일이 실제로 만들어졌는지
ls cycle_001_pages_0001-0005/stage1/META/ | head -5
ls cycle_001_pages_0001-0005/stage1/META/ | wc -l

# 3) Stage 2: 3종 CSV가 생성됐는지
ls cycle_001_pages_0001-0005/stage2/*.csv
# → HS_cell_type_1st_pass_meta_table.csv
# → HS_disease_1st_pass_meta_table.csv
# → HS_tissue_1st_pass_meta_table.csv

# 4) 행 개수 확인 (헤더 포함)
wc -l cycle_001_pages_0001-0005/stage2/*.csv

# 5) 사이클 요약
python -m json.tool cycle_001_pages_0001-0005/summary.json
```

### 여기서 반드시 확인해야 할 것

- 명령 직후 `echo $?`가 `0` 인지
- `summary.json`의 `status`가 `success` 인지
- Stage 2 CSV 3종 중 최소 1개 이상의 `rows > 0` 인지

`master_*.log`의 `WARNING`은 판정 기준이 아닙니다. 채울 수 없는 범위를 잘라낼 때도
크롤러는 경고를 남기는데, 그것은 정상적인 완주입니다. 로그 수준이 아니라 상태를
읽으십시오. `no_data`는 실행이 유효했고 크롤링할 것이 없었다는 뜻이며,
`failed_`로 시작하는 상태는 멈추고 원인을 찾아야 한다는 뜻입니다. 상태 목록은
[`README.ko.md`](README.ko.md#72-사이클-상태와-종료-코드)에 있습니다.

**이 단계가 실패하면** Stage 3는 어차피 실행할 수 없습니다. [트러블슈팅](#트러블슈팅)을 참고하세요.

> **페이지를 늘리고 싶다면.** 튜토리얼 용도로는 5페이지면 충분하지만, 실사용에 가까운 볼륨을 원하면 `--pages-per-cycle 100`으로 늘립니다. 단, Stage 1 크롤링 시간이 페이지 수에 비례하여 증가하므로 100페이지 시 약 10~20시간 소요됩니다.

---

## 3단계: 소규모 end-to-end — Stage 3까지 포함 (약 3~10시간)

`[2]`가 성공했다면, 이번에는 Stage 2 테이블에서 추출된 SRR ID 중 **2개만** 다운로드하여 Cell Ranger까지 돌리는 end-to-end 스모크 테스트를 실행합니다.

```bash
cd /path/to/genoar

python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline_e2e \
  --run-stage3 --max-samples 2 \
  --cellranger-cores 8 --cellranger-mem 64
```

### 옵션 설명

| 옵션 | 역할 |
|------|------|
| `--run-stage3` | Stage 3 활성화 (다운로드 + Cell Ranger) |
| `--max-samples 2` | Stage 2가 뽑은 SRR 중 최대 2개만 다운로드 |
| `--cellranger-cores 8` | Cell Ranger가 사용할 CPU 코어 |
| `--cellranger-mem 64` | Cell Ranger가 사용할 메모리 (GB) |

> **리소스 튜닝.** 서버 코어/메모리가 더 크면 `--cellranger-cores 16 --cellranger-mem 100`까지 올릴 수 있습니다. 반대로 다른 작업과 공유 중이라면 더 낮추세요.

### 실행 중 모니터링 (별도 터미널)

```bash
# 전체 master 로그 (Cell Ranger 진행도 포함)
tail -f /path/to/genoar/test_full_pipeline_e2e/master_*.log

# Stage 3 Docker 자체 출력 (Cell Ranger step 진행 상황)
tail -f /path/to/genoar/test_full_pipeline_e2e/cycle_*/stage3/logs/docker_stdout.log
```

> **"멈춘 듯" 보여도 정상입니다.** Cell Ranger 1 샘플은 1~4시간 걸리며, 이 동안 stdout이 수 분~수십 분 단위로 갱신됩니다. 30분 이상 진행이 없어 보여도 먼저 `docker_stdout.log`를 확인한 뒤에 판단하세요.

### 단계별 타임라인 예시

| 단계 | 대략 소요 | 로그 표시 |
|------|-----------|-----------|
| Stage 1 (5페이지 크롤) | 60~90분 | `[crawler]` 프리픽스 |
| Stage 2 (UMLS 매칭) | 1~2분 | `[analysis]` 프리픽스 |
| Stage 3a (SRA 다운로드 2개) | 6~20분 | download 진행률 |
| Stage 3b (Cell Ranger 2개) | 2~8시간 | `[cellranger]` 프리픽스 |

### 산출물 검증

```bash
cd /path/to/genoar/test_full_pipeline_e2e/cycle_001_pages_0001-0005

# 1) Stage 3 전처리 결과
python -m json.tool stage3/stage3_prep_summary.json
cat stage3/srr_list.txt

# 2) 다운로드된 SRA 파일
ls -lh stage3/sra/*/*.sra

# 3) Cell Ranger 성공 샘플
ls stage3/results/success/

# 4) Cell Ranger 출력 (핵심 리포트)
ls stage3/results/success/*/cellranger_output/outs/
# → web_summary.html  metrics_summary.csv  filtered_feature_bc_matrix/  ...
```

`web_summary.html`은 로컬로 다운로드해서 브라우저로 열면 QC 요약을 시각적으로 확인할 수 있습니다.

```bash
# 로컬 머신에서:
scp user@server:/path/to/genoar/test_full_pipeline_e2e/cycle_*/stage3/results/success/*/cellranger_output/outs/web_summary.html ./
```

### 여기서 반드시 확인해야 할 것

- `summary.json` → `stages.stage3_pipeline.success: true`
- `stage3/results/success/` 아래 샘플 디렉터리가 최소 1개 이상
- 각 성공 샘플의 `cellranger_output/outs/web_summary.html` 존재

**부분 성공 (`partial_success`)도 허용됩니다.** 2 샘플 중 하나는 성공하고 다른 하나는 SRA 다운로드 실패 또는 Cell Ranger 실패로 끝나는 경우가 흔합니다. 최소 1개가 성공하면 파이프라인 전체가 동작함을 증명한 것입니다.

**`no_data`는 실패가 아니지만 통과도 아닙니다.** Stage 3이 `3`으로 종료했다는 뜻입니다. 끝까지 돌았으나 넘겨받은 샘플 중 Cell Ranger 산출물이 나온 것이 하나도 없었습니다. 사유는 `no_data_reason`에 있습니다. [Stage 3b: 끝까지 돌았는데 분석된 샘플이 0건](#stage-3b-끝까지-돌았는데-분석된-샘플이-0건)을 참고하세요.

---

## (선택) Stage 3만 재실행 — 기존 Stage 2 결과 재사용

Stage 1 크롤링을 다시 할 필요 없이 **이미 완료된 Stage 2 결과**에 대해 Stage 3를 다른 샘플 개수/파라미터로 재실행하고 싶을 때 사용합니다.

```bash
cd /path/to/genoar

python cycle_test/run_cycle_test.py \
  --cycles 1 --stage3-only \
  --output-dir test_full_pipeline_e2e \
  --max-samples 5 \
  --cellranger-cores 16 --cellranger-mem 100
```

- `--output-dir`은 **이미 완료된 사이클 결과가 있는 디렉터리**를 그대로 가리킵니다.
- `cycle_NNN_pages_SSSS-EEEE/stage2/HS_*_1st_pass_meta_table.csv`가 존재해야 동작합니다.
- Stage 1, Stage 2는 건너뛰고 Stage 3 준비 → 다운로드 → Cell Ranger만 실행합니다.

**다운로드만 먼저 하고 싶다면** `--download-only`를 추가합니다. Cell Ranger는 생략되고 `stage3/sra/`까지만 채워집니다.

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 --stage3-only --download-only \
  --output-dir test_full_pipeline_e2e \
  --max-samples 5
```

---

## 트러블슈팅

### Stage 1: Docker 이미지를 찾을 수 없음

```
Docker image 'genoar-crawler:amd64' not found. Build it first.
```

→ `[0.3]` Docker 이미지 빌드를 다시 수행하세요. 빌드 후에는 반드시 `docker images | grep genoar`로 확인합니다.

### Stage 1: Chrome/ChromeDriver 권한 오류

```
[crawler] chromedriver: ... permission denied
```

호스트가 `--cap-add=SYS_ADMIN` / `--security-opt seccomp=unconfined`를 차단하는 환경입니다. 보안 정책을 완화하거나 해당 플래그가 허용되는 다른 호스트에서 `[2]`를 실행하세요.

### Stage 2: UMLS 파일을 못 찾음

```
UMLS data not found at /path/to/genoar/all_query_results
```

→ `ls all_query_results/umls_*_df.csv`로 3개 테이블을 확인하세요. 없다면 저장소에서 복원하세요 (커밋되어 있습니다).

### Stage 2: 테이블이 모두 0행

Stage 1이 META 파일을 만들긴 했으나 Homo sapiens + TRANSCRIPTOMIC 필터에 걸린 샘플이 없는 경우입니다. `--pages-per-cycle`을 늘려 더 넓은 범위를 크롤링하거나, `--start-page`를 바꿔 다른 구간을 시도하세요.

### Stage 3a: SRA 다운로드 실패

```bash
cat /path/to/genoar/test_full_pipeline_e2e/cycle_*/stage3/sra/download_failed.txt

# S3 접근 자체가 되는지 확인
curl -I https://sra-pub-run-odp.s3.amazonaws.com/sra/SRR000001/SRR000001
```

특정 SRR이 만료/이동된 경우 개별 실패가 정상입니다. 전부 실패한다면 네트워크/방화벽 문제입니다.

### Stage 3b: Cell Ranger 경로 자동 감지 실패

Dry-run 로그에 `(auto-detect failed — pass --cellranger-path)` 가 출력되면:

```bash
# 설치가 예상 위치에 되어 있는지
ls /path/to/genoar/cellranger/cellranger
ls /path/to/genoar/ref/fasta/

# 다른 위치에 있다면 명시적으로 지정
python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline_e2e \
  --run-stage3 --max-samples 2 \
  --cellranger-path /custom/path/to/cellranger \
  --ref-genome-path /custom/path/to/ref
```

### Stage 3b: 끝까지 돌았는데 분석된 샘플이 0건

Stage 3이 `3`으로 종료하고 사이클은 `no_data`가 됩니다. 이유를 담은 명세는 결과 옆에 있습니다.

```bash
cat test_full_pipeline_e2e/cycle_*/stage3/results/success/cellranger_eligibility.tsv
cat test_full_pipeline_e2e/cycle_*/stage3/results/success/cellranger_ineligible.tsv
```

가능성이 높은 순서로 두 가지입니다.

1. **샘플의 FASTQ가 1개뿐입니다.** Cell Ranger는 R1/R2 쌍을 요구하므로 이 샘플은 처리될 수 없습니다. 다른 SRR로 시도하십시오.
2. **이미지를 잘못된 대상으로 빌드했습니다.** 이미지에 Snakemake가 없으면 `summary.json`에 `step8: "not_run"`이 남습니다. `--target step9`로 다시 빌드하십시오.

`cellranger/`나 `ref/`가 없거나 쓸 수 없는 경우는 별개의 결과입니다. 파이프라인이 어떤 단계도 시작하기 전에 step 0에서 `2`로 종료하고, 사이클은 `failed_stage3`이 됩니다. 이 경우 사이클은 `no_data`가 되지 않습니다. 고쳐야 할 오류입니다. 빈 결과가 아닙니다.

### Stage 3b: 일부 샘플만 분석됨

Stage 3이 `4`로 종료하고 사이클은 `partial_success`가 됩니다. 이번 실행이 맡은 샘플 중 일부만 검증된 Cell Ranger 산출물을 가진 경우입니다. 샘플별 사유는 run 기록에 있습니다.

```bash
cat test_full_pipeline_e2e/cycle_*/stage3/results/runs/<run_id>/outcome.json
```

집계 대상은 이번 실행이 맡은 샘플뿐입니다. 이전 실행이 남긴 산출물은 `other_samples_with_output`에 나타나며, 보존되되 여기에 세지 않습니다. `GENOAR_ADOPT_PRIOR_RESULTS=1`로 채택한 산출물은 별도의 줄로 보고하며 어떤 판정에도 포함되지 않습니다.

### Stage 3b: Cell Ranger가 OOM / 오래 정지

```bash
cat /path/to/genoar/test_full_pipeline_e2e/cycle_*/stage3/logs/docker_stdout.log | tail -50
```

`--cellranger-mem`을 낮추거나 동시 샘플 수를 줄이세요. 시스템 메모리의 80%를 넘기지 않는 값이 안전합니다.

### 도중에 중단된 실행 재개

`Ctrl+C`는 종료를 요청하며 이후 사이클은 시작되지 않습니다. 실행 중인 사이클을 마치게 두지는 않습니다. 인터럽트가 크롤러에도 도달하므로 Stage 1은 `interrupted`로 끝나고 이후 스테이지는 건너뛰며, 프로세스는 `130`으로 종료합니다. `--skip-stage1`로 기존 Stage 1 결과를 재사용하거나, `--stage3-only`로 Stage 3만 재실행할 수 있습니다.

---

## 다음 단계

튜토리얼 `[1]`~`[3]`이 모두 성공했다면, 이제 본격적인 운영 실행으로 넘어갈 수 있습니다.

- **대규모 크롤링:** `--pages-per-cycle 100 --cycles 5 --wait-minutes 10` 으로 여러 사이클을 돌려 NCBI 전체 범위를 점진적으로 수집. GEO가 가진 것보다 많은 페이지를 요청해도 안전합니다 — 경고와 함께 범위가 잘립니다. 다만 `--start-page`가 끝을 넘어서면 크롤링할 것이 없어 사이클이 `no_data`로 끝납니다.
- **전체 샘플 Cell Ranger:** `--max-samples` 옵션을 빼면 Stage 2에서 추출된 모든 SRR을 처리합니다. 디스크 용량과 시간 예산을 먼저 점검하세요.
- **운영 관련 세부 옵션은** [`README.ko.md`](README.ko.md) §5 CLI 옵션 표를 참고하세요.
