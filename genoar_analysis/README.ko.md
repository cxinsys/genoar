# GENOAR Analysis (Stage 2)

크롤링된 GEO 메타데이터를 UMLS (Unified Medical Language System)와 매칭하여 표준화된 생물의학 개념으로 주석을 달아주는 분석 파이프라인입니다.

## 개요

Stage 1(Crawler)에서 수집한 META 파일을 입력받아 cell_type, tissue, disease 필드를 UMLS 표준 용어와 매칭하여 주석이 달린 CSV 테이블을 생성합니다.

```
[입력]                          [처리]                         [출력]
crawl_output/META/    →    UMLS 매칭 & 주석    →    HS_*_1st_pass_meta_table.csv
  GSE*_meta.txt              (FirstPassPipeline)         • cell_type
                                                         • tissue
all_query_results/                                       • disease
  umls_*_df.csv
```

---

## 빠른 시작

### Docker 실행 (권장)

```bash
# 1. Docker 이미지 빌드
cd /path/to/genoar
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .

# 2. 분석 실행
docker run --rm \
  -v $(pwd)/crawl_output/META:/data/meta:ro \
  -v $(pwd)/all_query_results:/data/umls:ro \
  -v $(pwd)/first_pass_output:/data/output \
  genoar-analysis:latest \
  --meta-dir /data/meta \
  --umls-dir /data/umls \
  --output-dir /data/output

# 3. 결과 확인
ls first_pass_output/
# HS_cell_type_1st_pass_meta_table.csv
# HS_tissue_1st_pass_meta_table.csv
# HS_disease_1st_pass_meta_table.csv
```

### Python API 실행

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 파이프라인 실행
python -c "
from genoar_analysis.pipelines import FirstPassPipeline
pipeline = FirstPassPipeline('crawl_output/META', 'all_query_results')
results = pipeline.create_all_first_pass_tables('first_pass_output')
"

# 또는 스크립트 실행
python generate_first_pass_tables.py
```

---

## 입력 데이터

### META 파일 (Stage 1 출력)

```
crawl_output/META/
└── GSE*_meta.txt       # 탭 구분 메타데이터
    ├── Run             # SRR 액세션 번호
    ├── BioSample       # 바이오샘플 ID
    ├── Organism        # 생물종
    ├── cell_type       # 세포 유형
    ├── tissue          # 조직 유형
    ├── disease         # 질병 상태
    └── ...
```

### UMLS 테이블

```
all_query_results/
├── umls_celltype_df.csv    # 세포 유형 개념 (CUI, STR, SAB, STY)
├── umls_tissue_df.csv      # 조직 개념
└── umls_disease_df.csv     # 질병 개념
```

레포지토리에 포함되어 있습니다. 확장하려면 메인 [README.ko.md](../README.ko.md#23-umls-데이터-준비)를 참고하세요.

---

## 출력 데이터

```
first_pass_output/
├── HS_cell_type_1st_pass_meta_table.csv
├── HS_tissue_1st_pass_meta_table.csv
└── HS_disease_1st_pass_meta_table.csv
```

### 출력 테이블 컬럼

| 컬럼 | 설명 | 예시 |
|------|------|------|
| Series | GSE 시리즈 ID | GSE123456 |
| Run | SRR 실행 ID | SRR7890123 |
| BioSample | 바이오샘플 ID | SAMN12345678 |
| cell_type / tissue / disease | 원본 값 | T cell |
| **CUI** | UMLS Concept Unique ID | C0039194 |
| **STR** | UMLS 표준 문자열 | T-Lymphocyte |
| **SAB** | 출처 약어 | NCI |
| **STY** | 의미 유형 | Cell |

---

## 파이프라인 단계

```
Step 1: 데이터 로딩
    │   crawl_output/META/*.txt 파일 읽기
    ▼
Step 2: 생물종 필터링
    │   Organism == "Homo sapiens" 필터
    ▼
Step 3: 필드 통합 (Consolidation)
    │   disease_state_modified 등 여러 필드 통합
    ▼
Step 4: UMLS 쿼리
    │   cell_type, tissue, disease를 UMLS DB와 매칭
    ▼
Step 5: SAB 우선순위 선택
    │   동일 용어의 여러 출처 중 최적 선택
    ▼
Step 6: First-pass 테이블 생성
        원본 메타데이터 + UMLS 주석 결합 → CSV 저장
```

---

## 디렉토리 구조

```
genoar_analysis/
├── core/
│   └── data.py                 # GenoarData 클래스 (AnnData 스타일)
├── io/
│   ├── readers.py              # META 파일 로드
│   └── umls_readers.py         # UMLS CSV 처리 + SAB 우선순위
├── preprocessing/
│   ├── filters.py              # 생물종 필터링
│   └── field_consolidation.py  # 필드 통합
├── analysis/
│   └── umls.py                 # UMLS 매칭 분석
├── pipelines/
│   └── first_pass_pipeline.py  # 전체 워크플로우
├── docker/
│   ├── Dockerfile              # Docker 이미지 정의
│   └── run_analysis.py         # Docker 엔트리포인트
└── README.md                   # 이 문서
```

---

## CLI 옵션 (Docker)

```bash
docker run genoar-analysis:latest [OPTIONS]

필수 옵션:
  --meta-dir PATH      META 파일 디렉토리
  --umls-dir PATH      UMLS CSV 파일 디렉토리
  --output-dir PATH    출력 디렉토리

선택 옵션:
  --json-output PATH   결과 요약 JSON 파일 경로
```

---

## 문제 해결

### Import 오류

```bash
# 의존성 설치
pip install -r requirements.txt
```

### UMLS 매칭 실패

```bash
# UMLS 테이블 확인
ls all_query_results/umls_*_df.csv
# 필요 파일: umls_celltype_df.csv, umls_tissue_df.csv, umls_disease_df.csv
```

### Docker 빌드 실패

```bash
# 프로젝트 루트에서 빌드 (Dockerfile이 genoar_analysis/를 여기서 복사)
cd /path/to/genoar
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .
```

---

## 테스트

```bash
# 기본 모듈 테스트
python genoar_analysis_tests/test_basic_modules.py

# 워크플로우 테스트
python genoar_analysis_tests/test_first_pass_workflow.py
```
