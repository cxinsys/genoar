#!/bin/bash
# GENOAR unified pipeline runner.
#
# This is the live entry point for the container: it is both the Dockerfile
# ENTRYPOINT and the docker-compose `command`. scripts/entrypoint.sh is an
# archived copy (see scripts/README.md) and is not what runs.

set -euo pipefail

# ---------------------------------------------------------------------------
# Exit codes
#
# Same contract as genoar_crawler.py, so that "nothing to do" stays
# distinguishable from both success and failure all the way up to the caller:
#   0  work was done
#   1  failure
#   2  invalid arguments / invalid page range
#   3  the run was valid but there was nothing to process, or nothing came out
# ---------------------------------------------------------------------------
readonly EXIT_OK=0
readonly EXIT_FAILURE=1
readonly EXIT_BAD_ARGS=2
readonly EXIT_NO_DATA=3

# Not one of the codes above: this is what the first-pass stage's embedded
# Python reports when the UMLS export is absent, so the caller can name the
# missing input instead of reporting a generic analysis failure.
readonly MISSING_UMLS_INPUT=4

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Logging helpers
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MODE=${MODE:-complete}
PAGES=${PAGES:-100}
WORKERS=${WORKERS:-1}
LOG_LEVEL=${LOG_LEVEL:-INFO}
SELENIUM_HEADLESS=${SELENIUM_HEADLESS:-false}

# GENOAR_APP_DIR only exists so the script can be exercised outside the
# container; in the image it is always /app.
APP_DIR=${GENOAR_APP_DIR:-/app}
CRAWL_OUTPUT_DIR="$APP_DIR/crawl_output"
UMLS_DIR="$APP_DIR/all_query_results"
FIRST_PASS_DIR="$APP_DIR/first_pass_output"

# Worst outcome seen so far, and the sentence that explains it.
STATUS=$EXIT_OK
STATUS_MESSAGE="GENOAR pipeline completed"

# Set once the analysis stage has actually run, so the summary knows whether
# empty first-pass tables mean anything.
ANALYSIS_RAN=0

# Severity order for record_status: ok < no data < bad args < failure.
status_rank() {
    case "$1" in
        "$EXIT_OK") echo 0 ;;
        "$EXIT_NO_DATA") echo 1 ;;
        "$EXIT_BAD_ARGS") echo 2 ;;
        *) echo 3 ;;
    esac
}

# Remember an outcome. The worst one wins, so a late success can never paper
# over an earlier failure.
record_status() {
    local code=$1
    local message=$2

    if [ "$(status_rank "$code")" -gt "$(status_rank "$STATUS")" ]; then
        STATUS=$code
        STATUS_MESSAGE=$message
    fi
}

# ---------------------------------------------------------------------------
# Virtual display (Xvfb)
#
# Xvfb refuses to start with "Server is already active for display 99" when
# /tmp/.X99-lock is left behind, which is what happens if a previous run was
# interrupted midway through its own cleanup. We remove such a lock only when
# we can show nothing is actually behind it: deleting the lock of a running
# server would be worse than the bug.
# ---------------------------------------------------------------------------
XVFB_DISPLAY_NUM=${XVFB_DISPLAY_NUM:-99}
XVFB_LOCK="/tmp/.X${XVFB_DISPLAY_NUM}-lock"
XVFB_SOCKET="/tmp/.X11-unix/X${XVFB_DISPLAY_NUM}"
XVFB_PID=""        # set only when this script is the one that started Xvfb
CLEANUP_DONE=0

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

# Remove the lock (and the socket that goes with it) only if it is stale.
clear_stale_x_lock() {
    local pid

    if [ ! -e "$XVFB_LOCK" ]; then
        return 0
    fi

    if x_display_is_live; then
        log_info "Keeping $XVFB_LOCK: a live X server owns display :${XVFB_DISPLAY_NUM}"
        return 1
    fi

    pid=$(x_lock_owner_pid)
    if [ -n "$pid" ]; then
        log_warning "Stale X lock $XVFB_LOCK: owner PID $pid is gone and :${XVFB_DISPLAY_NUM} does not answer"
    else
        log_warning "Stale X lock $XVFB_LOCK: no owner PID recorded and :${XVFB_DISPLAY_NUM} does not answer"
    fi

    rm -f "$XVFB_LOCK"
    log_info "Removed stale lock $XVFB_LOCK"

    if [ -e "$XVFB_SOCKET" ]; then
        rm -f "$XVFB_SOCKET"
        log_info "Removed stale socket $XVFB_SOCKET"
    fi

    return 0
}

start_display() {
    local waited=0

    if x_display_is_live; then
        log_info "Display :${XVFB_DISPLAY_NUM} is already served by a live X server; reusing it"
        export DISPLAY=":${XVFB_DISPLAY_NUM}"
        return 0
    fi

    clear_stale_x_lock || true

    log_info "Starting virtual display on :${XVFB_DISPLAY_NUM}..."
    Xvfb ":${XVFB_DISPLAY_NUM}" -ac -screen 0 1280x1024x16 &
    XVFB_PID=$!
    export DISPLAY=":${XVFB_DISPLAY_NUM}"

    while [ "$waited" -lt 10 ]; do
        if ! kill -0 "$XVFB_PID" 2>/dev/null; then
            log_error "Xvfb (PID $XVFB_PID) died while starting on :${XVFB_DISPLAY_NUM}"
            XVFB_PID=""
            return 1
        fi
        if [ -e "$XVFB_LOCK" ]; then
            log_success "Virtual display :${XVFB_DISPLAY_NUM} ready (Xvfb PID $XVFB_PID)"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done

    log_error "Xvfb did not create $XVFB_LOCK within ${waited}s"
    return 1
}

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
            log_info "Stopping Xvfb (PID $XVFB_PID)..."
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
            log_info "Removed our leftover X lock $XVFB_LOCK"
        fi
        XVFB_PID=""
    fi

    exit "$rc"
}

on_interrupt() {
    trap '' INT TERM
    echo ""
    log_warning "Interrupted; shutting down. Further interrupts are ignored until cleanup finishes."
    exit 130
}

trap cleanup EXIT
trap on_interrupt INT TERM

# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

# Turn a crawler exit code into a sentence and remember it. Returns the code so
# callers can stop the pipeline.
handle_crawler_exit() {
    local rc=$1

    case "$rc" in
        "$EXIT_OK")
            log_success "Crawling finished"
            ;;
        "$EXIT_NO_DATA")
            record_status "$EXIT_NO_DATA" \
                "Crawler had nothing to do: no page fell in the requested range (pages=$PAGES)"
            log_warning "Crawler reported nothing to do (exit $rc)"
            ;;
        "$EXIT_BAD_ARGS")
            record_status "$EXIT_BAD_ARGS" \
                "Crawler rejected its arguments (pages=$PAGES, workers=$WORKERS)"
            log_error "Crawler rejected its arguments (exit $rc)"
            ;;
        4)
            # run_parallel_crawl.sh only: every worker finished cleanly but not
            # one file came back. Empty, not broken - keep the two apart.
            record_status "$EXIT_NO_DATA" \
                "Crawl workers finished cleanly but collected no files"
            log_warning "Crawl workers finished cleanly but collected no files (exit $rc)"
            ;;
        *)
            record_status "$EXIT_FAILURE" "Crawling failed (exit $rc)"
            log_error "Crawling failed (exit $rc)"
            ;;
    esac

    return "$rc"
}

run_crawl_stage() {
    local rc=0

    if ! cd "$APP_DIR/genoar_crawler"; then
        record_status "$EXIT_FAILURE" "Crawler directory $APP_DIR/genoar_crawler is missing"
        log_error "Crawler directory $APP_DIR/genoar_crawler is missing"
        return 1
    fi

    if [ "$WORKERS" -gt 1 ]; then
        log_info "Running parallel crawl ($WORKERS workers)"
        bash run_parallel_crawl.sh "$WORKERS" "$PAGES" "$CRAWL_OUTPUT_DIR" || rc=$?
    else
        log_info "Running single-worker crawl"
        python genoar_crawler.py 1 "$PAGES" true -o "$CRAWL_OUTPUT_DIR" || rc=$?
    fi

    handle_crawler_exit "$rc"
}

run_integration_stage() {
    local rc=0

    log_info "Merging crawled data..."
    if ! cd "$APP_DIR/genoar_crawler"; then
        record_status "$EXIT_FAILURE" "Crawler directory $APP_DIR/genoar_crawler is missing"
        log_error "Crawler directory $APP_DIR/genoar_crawler is missing"
        return 1
    fi
    python integration.py "$CRAWL_OUTPUT_DIR" || rc=$?

    if [ "$rc" -ne 0 ]; then
        record_status "$EXIT_FAILURE" "Data integration failed (exit $rc)"
        log_error "Data integration failed (exit $rc)"
    fi

    return "$rc"
}

# First-pass table generation. Exits 1 if any field failed, 3 if no table was
# produced at all; neither may be swallowed into a successful run.
run_first_pass_stage() {
    local rc=0

    log_info "Generating first-pass tables..."
    if ! cd "$APP_DIR"; then
        record_status "$EXIT_FAILURE" "Application directory $APP_DIR is missing"
        log_error "Application directory $APP_DIR is missing"
        return 1
    fi
    ANALYSIS_RAN=1

    GENOAR_META_DIR="$CRAWL_OUTPUT_DIR/META" \
    GENOAR_UMLS_DIR="$UMLS_DIR" \
    GENOAR_FIRST_PASS_DIR="$FIRST_PASS_DIR" \
    python - <<'PYTHON' || rc=$?
import os
import sys

from genoar_analysis.pipelines.first_pass_pipeline import create_first_pass_tables

meta_dir = os.environ['GENOAR_META_DIR']
umls_dir = os.environ['GENOAR_UMLS_DIR']
output_dir = os.environ['GENOAR_FIRST_PASS_DIR']

# The UMLS export is an input, not something a run may invent. A stand-in
# vocabulary would annotate the corpus against a handful of terms and report
# the result in the same table and the same row counts as a real run.
# The three tables by name; "directory not empty" is not the test.
tables = ('umls_celltype_df.csv', 'umls_tissue_df.csv', 'umls_disease_df.csv')
missing = [t for t in tables if not os.path.isfile(os.path.join(umls_dir, t))]
if missing:
    print(
        f'No UMLS tables in {umls_dir} (missing {", ".join(missing)}): they are '
        'a required input and the run cannot substitute one.',
        file=sys.stderr,
    )
    print(
        'They ship with the repository in all_query_results/ (mounted read-only '
        'at /app/all_query_results); see README, UMLS Reference Data.',
        file=sys.stderr,
    )
    sys.exit(4)

os.makedirs(output_dir, exist_ok=True)
results = create_first_pass_tables(meta_dir, umls_dir, output_dir)

failed = []
for field, df in sorted(results.items()):
    if df is None:
        print(f'{field}: table generation FAILED')
        failed.append(field)
    else:
        print(f'{field}: {len(df)} rows')

if failed:
    print('First-pass table generation failed for: ' + ', '.join(failed))
    sys.exit(1)

if not results:
    print('First-pass generated no tables at all')
    sys.exit(3)
PYTHON

    case "$rc" in
        "$EXIT_OK")
            log_success "First-pass tables generated"
            ;;
        "$EXIT_NO_DATA")
            record_status "$EXIT_NO_DATA" "First-pass analysis produced no tables"
            log_warning "First-pass analysis produced no tables (exit $rc)"
            ;;
        "$MISSING_UMLS_INPUT")
            record_status "$EXIT_FAILURE" "No UMLS tables in $UMLS_DIR: they are a required input"
            log_error "No UMLS tables in $UMLS_DIR. They ship with the repository in all_query_results/ (README, UMLS Reference Data)."
            ;;
        *)
            record_status "$EXIT_FAILURE" "First-pass analysis failed (exit $rc)"
            log_error "First-pass analysis failed (exit $rc)"
            ;;
    esac

    return "$rc"
}

# Run every test, then report. A failing test must not be hidden behind
# `|| true` while the run still claims success.
run_test_stage() {
    local failed=()
    local rc=0
    local name

    if ! cd "$APP_DIR"; then
        record_status "$EXIT_FAILURE" "Application directory $APP_DIR is missing"
        log_error "Application directory $APP_DIR is missing"
        return 1
    fi

    # Point the tests at this run's own data. Without these they fall back to
    # sample_crawl_output/, which is not in the repository, so every data-backed
    # check skips itself even directly after a successful crawl.
    export GENOAR_META_DIR="$CRAWL_OUTPUT_DIR/META"
    export GENOAR_UMLS_DIR="$UMLS_DIR"

    # test_sab_priority_selection needs no fixture, so it always really runs.
    for name in test_basic_modules test_preserved_features \
                test_first_pass_workflow test_sab_priority_selection; do
        log_info "Running $name..."
        rc=0
        python "genoar_analysis/genoar_analysis_tests/$name.py" || rc=$?
        if [ "$rc" -ne 0 ]; then
            log_error "$name failed (exit $rc)"
            failed+=("$name")
        fi
    done

    if [ "${#failed[@]}" -gt 0 ]; then
        record_status "$EXIT_FAILURE" "Test suite failed: ${failed[*]}"
        log_error "Failing tests: ${failed[*]}"
        return 1
    fi

    # Every data-backed test skips itself when its fixture is absent, which is
    # the right outcome but is not the same as having tested anything.
    log_success "All tests passed or skipped (no failures)"
    return 0
}

# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------
count_files() {
    local dir=$1
    local pattern=$2

    if [ ! -d "$dir" ]; then
        echo 0
        return 0
    fi

    { find "$dir" -name "$pattern" -type f 2>/dev/null || true; } | wc -l | tr -d '[:space:]'
}

count_csv_rows() {
    local file=$1

    if [ ! -f "$file" ]; then
        echo 0
        return 0
    fi

    { tail -n +2 "$file" 2>/dev/null || true; } | wc -l | tr -d '[:space:]'
}

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------
require_positive_int() {
    local name=$1
    local value=$2

    case "$value" in
        ''|*[!0-9]*)
            log_error "$name must be a positive integer, got '$value'"
            exit "$EXIT_BAD_ARGS"
            ;;
    esac

    if [ "$value" -lt 1 ]; then
        log_error "$name must be at least 1, got '$value'"
        exit "$EXIT_BAD_ARGS"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
log_info "Starting GENOAR pipeline"
log_info "Mode: $MODE, pages: $PAGES, workers: $WORKERS, log level: $LOG_LEVEL"

case "$MODE" in
    complete|crawl|analyze|test) ;;
    *)
        log_error "Unknown mode: $MODE"
        log_info "Available modes: complete, crawl, analyze, test"
        exit "$EXIT_BAD_ARGS"
        ;;
esac

require_positive_int PAGES "$PAGES"
require_positive_int WORKERS "$WORKERS"

if [ "$SELENIUM_HEADLESS" = "true" ]; then
    if ! start_display; then
        log_error "Could not bring up the virtual display; aborting before the browser is needed"
        exit "$EXIT_FAILURE"
    fi
fi

case "$MODE" in
    complete)
        log_info "Running the full pipeline (crawl + analyze)"

        if run_crawl_stage; then
            if run_integration_stage; then
                run_first_pass_stage || true
            else
                log_error "Skipping the analysis stage: data integration did not succeed"
            fi
        elif [ "$STATUS" -eq "$EXIT_NO_DATA" ]; then
            log_warning "Skipping the remaining stages: the crawl stage had nothing to do"
        else
            log_error "Skipping the remaining stages: the crawl stage did not succeed"
        fi
        ;;

    crawl)
        log_info "Running the crawl stage only"

        if run_crawl_stage; then
            run_integration_stage || true
        fi
        ;;

    analyze)
        log_info "Running the analysis stage only"

        if [ ! -d "$CRAWL_OUTPUT_DIR/META" ] || [ -z "$(ls -A "$CRAWL_OUTPUT_DIR/META" 2>/dev/null)" ]; then
            log_error "No crawled data found under $CRAWL_OUTPUT_DIR/META. Run the crawl stage first."
            exit "$EXIT_FAILURE"
        fi

        run_first_pass_stage || true
        ;;

    test)
        log_info "Running the test suite"
        run_test_stage || true
        ;;
esac

# ---------------------------------------------------------------------------
# Result summary
# ---------------------------------------------------------------------------
log_info "Building result summary..."

META_COUNT=$(count_files "$CRAWL_OUTPUT_DIR/META" "*.txt")
SMTX_COUNT=$(count_files "$CRAWL_OUTPUT_DIR/SMTX" "*.gz")
SRR_COUNT=$(count_files "$CRAWL_OUTPUT_DIR/SRR" "*.txt")

CELL_TYPE_ROWS=$(count_csv_rows "$FIRST_PASS_DIR/HS_cell_type_1st_pass_meta_table.csv")
TISSUE_ROWS=$(count_csv_rows "$FIRST_PASS_DIR/HS_tissue_1st_pass_meta_table.csv")
DISEASE_ROWS=$(count_csv_rows "$FIRST_PASS_DIR/HS_disease_1st_pass_meta_table.csv")

echo ""
echo "=============================="
echo "GENOAR pipeline results"
echo "=============================="
echo ""
echo "Crawled data:"
echo "  - META files: $META_COUNT"
echo "  - SMTX files: $SMTX_COUNT"
echo "  - SRR files:  $SRR_COUNT"
echo ""
echo "Analysis output:"
echo "  - Cell type samples: $CELL_TYPE_ROWS"
echo "  - Tissue samples:    $TISSUE_ROWS"
echo "  - Disease samples:   $DISEASE_ROWS"
echo ""
echo "=============================="

# An empty run is not a successful run. Both checks below only downgrade the
# status; they never upgrade a failure to success.
if [ "$MODE" = "complete" ] || [ "$MODE" = "crawl" ]; then
    if [ "$((META_COUNT + SMTX_COUNT + SRR_COUNT))" -eq 0 ]; then
        record_status "$EXIT_NO_DATA" "Crawling collected no files at all under $CRAWL_OUTPUT_DIR"
    elif [ "$SRR_COUNT" -eq 0 ]; then
        log_warning "No SRR accession lists were collected; stage 3 would have nothing to download"
    fi
fi

if [ "$ANALYSIS_RAN" -eq 1 ] && [ "$((CELL_TYPE_ROWS + TISSUE_ROWS + DISEASE_ROWS))" -eq 0 ]; then
    record_status "$EXIT_NO_DATA" "First-pass tables are empty: no sample matched any field"
fi

case "$STATUS" in
    "$EXIT_OK")
        log_success "$STATUS_MESSAGE"
        ;;
    "$EXIT_NO_DATA")
        log_warning "$STATUS_MESSAGE"
        log_warning "Exiting $EXIT_NO_DATA: the run was valid but produced nothing. This is not a success."
        ;;
    *)
        log_error "$STATUS_MESSAGE"
        log_error "Exiting $STATUS: the pipeline did NOT complete successfully."
        ;;
esac

exit "$STATUS"
