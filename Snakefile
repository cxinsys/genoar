# GENOAR Complete Workflow Snakemake Pipeline
# Automated pipeline from crawling to first-pass table generation

import os
import glob
from pathlib import Path

# Configuration
configfile: "workflow/config.yaml"

# Global variables
# config.yaml groups its settings, so read them by their nested path. Flat
# aliases would be a second place a directory could be changed.
CRAWL_OUTPUT = config["directories"]["crawl_output"]
UMLS_DATA = config["directories"]["umls_data"]
FIRST_PASS_OUTPUT = config["directories"]["first_pass_output"]
LOG_DIR = config["directories"]["logs"]

# Ensure output directories exist
os.makedirs(CRAWL_OUTPUT, exist_ok=True)
os.makedirs(f"{CRAWL_OUTPUT}/META", exist_ok=True)
os.makedirs(f"{CRAWL_OUTPUT}/SMTX", exist_ok=True)
os.makedirs(f"{CRAWL_OUTPUT}/SRR", exist_ok=True)
os.makedirs(FIRST_PASS_OUTPUT, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Target rule - defines the final outputs
rule all:
    input:
        # Integration and analysis outputs
        f"{CRAWL_OUTPUT}/integration_report.csv",
        f"{CRAWL_OUTPUT}/statistics_summary.json",
        # First-pass tables
        f"{FIRST_PASS_OUTPUT}/HS_cell_type_1st_pass_meta_table.csv",
        f"{FIRST_PASS_OUTPUT}/HS_tissue_1st_pass_meta_table.csv",
        f"{FIRST_PASS_OUTPUT}/HS_disease_1st_pass_meta_table.csv",
        # Workflow summary
        f"{LOG_DIR}/workflow_summary.json"

# Rule 1: Data Crawling
rule crawl_data:
    output:
        meta_done = f"{CRAWL_OUTPUT}/.crawl_completed",
        integration = f"{CRAWL_OUTPUT}/integration_report.csv",
        stats = f"{CRAWL_OUTPUT}/statistics_summary.json"
    params:
        # PAGES and WORKERS are the knobs make and docker-compose already pass
        # to the crawler, so honour them here too rather than making a
        # Snakemake run the one entry point that ignores them.
        pages = int(os.environ.get("PAGES") or config["crawling"]["pages"]),
        workers = int(os.environ.get("WORKERS") or config["crawling"]["workers"]),
        headless = str(config["crawling"]["headless"]).lower()
    log:
        f"{LOG_DIR}/crawl.log"
    shell:
        """
        echo "Starting data crawling..." > {log}
        
        cd genoar_crawler
        
        # Run parallel crawling if workers > 1
        if [ {params.workers} -gt 1 ]; then
            echo "Running parallel crawling with {params.workers} workers" >> {log}
            bash run_docker_parallel.sh {params.workers} {params.pages} linux >> {log} 2>&1
        else
            echo "Running single-threaded crawling" >> {log}
            python genoar_crawler.py 1 {params.pages} {params.headless} >> {log} 2>&1
        fi
        
        # Run integration analysis
        echo "Running integration analysis..." >> {log}
        python integration.py >> {log} 2>&1
        
        # Copy results to main crawl_output directory
        cp -r crawl_output/* ../{CRAWL_OUTPUT}/ || true
        
        # Mark crawling as completed
        touch ../{output.meta_done}
        
        echo "Crawling completed successfully" >> {log}
        """

# Rule 2: Validate crawled data
rule validate_crawl:
    input:
        f"{CRAWL_OUTPUT}/.crawl_completed"
    output:
        validation = f"{LOG_DIR}/crawl_validation.txt"
    log:
        f"{LOG_DIR}/validation.log"
    shell:
        """
        echo "Validating crawled data..." > {log}
        
        # Count files in each directory
        meta_count=$(find {CRAWL_OUTPUT}/META -name "*.txt" | wc -l)
        smtx_count=$(find {CRAWL_OUTPUT}/SMTX -name "*.gz" | wc -l)
        srr_count=$(find {CRAWL_OUTPUT}/SRR -name "*.txt" | wc -l)
        
        echo "Validation Results:" > {output.validation}
        echo "META files: $meta_count" >> {output.validation}
        echo "SMTX files: $smtx_count" >> {output.validation}
        echo "SRR files: $srr_count" >> {output.validation}
        
        # Check if we have any data
        if [ $meta_count -eq 0 ]; then
            echo "ERROR: No META files found!" >> {output.validation}
            echo "ERROR: No META files found!" >> {log}
            exit 1
        else
            echo "SUCCESS: Crawled data validation passed" >> {output.validation}
            echo "SUCCESS: Crawled data validation passed" >> {log}
        fi
        """

# Rule 3: Check UMLS data availability
rule check_umls:
    input:
        f"{LOG_DIR}/crawl_validation.txt"
    output:
        umls_check = f"{LOG_DIR}/umls_check.txt"
    log:
        f"{LOG_DIR}/umls_check.log"
    shell:
        """
        echo "Checking UMLS data availability..." > {log}
        
        # The bundled CSVs under all_query_results/ are the concept
        # vocabulary every match in Stage 2 is drawn from. Fabricating a
        # stand-in here would let the run finish and report row counts for
        # tables built against a vocabulary the user never supplied, so a
        # missing or incomplete input stops the run instead.
        missing=""
        for name in umls_celltype_df umls_tissue_df umls_disease_df; do
            f="{UMLS_DATA}/$name.csv"
            if [ ! -s "$f" ]; then
                missing="$missing $f"
            fi
        done

        if [ -n "$missing" ]; then
            echo "ERROR: UMLS reference data missing or empty:$missing" | tee -a {log} >&2
            echo "Stage 2 matches concepts against these files; see the UMLS" >&2
            echo "Reference Data section of README.md for what they contain." >&2
            exit 1
        fi

        echo "UMLS data found:" > {output.umls_check}
        for name in celltype tissue disease; do
            f="{UMLS_DATA}/umls_${{name}}_df.csv"
            echo "$name entries: $(wc -l < "$f")" >> {output.umls_check}
        done

        echo "UMLS check completed" >> {log}
        """

# Rule 4: Generate first-pass tables
rule generate_first_pass_tables:
    input:
        umls_check = f"{LOG_DIR}/umls_check.txt",
        crawl_done = f"{CRAWL_OUTPUT}/.crawl_completed"
    output:
        cell_type = f"{FIRST_PASS_OUTPUT}/HS_cell_type_1st_pass_meta_table.csv",
        tissue = f"{FIRST_PASS_OUTPUT}/HS_tissue_1st_pass_meta_table.csv",
        disease = f"{FIRST_PASS_OUTPUT}/HS_disease_1st_pass_meta_table.csv"
    log:
        f"{LOG_DIR}/first_pass.log"
    shell:
        """
        echo "Generating first-pass tables..." > {log}
        
        # Imported as the installed package, from the working directory the
        # rest of this rule uses. genoar_analysis imports its own submodules
        # package-relatively, so reaching in at genoar_analysis/pipelines
        # instead - a cd and a sys.path entry - leaves those imports one level
        # above the top-level package and the rule dies on the first import.
        python -c "
from genoar_analysis.pipelines.first_pass_pipeline import create_first_pass_tables

# Create first-pass tables
results = create_first_pass_tables(
    meta_dir='{CRAWL_OUTPUT}/META',
    umls_data_dir='{UMLS_DATA}',
    output_dir='{FIRST_PASS_OUTPUT}'
)

# Log results
for field, result in results.items():
    if result is not None:
        print(f'Generated {{field}} table: {{len(result)}} rows')
    else:
        print(f'Failed to generate {{field}} table')
" >> {log} 2>&1
        
        # Verify output files exist. A missing table means the analysis did
        # not produce it; standing in an empty one would satisfy `rule all` and
        # let the summary report a failed run as a completed one.
        for file in {output.cell_type} {output.tissue} {output.disease}; do
            if [ ! -f "$file" ]; then
                echo "ERROR: Output file $file not created" >> {log}
                exit 1
            fi
            rows=$(wc -l < "$file")
            echo "Generated $file with $rows rows" >> {log}
        done
        
        echo "First-pass table generation completed" >> {log}
        """

# Rule 5: Generate workflow summary
rule generate_summary:
    input:
        cell_type = f"{FIRST_PASS_OUTPUT}/HS_cell_type_1st_pass_meta_table.csv",
        tissue = f"{FIRST_PASS_OUTPUT}/HS_tissue_1st_pass_meta_table.csv",
        disease = f"{FIRST_PASS_OUTPUT}/HS_disease_1st_pass_meta_table.csv",
        integration = f"{CRAWL_OUTPUT}/integration_report.csv"
    output:
        summary = f"{LOG_DIR}/workflow_summary.json"
    log:
        f"{LOG_DIR}/summary.log"
    shell:
        """
        echo "Generating workflow summary..." > {log}
        
        # Count records in each output file
        cell_type_count=$([ -f "{input.cell_type}" ] && tail -n +2 "{input.cell_type}" | wc -l || echo "0")
        tissue_count=$([ -f "{input.tissue}" ] && tail -n +2 "{input.tissue}" | wc -l || echo "0")
        disease_count=$([ -f "{input.disease}" ] && tail -n +2 "{input.disease}" | wc -l || echo "0")
        
        # Count META files
        meta_count=$(find {CRAWL_OUTPUT}/META -name "*.txt" | wc -l)
        
        # Get total samples from integration report if available
        total_samples=0
        if [ -f "{input.integration}" ]; then
            total_samples=$(tail -n +2 "{input.integration}" | wc -l)
        fi
        
        # Create JSON summary
        cat > {output.summary} << EOF
{{
    "workflow_completion_time": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "input_data": {{
        "total_gse_series": $meta_count,
        "total_samples": $total_samples
    }},
    "first_pass_tables": {{
        "cell_type_samples": $cell_type_count,
        "tissue_samples": $tissue_count,
        "disease_samples": $disease_count
    }},
    "success_rates": {{
        "cell_type_match_rate": "$(echo "scale=2; $cell_type_count * 100 / $total_samples" | bc -l 2>/dev/null || echo "N/A")%",
        "tissue_match_rate": "$(echo "scale=2; $tissue_count * 100 / $total_samples" | bc -l 2>/dev/null || echo "N/A")%",
        "disease_match_rate": "$(echo "scale=2; $disease_count * 100 / $total_samples" | bc -l 2>/dev/null || echo "N/A")%"
    }},
    "output_files": [
        "{input.cell_type}",
        "{input.tissue}",
        "{input.disease}"
    ]
}}
EOF
        
        echo "Workflow summary generated successfully" >> {log}
        cat {output.summary} >> {log}
        """

# Additional utility rules
rule clean:
    shell:
        """
        echo "Cleaning up workflow outputs..."
        rm -rf {CRAWL_OUTPUT}/*
        rm -rf {FIRST_PASS_OUTPUT}/*
        rm -rf {LOG_DIR}/*
        echo "Cleanup completed"
        """

rule test_pipeline:
    shell:
        """
        echo "Running pipeline tests..."
        python genoar_analysis/genoar_analysis_tests/test_basic_modules.py
        python genoar_analysis/genoar_analysis_tests/test_preserved_features.py
        python genoar_analysis/genoar_analysis_tests/test_first_pass_workflow.py
        echo "All tests completed successfully"
        """