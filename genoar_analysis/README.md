# GENOAR Analysis (Stage 2)

[Korean / 한국어](README.ko.md)

Analysis pipeline that annotates crawled GEO metadata with standardized biomedical concepts by matching `cell_type`, `tissue`, and `disease` fields against UMLS (Unified Medical Language System).

## Overview

Takes the META files produced by Stage 1 (Crawler), matches `cell_type`, `tissue`, and `disease` fields to UMLS standard terms, and emits annotated CSV tables.

```
[Input]                        [Processing]                   [Output]
crawl_output/META/    →    UMLS matching & annotation   →   HS_*_1st_pass_meta_table.csv
  GSE*_meta.txt              (FirstPassPipeline)              • cell_type
                                                              • tissue
all_query_results/                                            • disease
  umls_*_df.csv
```

---

## Quick Start

### Docker (recommended)

```bash
# 1. Build the image
cd /path/to/genoar
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .

# 2. Run the analysis
docker run --rm \
  -v $(pwd)/crawl_output/META:/data/meta:ro \
  -v $(pwd)/all_query_results:/data/umls:ro \
  -v $(pwd)/first_pass_output:/data/output \
  genoar-analysis:latest \
  --meta-dir /data/meta \
  --umls-dir /data/umls \
  --output-dir /data/output

# 3. Check results
ls first_pass_output/
# HS_cell_type_1st_pass_meta_table.csv
# HS_tissue_1st_pass_meta_table.csv
# HS_disease_1st_pass_meta_table.csv
```

### Python API

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the pipeline
python -c "
from genoar_analysis.pipelines import FirstPassPipeline
pipeline = FirstPassPipeline('crawl_output/META', 'all_query_results')
results = pipeline.create_all_first_pass_tables('first_pass_output')
"

# Or run the helper script
python generate_first_pass_tables.py
```

---

## Input Data

### META files (Stage 1 output)

```
crawl_output/META/
└── GSE*_meta.txt       # Tab-separated metadata
    ├── Run             # SRR accession number
    ├── BioSample       # BioSample ID
    ├── Organism        # Species
    ├── cell_type       # Cell type
    ├── tissue          # Tissue type
    ├── disease         # Disease state
    └── ...
```

### UMLS tables

```
all_query_results/
├── umls_celltype_df.csv    # Cell-type concepts (CUI, STR, SAB, STY)
├── umls_tissue_df.csv      # Tissue concepts
└── umls_disease_df.csv     # Disease concepts
```

Included in the repository. To extend them, see the main [README.md](../README.md#umls-reference-data).

---

## Output Data

```
first_pass_output/
├── HS_cell_type_1st_pass_meta_table.csv
├── HS_tissue_1st_pass_meta_table.csv
└── HS_disease_1st_pass_meta_table.csv
```

### Output table columns

| Column | Description | Example |
|--------|-------------|---------|
| Series | GSE series ID | GSE123456 |
| Run | SRR run ID | SRR7890123 |
| BioSample | BioSample ID | SAMN12345678 |
| cell_type / tissue / disease | Original value | T cell |
| **CUI** | UMLS Concept Unique ID | C0039194 |
| **STR** | UMLS standard string | T-Lymphocyte |
| **SAB** | Source abbreviation | NCI |
| **STY** | Semantic type | Cell |

---

## Pipeline Steps

```
Step 1: Data loading
    │   Read crawl_output/META/*.txt
    ▼
Step 2: Species filter
    │   Keep Organism == "Homo sapiens"
    ▼
Step 3: Field consolidation
    │   Merge disease_state_modified and related fields
    ▼
Step 4: UMLS query
    │   Match cell_type / tissue / disease against the UMLS DB
    ▼
Step 5: SAB priority selection
    │   Pick the best source among multiple hits for the same term
    ▼
Step 6: First-pass table generation
        Combine original metadata + UMLS annotations → write CSV
```

---

## Directory Structure

```
genoar_analysis/
├── core/
│   └── data.py                 # GenoarData class (AnnData-style container)
├── io/
│   ├── readers.py              # META file loaders
│   └── umls_readers.py         # UMLS CSV loaders + SAB priority
├── preprocessing/
│   ├── filters.py              # Species filtering
│   └── field_consolidation.py  # Field consolidation
├── analysis/
│   └── umls.py                 # UMLS matching analysis
├── pipelines/
│   └── first_pass_pipeline.py  # End-to-end workflow
├── docker/
│   ├── Dockerfile              # Image definition
│   └── run_analysis.py         # Docker entrypoint
├── README.md                   # This file (English)
└── README.ko.md                # Korean version
```

---

## CLI Options (Docker)

```bash
docker run genoar-analysis:latest [OPTIONS]

Required:
  --meta-dir PATH      Directory containing META files
  --umls-dir PATH      Directory containing UMLS CSV files
  --output-dir PATH    Output directory

Optional:
  --json-output PATH   Path to summary JSON output
```

---

## Troubleshooting

### Import errors

```bash
pip install -r requirements.txt
```

### UMLS matching fails

```bash
# Verify the UMLS tables are present
ls all_query_results/umls_*_df.csv
# Required: umls_celltype_df.csv, umls_tissue_df.csv, umls_disease_df.csv
```

### Docker build fails

```bash
# Build from the project root (the Dockerfile copies genoar_analysis/ from there)
cd /path/to/genoar
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .
```

---

## Tests

```bash
# Basic module tests
python genoar_analysis_tests/test_basic_modules.py

# End-to-end workflow test
python genoar_analysis_tests/test_first_pass_workflow.py
```
