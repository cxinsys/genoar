#!/bin/bash
# GENOAR Docker Container Entrypoint Script
#
# NOTE: archived. The image's real entry point is workflow/run.sh (Dockerfile
# ENTRYPOINT and the docker-compose command both point at it). Kept in sync
# with run.sh's display handling so it stays usable if run by hand.

set -e

# Display state, shared by setup_display() and cleanup().
XVFB_DISPLAY_NUM=${XVFB_DISPLAY_NUM:-99}
XVFB_LOCK="/tmp/.X${XVFB_DISPLAY_NUM}-lock"
XVFB_SOCKET="/tmp/.X11-unix/X${XVFB_DISPLAY_NUM}"
XVFB_PID=""        # set only when this script is the one that started Xvfb
CLEANUP_DONE=0

# Runs on every exit. Idempotent on purpose: a second Ctrl+C while a graceful
# shutdown is in progress must not re-enter this and must not leave a lock
# behind. Signals are ignored for the duration.
cleanup() {
    local rc=$?
    local waited=0

    trap '' INT TERM HUP EXIT
    set +e

    if [ "$CLEANUP_DONE" -eq 1 ]; then
        exit "$rc"
    fi
    CLEANUP_DONE=1

    if [ -n "$XVFB_PID" ]; then
        if kill -0 "$XVFB_PID" 2>/dev/null; then
            echo "Stopping Xvfb (PID $XVFB_PID)..."
            kill -TERM "$XVFB_PID" 2>/dev/null
            while kill -0 "$XVFB_PID" 2>/dev/null && [ "$waited" -lt 5 ]; do
                sleep 1
                waited=$((waited + 1))
            done
            kill -KILL "$XVFB_PID" 2>/dev/null
        fi
        wait "$XVFB_PID" 2>/dev/null

        # Xvfb normally removes its own lock. If it was killed before it could,
        # take the lock with us so the next run does not trip over it.
        if [ -e "$XVFB_LOCK" ] && ! x_display_is_live; then
            rm -f "$XVFB_LOCK" "$XVFB_SOCKET"
            echo "Removed our leftover X lock $XVFB_LOCK"
        fi
        XVFB_PID=""
    fi

    exit "$rc"
}

on_interrupt() {
    trap '' INT TERM
    echo ""
    echo "Interrupted; shutting down. Further interrupts are ignored until cleanup finishes."
    exit 130
}

trap cleanup EXIT
trap on_interrupt INT TERM

show_help() {
    cat << EOF
GENOAR Complete Workflow Container

Usage:
    docker run genoar [COMMAND] [OPTIONS]

Commands:
    crawl [PAGES] [WORKERS]     - Run crawling pipeline
    analyze                     - Run analysis pipeline only (requires crawl data)
    workflow                    - Run complete workflow (crawl + analyze)
    test                        - Run test suite
    bash                        - Interactive shell
    --help                      - Show this help

Options:
    --pages N                   - Number of pages to crawl (default: 100)
    --workers N                 - Number of parallel workers (default: 4)
    --headless                  - Run in headless mode (default: true in container)
    --output-dir DIR            - Output directory (default: /app/workflow_output)

Examples:
    # Run complete workflow with 50 pages and 2 workers
    docker run genoar workflow --pages 50 --workers 2
    
    # Run only crawling
    docker run genoar crawl 100 4
    
    # Run only analysis (requires existing crawl data)
    docker run genoar analyze
    
    # Interactive mode
    docker run -it genoar bash

Environment Variables:
    SELENIUM_HEADLESS=true      - Force headless mode
    SNAKEMAKE_CORES=4           - Number of cores for Snakemake
    UMLS_DATA_DIR               - Directory for UMLS data (default: /app/all_query_results)
EOF
}

# The PID an X lock file records (11 right-aligned digits), or empty if the
# file is missing or unreadable.
x_lock_owner_pid() {
    [ -f "$XVFB_LOCK" ] || return 0
    tr -cd '0-9' < "$XVFB_LOCK" 2>/dev/null || true
}

# Succeeds when a real X server is behind the display. Two independent
# signals, because either one can be absent: the recorded owner is still
# alive, or the display answers a query.
x_display_is_live() {
    local pid
    pid=$(x_lock_owner_pid)

    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi

    if command -v xdpyinfo >/dev/null 2>&1 &&
       xdpyinfo -display ":${XVFB_DISPLAY_NUM}" >/dev/null 2>&1; then
        return 0
    fi

    return 1
}

# Xvfb refuses to start with "Server is already active for display 99" when
# /tmp/.X99-lock is left behind, which is what happens if a previous run was
# interrupted midway through its own cleanup. Remove such a lock only when we
# can show nothing is behind it: deleting a running server's lock would be
# worse than the bug.
clear_stale_x_lock() {
    local pid

    if [ ! -e "$XVFB_LOCK" ]; then
        return 0
    fi

    if x_display_is_live; then
        echo "Keeping $XVFB_LOCK: a live X server owns display :${XVFB_DISPLAY_NUM}"
        return 1
    fi

    pid=$(x_lock_owner_pid)
    if [ -n "$pid" ]; then
        echo "Stale X lock $XVFB_LOCK: owner PID $pid is gone and :${XVFB_DISPLAY_NUM} does not answer"
    else
        echo "Stale X lock $XVFB_LOCK: no owner PID recorded and :${XVFB_DISPLAY_NUM} does not answer"
    fi

    rm -f "$XVFB_LOCK"
    echo "Removed stale lock $XVFB_LOCK"

    if [ -e "$XVFB_SOCKET" ]; then
        rm -f "$XVFB_SOCKET"
        echo "Removed stale socket $XVFB_SOCKET"
    fi

    return 0
}

setup_display() {
    local waited=0

    if [ -n "$DISPLAY" ] && [ "$SELENIUM_HEADLESS" != "true" ]; then
        return 0
    fi

    if x_display_is_live; then
        echo "Display :${XVFB_DISPLAY_NUM} is already served by a live X server; reusing it"
        export DISPLAY=":${XVFB_DISPLAY_NUM}"
        return 0
    fi

    clear_stale_x_lock || true

    echo "Setting up virtual display on :${XVFB_DISPLAY_NUM}..."
    Xvfb ":${XVFB_DISPLAY_NUM}" -ac -screen 0 1280x1024x24 > /dev/null 2>&1 &
    XVFB_PID=$!
    export DISPLAY=":${XVFB_DISPLAY_NUM}"

    while [ "$waited" -lt 10 ]; do
        if ! kill -0 "$XVFB_PID" 2>/dev/null; then
            echo "Error: Xvfb (PID $XVFB_PID) died while starting on :${XVFB_DISPLAY_NUM}" >&2
            XVFB_PID=""
            return 1
        fi
        if [ -e "$XVFB_LOCK" ]; then
            echo "Virtual display :${XVFB_DISPLAY_NUM} ready (Xvfb PID $XVFB_PID)"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done

    echo "Error: Xvfb did not create $XVFB_LOCK within ${waited}s" >&2
    return 1
}

run_crawl() {
    local pages=${1:-100}
    local workers=${2:-4}
    
    echo "Starting GENOAR crawling pipeline..."
    echo "Pages: $pages, Workers: $workers"
    
    setup_display
    
    cd /app/genoar_crawler
    
    # Run the crawler
    if [ "$workers" -gt 1 ]; then
        echo "Running parallel crawling with $workers workers"
        ./run_docker_parallel.sh $workers $pages linux
    else
        echo "Running single-threaded crawling"
        python genoar_crawler.py $pages 1 true
    fi
    
    # Run integration analysis
    echo "Running integration analysis..."
    python integration.py
    
    echo "Crawling pipeline completed!"
}

run_analysis() {
    echo "Starting GENOAR analysis pipeline..."
    
    cd /app
    
    # Check if crawl data exists
    if [ ! -d "crawl_output/META" ] || [ -z "$(ls -A crawl_output/META)" ]; then
        echo "Error: No crawl data found. Please run crawling first."
        exit 1
    fi
    
    # The three UMLS tables by name: a directory that merely exists, or holds
    # something else, is not the input.
    for name in umls_celltype_df umls_tissue_df umls_disease_df; do
        if [ ! -s "all_query_results/$name.csv" ]; then
            echo "Error: all_query_results/$name.csv is missing. The UMLS tables ship" >&2
            echo "with the repository (README, UMLS Reference Data); mount" >&2
            echo "all_query_results/ into the container." >&2
            exit 1
        fi
    done
    
    # Run the analysis pipeline using Snakemake
    echo "Running Snakemake workflow..."
    snakemake --cores $SNAKEMAKE_CORES --configfile workflow/config.yaml
    
    echo "Analysis pipeline completed!"
}

run_complete_workflow() {
    local pages=${1:-100}
    local workers=${2:-4}
    
    echo "Starting complete GENOAR workflow..."
    
    # Run crawling
    run_crawl $pages $workers
    
    # Copy crawl results to main directory
    if [ -d "/app/genoar_crawler/crawl_output" ]; then
        echo "Copying crawl results..."
        cp -r /app/genoar_crawler/crawl_output/* /app/crawl_output/ || true
    fi
    
    # Run analysis
    run_analysis
    
    echo "Complete workflow finished!"
    echo "Results available in:"
    echo "  - /app/crawl_output/ (crawl results)"
    echo "  - /app/first_pass_output/ (analysis results)"
}

run_tests() {
    echo "Running GENOAR test suite..."
    
    cd /app
    
    # Ensure conda environment is set up (if available)
    if command -v conda &> /dev/null; then
        echo "Conda detected, but using system Python in container"
    fi
    
    # Run all test scripts
    echo "Running basic modules test..."
    python genoar_analysis/genoar_analysis_tests/test_basic_modules.py

    echo "Running preserved features test..."
    python genoar_analysis/genoar_analysis_tests/test_preserved_features.py

    echo "Running first-pass workflow test..."
    python genoar_analysis/genoar_analysis_tests/test_first_pass_workflow.py
    
    echo "All tests completed!"
}

# Parse command line arguments
case "$1" in
    crawl)
        shift
        pages="$1"
        workers="$2"
        run_crawl "$pages" "$workers"
        ;;
    analyze)
        run_analysis
        ;;
    workflow)
        shift
        # Parse additional options
        pages=100
        workers=4
        while [[ $# -gt 0 ]]; do
            case $1 in
                --pages)
                    pages="$2"
                    shift 2
                    ;;
                --workers)
                    workers="$2"
                    shift 2
                    ;;
                --headless)
                    export SELENIUM_HEADLESS=true
                    shift
                    ;;
                --output-dir)
                    output_dir="$2"
                    shift 2
                    ;;
                *)
                    echo "Unknown option: $1"
                    show_help
                    exit 1
                    ;;
            esac
        done
        run_complete_workflow "$pages" "$workers"
        ;;
    test)
        run_tests
        ;;
    bash)
        exec /bin/bash
        ;;
    --help|-h)
        show_help
        ;;
    *)
        if [ $# -eq 0 ]; then
            show_help
        else
            echo "Unknown command: $1"
            show_help
            exit 1
        fi
        ;;
esac