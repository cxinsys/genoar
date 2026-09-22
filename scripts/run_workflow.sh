#!/bin/bash
# GENOAR Complete Workflow Execution Script

set -e

# Default parameters
PAGES=100
WORKERS=4
MODE="complete"
BUILD_IMAGE=false
USE_DOCKER=true
CLEAN=false
TEST=false

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

show_help() {
    cat << EOF
GENOAR Workflow Execution Script

Usage:
    ./run_workflow.sh [OPTIONS]

Options:
    -p, --pages N           Number of pages to crawl (default: 100)
    -w, --workers N         Number of parallel workers (default: 4)
    -m, --mode MODE         Execution mode: complete|crawl|analyze|test (default: complete)
    --build                 Build Docker image before running
    --native                Run natively (not in Docker container)
    --clean                 Clean previous outputs before running
    --test                  Run test suite after workflow
    -h, --help              Show this help message

Modes:
    complete    - Run complete workflow (crawl + analyze)
    crawl       - Run only crawling phase
    analyze     - Run only analysis phase (requires crawl data)
    test        - Run test suite only

Examples:
    # Run complete workflow with default settings
    ./run_workflow.sh

    # Run with custom parameters
    ./run_workflow.sh --pages 50 --workers 2 --mode complete

    # Run only crawling phase
    ./run_workflow.sh --mode crawl --pages 200

    # Run only analysis (requires existing crawl data)
    ./run_workflow.sh --mode analyze

    # Run in native mode (no Docker)
    ./run_workflow.sh --native --mode test

    # Clean and rebuild
    ./run_workflow.sh --clean --build --test

Environment Variables:
    GENOAR_DATA_DIR         - Base data directory (default: current directory)
    GENOAR_LOG_LEVEL        - Logging level (INFO, DEBUG, WARNING, ERROR)
    SELENIUM_HEADLESS       - Force headless browser mode (true/false)
EOF
}

check_prerequisites() {
    print_status "Checking prerequisites..."
    
    if [ "$USE_DOCKER" = true ]; then
        if ! command -v docker &> /dev/null; then
            print_error "Docker is not installed or not in PATH"
            exit 1
        fi
        
        if ! command -v docker-compose &> /dev/null; then
            print_error "Docker Compose is not installed or not in PATH"
            exit 1
        fi
        
        print_success "Docker and Docker Compose found"
    else
        # Check native dependencies
        if ! command -v python3 &> /dev/null; then
            print_error "Python 3 is not installed or not in PATH"
            exit 1
        fi
        
        if ! command -v snakemake &> /dev/null; then
            print_warning "Snakemake not found, installing..."
            pip install snakemake
        fi
        
        print_success "Native dependencies found"
    fi
}

build_image() {
    if [ "$BUILD_IMAGE" = true ]; then
        print_status "Building Docker image..."
        docker build -t genoar:latest .
        print_success "Docker image built successfully"
    fi
}

clean_outputs() {
    if [ "$CLEAN" = true ]; then
        print_status "Cleaning previous outputs..."
        
        # Create backup if data exists
        if [ -d "crawl_output" ] && [ "$(ls -A crawl_output)" ]; then
            backup_dir="backup_$(date +%Y%m%d_%H%M%S)"
            print_status "Creating backup: $backup_dir"
            mkdir -p "$backup_dir"
            cp -r crawl_output first_pass_output logs "$backup_dir/" 2>/dev/null || true
        fi
        
        # Clean directories
        rm -rf crawl_output/* first_pass_output/* logs/* workflow_output/* 2>/dev/null || true
        mkdir -p crawl_output/META crawl_output/SMTX crawl_output/SRR
        mkdir -p first_pass_output logs workflow_output
        
        print_success "Cleanup completed"
    fi
}

run_docker_workflow() {
    print_status "Running Docker-based workflow..."
    
    case $MODE in
        complete)
            print_status "Starting complete workflow (crawl + analyze)..."
            docker-compose run --rm genoar workflow --pages $PAGES --workers $WORKERS
            ;;
        crawl)
            print_status "Starting crawling phase only..."
            docker-compose --profile crawler run --rm genoar-crawler crawl $PAGES $WORKERS
            ;;
        analyze)
            print_status "Starting analysis phase only..."
            docker-compose --profile analysis run --rm genoar-analysis analyze
            ;;
        test)
            print_status "Running test suite..."
            docker-compose --profile test run --rm genoar-test test
            ;;
        *)
            print_error "Unknown mode: $MODE"
            exit 1
            ;;
    esac
}

run_native_workflow() {
    print_status "Running native workflow..."
    
    # Ensure Python path is set
    export PYTHONPATH="$(pwd):$PYTHONPATH"
    
    case $MODE in
        complete)
            print_status "Starting complete workflow with Snakemake..."
            snakemake --configfile workflow/config.yaml --cores $WORKERS
            ;;
        crawl)
            print_status "Starting crawling phase..."
            cd genoar_crawler
            if [ $WORKERS -gt 1 ]; then
                bash run_parallel_crawl.sh $WORKERS $PAGES
            else
                python genoar_crawler.py $PAGES 1 true
            fi
            python integration.py
            cd ..
            ;;
        analyze)
            print_status "Starting analysis phase..."
            python -c "
from genoar_analysis.pipelines.first_pass_pipeline import create_first_pass_tables
results = create_first_pass_tables(
    meta_dir='crawl_output/META',
    umls_data_dir='all_query_results',
    output_dir='first_pass_output'
)
print('Analysis completed successfully')
"
            ;;
        test)
            print_status "Running test suite..."
            python genoar_analysis/genoar_analysis_tests/test_basic_modules.py
            python genoar_analysis/genoar_analysis_tests/test_preserved_features.py
            python genoar_analysis/genoar_analysis_tests/test_first_pass_workflow.py
            ;;
        *)
            print_error "Unknown mode: $MODE"
            exit 1
            ;;
    esac
}

run_tests() {
    if [ "$TEST" = true ]; then
        print_status "Running test suite..."
        if [ "$USE_DOCKER" = true ]; then
            docker-compose --profile test run --rm genoar-test test
        else
            python genoar_analysis/genoar_analysis_tests/test_basic_modules.py
            python genoar_analysis/genoar_analysis_tests/test_preserved_features.py
            python genoar_analysis/genoar_analysis_tests/test_first_pass_workflow.py
        fi
        print_success "All tests completed"
    fi
}

show_summary() {
    print_status "Workflow Summary"
    echo "===================="
    
    if [ -f "logs/workflow/workflow_summary.json" ]; then
        cat logs/workflow/workflow_summary.json
    elif [ -f "first_pass_output/HS_cell_type_1st_pass_meta_table.csv" ]; then
        echo "Output files created:"
        ls -la first_pass_output/*.csv 2>/dev/null || echo "No output files found"
    else
        print_warning "No summary data available"
    fi
    
    echo "===================="
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--pages)
            PAGES="$2"
            shift 2
            ;;
        -w|--workers)
            WORKERS="$2"
            shift 2
            ;;
        -m|--mode)
            MODE="$2"
            shift 2
            ;;
        --build)
            BUILD_IMAGE=true
            shift
            ;;
        --native)
            USE_DOCKER=false
            shift
            ;;
        --clean)
            CLEAN=true
            shift
            ;;
        --test)
            TEST=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Main execution
main() {
    print_status "GENOAR Workflow Execution Starting..."
    print_status "Mode: $MODE, Pages: $PAGES, Workers: $WORKERS, Docker: $USE_DOCKER"
    
    # Run workflow steps
    check_prerequisites
    clean_outputs
    build_image
    
    # Execute the workflow
    if [ "$USE_DOCKER" = true ]; then
        run_docker_workflow
    else
        run_native_workflow
    fi
    
    # Run tests if requested
    run_tests
    
    # Show summary
    show_summary
    
    print_success "GENOAR workflow completed successfully!"
}

# Execute main function
main