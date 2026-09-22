# GENOAR Analysis Tests

This directory contains comprehensive test suites for the GENOAR Analysis package. After deduplication, the test suite is organized into two focused notebooks that eliminate ~70% of duplicate code while preserving all unique functionality.

## Test Structure

### 1. `integrated_functionality_test.ipynb` - Comprehensive Package Tests

Interactive Jupyter notebook providing complete testing of all package functionality after Ground Truth removal.

**Test Coverage:**
- **Package Structure** - Module imports and class availability
- **Data Loading** - META file processing with real data validation
- **GenoarData Core** - Object management and core methods
- **UMLS Integration** - Real data querying with statistics
- **Field Analysis** - Statistical analysis and field comparisons
- **Preprocessing** - Complete filtering and data cleaning pipeline
- **Visualization** - Actual plot generation with fallback strategies
- **Performance** - Memory usage and processing speed benchmarks
- **Pipeline Integration** - First-pass pipeline initialization

**Key Features:**
- Interactive execution with detailed output
- Real data processing where available
- Performance benchmarking and memory monitoring
- Comprehensive error handling with graceful fallbacks
- Visual plots and statistical summaries

**Usage:**
```bash
conda activate genoar
jupyter notebook integrated_functionality_test.ipynb
```

**Requirements:**
- META files in `crawl_output/META/`
- UMLS data in `all_query_results/` (optional - uses mock data if not found)
- No Ground Truth file needed

---

### 2. `test_first_pass_workflow.ipynb` - End-to-End Workflow Validation

Interactive Jupyter notebook providing complete end-to-end validation of the first-pass workflow from raw META files to final CSV outputs.

**Workflow Steps:**
```
META files (GSE*.txt)
     ↓
1. Load and merge
     ↓
2. Filter (Homo sapiens, TRANSCRIPTOMIC)
     ↓
3. Consolidate fields (pipeline consolidation)
   - build disease_state_modified
   - merge the sex fields
     ↓
4. UMLS matching (umls_readers.py)
   - CSV-backed lookup
   - apply SAB priority order
     ↓
5. Keep only rows that got a CUI
     ↓
6. Write CSV output
   - HS_cell_type_1st_pass_meta_table.csv
   - HS_tissue_1st_pass_meta_table.csv
   - HS_disease_1st_pass_meta_table.csv
```

**Enhanced Features:**
- Step-by-step workflow validation with detailed progress tracking
- Quality assurance checks for data completeness and UMLS matching rates
- Statistics dashboard with comprehensive processing metrics
- Production readiness assessment with scoring system
- File integrity verification and validation
- Performance monitoring and resource usage tracking

**Usage:**
```bash
conda activate genoar
jupyter notebook test_first_pass_workflow.ipynb
```

**Requirements:**
- META files in `crawl_output/META/`
- UMLS data in `all_query_results/`
- Write permissions for output directory

**Output:**
- Creates `test_first_pass_output/` directory
- Generates production-ready first-pass tables
- Comprehensive validation and readiness reports

---

## Path Configuration

| What | Path | How it is set |
|------|------|----------|
| UMLS data | `{project_root}/all_query_results/` | **Fixed** (not configurable) |
| META data | User-supplied | `-m` / `--meta-dir` |
| Output directory | User-supplied | `-o` / `--output-dir` |

**Notes:**
- The UMLS data directory (`all_query_results/`) is discovered automatically from the project root
- The META directory and the output directory are set with CLI arguments
- If you pass neither, the defaults are `sample_crawl_output/META` and `test_first_pass_output`

---

## Running Tests

### Interactive Testing (Jupyter Notebooks)
```bash
conda activate genoar

# Start Jupyter and run notebooks interactively
jupyter notebook

# Then open:
# 1. integrated_functionality_test.ipynb - for comprehensive functionality testing
# 2. test_first_pass_workflow.ipynb - for end-to-end workflow validation
```

### Python Script Testing (CLI)

#### 1. First-pass workflow test (the whole pipeline)
```bash
# Default run (uses sample_crawl_output/META)
python test_first_pass_workflow.py

# Point at a specific META directory and output directory
python test_first_pass_workflow.py \
    --meta-dir ~/genoar/crawl_output/META \
    --output-dir ~/genoar/first_pass_output

# Same thing with the short options
python test_first_pass_workflow.py -m ~/genoar/crawl_output/META -o ~/genoar/output
```

#### 2. Basic module test (no Ground Truth needed)
```bash
# Default run
python test_basic_modules.py

# Point at a specific META directory
python test_basic_modules.py --meta-dir ~/genoar/crawl_output/META
```

#### 3. Preserved features test
```bash
# Default run
python test_preserved_features.py

# Point at a specific META directory
python test_preserved_features.py --meta-dir ~/genoar/crawl_output/META
```

### CLI options at a glance

| Script | Option | Meaning |
|----------|------|------|
| `test_first_pass_workflow.py` | `-m`, `--meta-dir` | Directory of META files |
| | `-o`, `--output-dir` | Output directory |
| `test_basic_modules.py` | `-m`, `--meta-dir` | Directory of META files |
| `test_preserved_features.py` | `-m`, `--meta-dir` | Directory of META files |

## Test Requirements

### Directory Structure
```
genoar/
├── crawl_output/META/          # META files (GSE*.txt)
├── all_query_results/          # UMLS tables (umls_*_df.csv)
├── genoar_analysis/            # Package source code
├── genoar_analysis_tests/      # Test suites (this directory)
└── [ground_truth_file.csv]     # Optional Ground Truth file
```

### Required Files

**Essential:**
- META files: `crawl_output/META/GSE*_meta.txt`
- UMLS tables: `all_query_results/umls_*_df.csv` (included in the repository)

**Optional:**
- Additional UMLS files for comprehensive testing
- Jupyter notebook for interactive execution

### Dependencies

All tests use the same dependencies as the main package:
- pandas
- numpy
- matplotlib
- matplotlib-venn
- pathlib
- logging

## Test Output

Each test provides:
- `[OK]` Success indicators for passing tests
- `[WARN]` Warnings for non-critical issues
- `[FAIL]` Error messages for failures
- `[STAT]` Statistics and metrics
- `[DONE]` Summary of results

## Troubleshooting

**Common Issues:**

1. **Import Errors:**
   - Ensure `conda activate genoar` is run
   - Check that parent directory is in Python path

2. **File Not Found:**
   - Verify META files exist in `crawl_output/META/`
   - Check UMLS files in `all_query_results/`
   - For Ground Truth tests, ensure GT file is accessible

3. **UMLS Integration:**
   - Tests will use mock data if UMLS files not found
   - For full testing, ensure UMLS CSV files are present

4. **Permission Errors:**
   - Ensure write permissions for output directories
   - Check disk space for CSV file generation

5. **Memory Issues:**
   - Large META files may require sufficient RAM
   - Consider testing with subset of files if needed

## Contributing

When adding new tests:
1. Follow the existing test structure
2. Use clear test names and documentation
3. Provide detailed error messages
4. Include success/failure indicators
5. Add appropriate requirements and usage instructions