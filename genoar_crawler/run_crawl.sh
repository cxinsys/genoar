#!/bin/bash

# GENOAR parallel crawling, interactive Docker launcher.
#
# Asks for a worker count and a page count, then runs one container per worker.
# The final verdict reflects the containers' real exit codes: if any worker
# failed, this script fails too.
#
# The image is picked by host architecture. This repository ships two crawler
# images - Dockerfile.amd64 (Google Chrome, x86_64) and Dockerfile.arm64
# (Chromium, ARM64) - and a developer Mac may be either one, so the OS name
# cannot choose between them but `uname -m` can. Both images set
# CHROME_BINARY_PATH and CHROME_DRIVER_PATH themselves, to different values;
# the crawler reads them, so this script must not override them.
# Use run_docker_parallel.sh when you need to force a specific architecture.
#
# Page splitting, worker identity and aggregation follow run_docker_parallel.sh
# exactly. They have to: computing pages/workers and nothing else gives three
# of four workers an end page of 0 when 1 page is asked for - the crawler's
# "crawl every page" sentinel - and starts three simultaneous full crawls of
# GEO.
#
# Whether the run covered its range is decided in one place for all three
# launchers: the aggregator. This script writes the same run.json - requested
# range, distributed end, the probed page count - and reads the same exit code
# back, so a run that stopped short of GEO's corpus is refused here exactly as
# it is there. There is no second completion judgement in this file to keep in
# step with it.
#
# Container naming and cleanup follow run_docker_parallel.sh exactly too, and
# for the same reason: a name like geo-worker-<n> is shared by every run on the
# host, so opening with
#     docker ps -aq --filter name=geo-worker  ->  docker stop / docker rm
# stops and removes the live workers of any crawl already under way. Containers
# are named genoar-worker-<run_id>-<n> and labelled genoar.run=<run_id>, and
# nothing here touches a container without that label.
#
# Environment:
#   GENOAR_RUN_ID              name this run (default: a fresh timestamped id)
#   GENOAR_RECLAIM_RUN=1       discard the containers an earlier run of the same
#                              id left behind when it left no run record.
#                              It never removes a run record.
#   GENOAR_RESUME=1            refused (exit 2). This launcher cannot resume a
#                              run; run_parallel_crawl.sh can. Ignoring it would
#                              start a fresh crawl under a flag that says the
#                              opposite.
#   GENOAR_TOTAL_PAGES=<n>     skip the corpus probe and use this page count
#   GENOAR_SKIP_PAGE_PROBE=1   skip the corpus probe and trust the answer given
#   GENOAR_MONITOR_INTERVAL    seconds between status updates (default 30)
#   GENOAR_WORKER_START_DELAY  seconds between worker launches (default 3)
#
# Exit status:
#   0  work was done, the run aggregated, and this run collected files
#   1  at least one worker failed, the run did not cover its range, the user
#      cancelled, or this run's directory could not be created at all - a
#      read-only tree or a full disk, which is not a run id being taken
#   2  invalid input, or a run of this id already exists on this host: its
#      containers are here, its record is here, or another launcher holds it
#   3  no worker failed, but no worker had anything to crawl either
#   4  workers finished cleanly yet this run collected not a single file
#
# "This run" is meant literally in 0 and 4, as in the other two launchers.
# crawl_output is persistent and shared: SMTX/SRR/META hold every run ever made
# into it, and must, since a new run may not erase an earlier one's results. So
# counting the files in it answered a different question. This script counted
# `*.gz` across the whole directory, which was wrong twice over - one leftover
# matrix from any earlier run reported a run that downloaded nothing as a
# success, and a run that collected only accession lists and metadata, which
# have no .gz at all, reported that it had collected nothing. The number that
# decides comes from the run manifest the aggregator writes.

set -e

echo "🚀 Starting GENOAR parallel crawl"

case "$(uname -m)" in
    arm64|aarch64) ARCH="arm64" ;;
    x86_64|amd64)  ARCH="amd64" ;;
    *)
        echo "❌ Unsupported architecture: $(uname -m)"
        echo "   Only amd64 and arm64 crawler images exist in this repository."
        exit 1
        ;;
esac

IMAGE_NAME="genoar:$ARCH"
DOCKERFILE="Dockerfile.$ARCH"
DOCKER_PLATFORM="linux/$ARCH"

if [ ! -f "$DOCKERFILE" ]; then
    echo "❌ $DOCKERFILE not found. Run this script from genoar_crawler/."
    exit 1
fi

echo "🔧 Architecture: $ARCH - image $IMAGE_NAME from $DOCKERFILE"

is_positive_int() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ "$1" -ge 1 ]
}

# GENOAR_RESUME=1 is refused here rather than ignored.
#
# Resuming means giving every worker back the slice it started on and letting it
# continue its own checkpoint. Only run_parallel_crawl.sh has a path that does
# that. Here the flag reached the end of the script without ever being read: a
# user who set it got a brand new crawl from page 1 and no indication that the
# thing they asked for had not happened. A flag whose name promises a
# continuation and whose effect is a fresh crawl is worse than one that is not
# supported, so this says so and stops - before the image build and the prompts,
# because the answer will not depend on them.
if [ "${GENOAR_RESUME:-}" = "1" ]; then
    echo "❌ GENOAR_RESUME=1 is set, and this launcher cannot resume a run."
    echo "   Resuming means handing every worker the slice it started on and"
    echo "   letting it continue its own checkpoint. Only run_parallel_crawl.sh"
    echo "   does that. Ignoring the flag here would start a fresh crawl from"
    echo "   page 1 while looking like a continuation, so this run stops."
    echo "   To continue a run:"
    echo "     GENOAR_RUN_ID=<id> GENOAR_RESUME=1 ./run_parallel_crawl.sh <workers> <pages>"
    echo "   To start a new run:  unset GENOAR_RESUME"
    exit 2
fi

# Build the image if it is not there yet. `docker images -q <name:tag>` prints
# an id or nothing; grepping the table for "name:tag" never matches, because
# the repository and the tag are separate columns.
if [ -z "$(docker images -q "$IMAGE_NAME" 2>/dev/null)" ]; then
    echo "🔧 Building the Docker image..."
    docker build --platform "$DOCKER_PLATFORM" -t "$IMAGE_NAME" -f "$DOCKERFILE" . --quiet
fi

# Ask for the worker count and page range
read -r -p "🔧 Number of workers [4]: " WORKERS
WORKERS=${WORKERS:-4}

read -r -p "📄 Pages to crawl [20]: " MAX_PAGES
MAX_PAGES=${MAX_PAGES:-20}

# Validate before any arithmetic: dividing by zero workers aborts the script
# with a message nobody can act on.
if ! is_positive_int "$WORKERS"; then
    echo "❌ Number of workers must be a positive integer (got: '$WORKERS')"
    exit 2
fi
if ! is_positive_int "$MAX_PAGES"; then
    echo "❌ Pages to crawl must be a positive integer (got: '$MAX_PAGES')"
    exit 2
fi
# Checked here rather than where it is used, so every input is validated before
# the run id is reserved. Reserving first and validating afterwards would refuse
# the run *after* it had claimed its id, and the corrected retry would then be
# turned away by the claim its own predecessor left behind.
if [ -n "${GENOAR_TOTAL_PAGES:-}" ] && ! is_positive_int "$GENOAR_TOTAL_PAGES"; then
    echo "❌ GENOAR_TOTAL_PAGES must be a positive integer (got: '$GENOAR_TOTAL_PAGES')"
    exit 2
fi

# The crawler's IDENTITY_PATTERN (genoar_crawler.py), as an ERE. Stage 3's
# run_docker_pipeline.sh carries the identical line on purpose: all three read
# the same GENOAR_RUN_ID, so all three must agree about what one is.
run_id_is_usable() {
    [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]
}

# Leading and trailing whitespace only, as the crawler's .strip() does. Inner
# whitespace is left alone so that "a b" is refused rather than quietly becoming
# a different id than the one the operator set.
trim_ws() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    printf %s "${s%"${s##*[![:space:]]}"}"
}

# runs/LATEST is Stage 3's newest-run pointer file. Nothing on this side writes
# it today, but both halves take their id from this one variable, and a rule a
# user meets in one half has to hold in the other. Case-sensitive, exactly as
# Stage 3 compares it, so 'latest' stays usable on both sides.
RUN_ID_RESERVED="LATEST"

# genoar_crawler.py's RUN_PLAN_FILE: the name of a run's plan, and so of its
# record. The aggregator refuses a run that has none.
RUN_PLAN_FILE="run.json"

# Validated before it is used to build a path. A character-set gate alone -
#     case "$RUN_ID" in *[!A-Za-z0-9._-]*|'') ... reject ;; esac
# - checks the character set and nothing else: no anchor on the first
# character, no length bound. It accepts '..', '.', '-rf', '.hidden' and an
# 80-character id, every one of which genoar_crawler.py refuses. The order is
# what makes that exploitable rather than merely inconsistent - RUN_DIR is built
# and mkdir -p'd here, before any Python runs, so GENOAR_RUN_ID=.. resolves the
# run directory onto the output directory itself and would create worker
# directories next to SMTX/, SRR/ and META/.
RUN_ID=$(trim_ws "${GENOAR_RUN_ID:-}")
if [ -z "$RUN_ID" ]; then
    # Unset, empty, or nothing but whitespace. The crawler's .strip() reads all
    # three as "no id was given", so this mints one rather than refusing.
    RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
fi
if ! run_id_is_usable "$RUN_ID"; then
    echo "❌ GENOAR_RUN_ID is not usable as a directory name: '$RUN_ID'"
    echo "   Start with a letter or digit, then letters, digits, '.', '_' or"
    echo "   '-', at most 64 characters. That is genoar_crawler.py's own rule"
    echo "   for GENOAR_RUN_ID, and Stage 3 applies the same one."
    echo "   The run directory is created here, before any Python sees the id,"
    echo "   which is why '..' and '.' must not get this far."
    echo "   Unset GENOAR_RUN_ID to let this run mint its own."
    exit 2
fi
if [ "$RUN_ID" = "$RUN_ID_RESERVED" ]; then
    echo "❌ GENOAR_RUN_ID='$RUN_ID' is reserved: Stage 3 keeps runs/$RUN_ID_RESERVED"
    echo "   as its newest-run pointer, and both stages read this one variable."
    echo "   Choose another id, or unset GENOAR_RUN_ID to mint one."
    exit 2
fi

# This run's containers, and the only ones this script will ever touch. A label
# is an exact key/value match; `--filter name=` is a substring match with no
# anchored form, which is why the old name filter reached every run on the host.
# The run id is in the name as well, because names are unique per host and two
# concurrent runs would otherwise both want "geo-worker-1".
RUN_LABEL="genoar.run=$RUN_ID"

worker_container_name() {
    printf 'genoar-worker-%s-%s' "$RUN_ID" "$1"
}

run_containers() {
    docker ps "$@" --filter "label=$RUN_LABEL" 2>/dev/null || true
}

# Refuse before anything is asked of the user or written to disk. Normally there
# is nothing here at all: a run id is minted per run, so only a hand-set
# GENOAR_RUN_ID can collide.
# Where this run's records live. Derived here, before either check below, so
# both can be answered before anything is created.
OUTPUT_DIR="$(pwd)/crawl_output"
RUN_DIR="$OUTPUT_DIR/runs/$RUN_ID"
WORKERS_DIR="$RUN_DIR/workers"
RUN_PLAN="$RUN_DIR/$RUN_PLAN_FILE"

# The refusal an existing record earns, written once because the reservation
# below can arrive at it too - when a run this one raced wrote its plan first.
refuse_recorded_run() {
    echo "❌ Run '$RUN_ID' already has a record here:"
    echo "     $RUN_PLAN"
    echo ""
    echo "   That file says what that run set out to do, and the aggregator"
    echo "   judges the run against it. Starting again under the same id would"
    echo "   overwrite it, so this run refuses rather than destroy it."
    echo "   No flag removes it: GENOAR_RECLAIM_RUN=1 discards leftover"
    echo "   containers, never a run record."
    echo "   Start a separate run with a fresh id (unset GENOAR_RUN_ID)."
    LEFTOVER_SAME_RUN=$(run_containers -aq)
    if [ -n "$LEFTOVER_SAME_RUN" ]; then
        echo ""
        echo "   That run's containers are still here too, and stay:"
        run_containers -a --format '     {{.Names}}  ({{.Status}})'
    fi
}

# Three conditions, asked in this order, and the order is the point. See the
# same block in run_docker_parallel.sh: most urgent first, the one with no way
# through second, and the one GENOAR_RECLAIM_RUN resolves last, so that flag is
# only ever suggested where setting it is the answer.
#
# All three read the host, and a read is a question about the past: between the
# answer and this run acting on it, another launcher can do anything. The
# reservation that settles that is one atomic step and comes after them, below.
echo "🔎 Checking whether run '$RUN_ID' already exists on this host..."

# 1. Live.
RUNNING_SAME_RUN=$(run_containers -q)
if [ -n "$RUNNING_SAME_RUN" ]; then
    echo "❌ Run '$RUN_ID' is already running here:"
    run_containers --format '     {{.Names}}  ({{.Status}})'
    echo ""
    echo "   Two runs sharing one id share their container names, their run"
    echo "   directory and their manifest. Refusing to start a second one."
    echo "   Let this script mint an id (unset GENOAR_RUN_ID), or wait."
    exit 2
fi

# 2. Recorded. run.json outlives the containers - `docker system prune` clears
# them and leaves it - so the container check alone left the record unprotected.
# No flag through: this script cannot resume, so proceeding would mean
# overwriting the record, and a run record is never overwritten.
if [ -e "$RUN_PLAN" ]; then
    refuse_recorded_run
    exit 2
fi

# 3. Containers but no record: nothing here is evidence of what a run did.
LEFTOVER_SAME_RUN=$(run_containers -aq)
if [ -n "$LEFTOVER_SAME_RUN" ]; then
    if [ "${GENOAR_RECLAIM_RUN:-}" = "1" ]; then
        echo "♻️  GENOAR_RECLAIM_RUN=1: discarding the stopped container(s) an"
        echo "   earlier run of id '$RUN_ID' left behind."
        run_containers -a --format '     removing {{.Names}}  ({{.Status}})'
        # Deliberately unquoted: one container id per line, all passed as
        # arguments. Only ids carrying this run's label reach this line.
        # shellcheck disable=SC2086
        docker rm $LEFTOVER_SAME_RUN >/dev/null 2>&1 || true
    else
        echo "❌ Run '$RUN_ID' already has containers here, stopped:"
        run_containers -a --format '     {{.Names}}  ({{.Status}})'
        echo ""
        echo "   They are an earlier run of this id that left no record behind."
        echo "   Starting on top of them would mix that run's exit codes into"
        echo "   this run's evidence, so this run neither reuses nor silently"
        echo "   deletes them."
        echo "   Read them first:  docker logs <name>"
        echo "   Then start a run with a fresh id (unset GENOAR_RUN_ID), or"
        echo "   re-run this with GENOAR_RECLAIM_RUN=1 to discard them."
        exit 2
    fi
fi

# ---------------------------------------------------------------------------
# 4. Reserving the run id, atomically, which is what the three checks above
#    cannot do however carefully they are ordered.
#
# Each reads the host and then acts on what it read; between those two moments
# another launcher can start, read the same emptiness and act on it too. The
# previous round's record guard was that shape - a `[ -e "$RUN_PLAN" ]` test and
# a later `mkdir -p` - and against two launchers started at one instant it did
# nothing: 30 pairs out of 30 both started their containers, onto one run
# directory and one manifest.
#
# `mkdir` without `-p` creates the directory or fails because it is there, in
# one step the kernel does not interleave, so of any number of racers exactly one
# gets it. The run directory *is* the claim, and it stays, so the claim outlives
# the process that made it. A failed `mkdir` is not by itself a collision - a
# read-only tree and a full disk fail it too - so the failure path asks whether
# the directory is there now and separates "taken" (exit 2) from "could not be
# created" (exit 1). Only the leaf is claimed; `runs/` is shared and gets -p.
# ---------------------------------------------------------------------------
if ! mkdir -p "$OUTPUT_DIR/runs" 2>/dev/null; then
    echo "❌ The run index could not be created: $OUTPUT_DIR/runs"
    echo "   This is not a run id collision - nothing has claimed '$RUN_ID'."
    echo "   Check that $OUTPUT_DIR exists, is writable and has space."
    exit 1
fi

RESERVED=0
PLAN_WRITTEN=0
if mkdir "$RUN_DIR" 2>/dev/null; then
    RESERVED=1
elif [ ! -d "$RUN_DIR" ]; then
    echo "❌ The run directory could not be created: $RUN_DIR"
    echo "   It is not there afterwards either, so this is not a run id"
    echo "   collision - nothing has claimed '$RUN_ID'. A read-only output"
    echo "   tree, a full disk or a missing parent all end here."
    exit 1
elif [ -e "$RUN_PLAN" ]; then
    # A run this one raced got here first and has already written its plan.
    refuse_recorded_run
    exit 2
else
    echo "❌ Run '$RUN_ID' is already claimed here:"
    echo "     $RUN_DIR"
    echo "   That directory is a reservation of this id that is not this run's:"
    echo "   another launcher took it a moment ago and has not written its"
    echo "   $RUN_PLAN_FILE yet, or one died before it wrote one. Two runs under"
    echo "   one id share every worker directory, checkpoint and manifest below"
    echo "   it, so this run refuses rather than move in beside it."
    echo "   Start a separate run with a fresh id (unset GENOAR_RUN_ID)."
    echo "   If no run holds it, remove that directory and start again."
    exit 2
fi

# Giving the id back, when this run turns out never to have used it.
#
# This script asks the user to confirm *after* it has reserved the id, because
# the confirmation quotes the page split and the split needs the corpus probe.
# Answering "no" must therefore not leave the id claimed - nor must a failed
# probe, or a Ctrl+C at the prompt. A reservation with no plan in it is evidence
# of nothing. `rmdir` removes it only while it is still empty, so a run that got
# as far as writing its plan keeps its claim.
release_run_claim() {
    local status=$?
    if [ "$RESERVED" -eq 1 ] && [ "$PLAN_WRITTEN" -eq 0 ]; then
        rmdir "$RUN_DIR" 2>/dev/null || true
    fi
    return "$status"
}
trap release_run_claim EXIT

# The shared data area. SMTX/SRR/META hold every run ever made into this output
# directory and are never cleared: a new run is isolated by its run id, not by
# deleting results. They are nobody's claim, so they get -p; the run's own
# worker directory is created once its plan is on disk.
mkdir -p "$OUTPUT_DIR/SMTX" "$OUTPUT_DIR/SRR" "$OUTPUT_DIR/META" logs

# Ask GEO once how many pages it actually holds, then split that, so the later
# workers of an over-long request are not started with nothing to do.
AVAILABLE_PAGES=""
if [ -n "${GENOAR_TOTAL_PAGES:-}" ]; then
    AVAILABLE_PAGES=$GENOAR_TOTAL_PAGES
    echo "📄 Using GENOAR_TOTAL_PAGES=$AVAILABLE_PAGES (corpus probe skipped)"
elif [ "${GENOAR_SKIP_PAGE_PROBE:-}" = "1" ]; then
    echo "📄 Corpus probe skipped (GENOAR_SKIP_PAGE_PROBE=1)"
else
    echo "📄 Asking GEO how many pages the query currently holds..."
    PROBE_OUT=""
    PROBE_OUT=$(docker run --rm --platform "$DOCKER_PLATFORM" \
        --label "$RUN_LABEL" --label "genoar.role=probe" \
        -v "$OUTPUT_DIR:/data" --shm-size=2g "$IMAGE_NAME" \
        --report-pages -o /data 2>&1) || PROBE_OUT=""
    AVAILABLE_PAGES=$(printf '%s\n' "$PROBE_OUT" \
        | sed -n 's/.*GENOAR_PAGE_REPORT .*"total_pages": *\([0-9]*\).*/\1/p' \
        | tail -1)
    if is_positive_int "${AVAILABLE_PAGES:-}"; then
        echo "📄 GEO currently holds $AVAILABLE_PAGES page(s) for this query"
    else
        AVAILABLE_PAGES=""
        echo "⚠️  Could not read the page count from GEO; splitting the requested"
        echo "   $MAX_PAGES page(s) instead."
    fi
fi

DISTRIBUTED_PAGES=$MAX_PAGES
if is_positive_int "${AVAILABLE_PAGES:-}" && [ "$AVAILABLE_PAGES" -lt "$MAX_PAGES" ]; then
    DISTRIBUTED_PAGES=$AVAILABLE_PAGES
    echo "ℹ️  Requested $MAX_PAGES page(s); GEO holds $AVAILABLE_PAGES, so"
    echo "   $DISTRIBUTED_PAGES page(s) are distributed across the workers."
fi

# Spread the remainder over the first few workers instead of dumping it on the
# last one, and never hand anybody an end page of 0.
PAGES_PER_WORKER=$((DISTRIBUTED_PAGES / WORKERS))
REMAINDER=$((DISTRIBUTED_PAGES % WORKERS))

echo ""
echo "🔧 Configuration:"
echo "  - Run ID: $RUN_ID"
echo "  - Workers: $WORKERS"
echo "  - Pages requested: $MAX_PAGES"
echo "  - Pages distributed: $DISTRIBUTED_PAGES"
echo "  - Pages per worker: ~$PAGES_PER_WORKER"

if [ "$DISTRIBUTED_PAGES" -lt "$WORKERS" ]; then
    echo ""
    echo "⚠️  Only $DISTRIBUTED_PAGES page(s) for $WORKERS workers."
    echo "   $((WORKERS - DISTRIBUTED_PAGES)) worker(s) will get no pages and are reported as skipped."
fi
echo ""

read -r -p "⚡ Start crawling with these settings? (y/N): " CONFIRM
if [[ ! $CONFIRM =~ ^[Yy]$ ]]; then
    echo "❌ Cancelled"
    exit 1
fi

cat > "$RUN_PLAN" <<EOF
{
  "run_id": "$RUN_ID",
  "requested_pages": {"start": 1, "end": $MAX_PAGES},
  "distributed_end": $DISTRIBUTED_PAGES,
  "available_pages": ${AVAILABLE_PAGES:-null},
  "items_per_page": 500,
  "workers": $WORKERS,
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

# From here the run has said what it set out to do, so its reservation stands
# even if the rest of this script fails: see release_run_claim above.
PLAN_WRITTEN=1

mkdir -p "$WORKERS_DIR"

# The top-level manifest points at the newest completed run. Clear it now so a
# run that fails cannot leave the previous run's manifest standing as this
# run's completion; each run's own manifest stays under runs/<id>/ either way.
rm -f "$OUTPUT_DIR/crawl_manifest.json"

echo ""
echo "🚀 Starting the parallel crawl!"

# Nothing is stopped or removed here, and in particular there is no sweep over
# every container on the host whose name contains "geo-worker". The containers
# this run may touch are the ones carrying its label, and those were settled at
# the top of the script, before the user was asked to confirm anything.

# Launch the workers. STARTED_WORKERS is what the monitor and the aggregator
# look at: a worker with no pages is never started and never counted as one.
STARTED_WORKERS=""
NEXT_PAGE=1
for i in $(seq 1 "$WORKERS"); do
    PAGE_COUNT=$PAGES_PER_WORKER
    if [ "$i" -le "$REMAINDER" ]; then
        PAGE_COUNT=$((PAGE_COUNT + 1))
    fi

    if [ "$PAGE_COUNT" -eq 0 ]; then
        echo "⏭️  Worker $i: no pages left to assign, not started"
        continue
    fi

    START_PAGE=$NEXT_PAGE
    END_PAGE=$((START_PAGE + PAGE_COUNT - 1))
    NEXT_PAGE=$((END_PAGE + 1))

    WORKER_DIR="$WORKERS_DIR/worker-$i"
    mkdir -p "$WORKER_DIR"
    cat > "$WORKER_DIR/assignment.json" <<EOF
{"worker_id": "$i", "start_page": $START_PAGE, "end_page": $END_PAGE}
EOF
    rm -f "$WORKER_DIR/exit_code"

    echo "🔄 Starting worker $i: pages $START_PAGE-$END_PAGE"

    # The image's ENTRYPOINT is `python /app/genoar_crawler.py`, so these are
    # its positional arguments: start page, end page, headless.
    # GENOAR_RUN_ID and GENOAR_WORKER_ID are what keep these containers apart:
    # the crawler is PID 1 in every one of them, so without an assigned
    # identity they all share one Chrome profile and one checkpoint in /data.
    # The same identity goes on the container, as name and as labels, so a
    # concurrent run neither collides with these names nor selects them.
    docker run -d \
      --name "$(worker_container_name "$i")" \
      --label "$RUN_LABEL" \
      --label "genoar.worker=$i" \
      --label "genoar.role=worker" \
      --platform "$DOCKER_PLATFORM" \
      -v "$OUTPUT_DIR:/data" \
      -e GENOAR_RUN_ID="$RUN_ID" \
      -e GENOAR_WORKER_ID="$i" \
      --memory="2g" \
      --cpus="1.0" \
      --shm-size=2g \
      "$IMAGE_NAME" \
      "$START_PAGE" "$END_PAGE" true

    STARTED_WORKERS="$STARTED_WORKERS $i"
    sleep "${GENOAR_WORKER_START_DELAY:-3}"  # Stagger the worker starts
done

if [ -z "$STARTED_WORKERS" ]; then
    echo "⚠️  No worker had any page to crawl."
    exit 3
fi

echo ""
echo "✅ All workers started!"
echo ""

# These describe containers that are already running, so a run whose monitor is
# skipped still needs them. Each selects on this run's label or names one of
# this run's containers; the host-wide forms they replace are what taught the
# habit of stopping "all the workers", somebody else's included.
echo "📊 Watch progress with:"
echo "  This run's workers: docker ps --filter label=$RUN_LABEL"
echo "  Follow a log:       docker logs -f $(worker_container_name 1)"
echo "  Crawl results:      ls -la crawl_output/*/"
echo ""
echo "🛑 Stop this run's workers: docker stop \$(docker ps -q --filter label=$RUN_LABEL)"
echo ""

# Wiring tests launch the workers against a stub docker and stop here; the
# monitoring loop below only ends when every container has exited.
if [ "${GENOAR_SKIP_MONITOR:-}" = "1" ]; then
    exit 0
fi

# "<running|exited|missing> <exit code>" for one worker container.
#
# `docker ps -a` prints "Exited (3)" for a worker that found no page in its
# range, and string-matching only "Exited (0)" reported that healthy outcome as
# a failure. The exit code has to be read as a number, from the container's own
# state - the same three buckets run_docker_parallel.sh uses.
container_state() {
    local name=$1
    local state=""

    state=$(docker inspect -f '{{.State.Running}} {{.State.ExitCode}}' "$name" 2>/dev/null) || state=""
    case "$state" in
        "true "*)  printf 'running 0' ;;
        "false "*) printf 'exited %s' "${state#false }" ;;
        *)         printf 'missing 0' ;;
    esac
    return 0
}

# Count files matching a pattern. Never fails and never prints whitespace, so
# the caller can use the result in arithmetic without `set -e` surprises.
#
# This counts what is on disk, which is every run that ever wrote into
# crawl_output, and never what this run collected - that question is answered
# by run_report_value below, off the manifest the aggregator writes.
count_files() {
    local dir=$1
    local pattern=$2
    local n=0

    if [ -d "$dir" ]; then
        n=$(find "$dir" -name "$pattern" -type f 2>/dev/null | wc -l | tr -d '[:space:]') || n=0
    fi
    printf '%s' "${n:-0}"
    return 0
}

# A whole number, possibly zero - what a file count may be, unlike a page count.
is_count() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    return 0
}

# One number out of the aggregator's GENOAR_RUN_REPORT line, which describes
# the run that was just merged and nothing else. Prints nothing when the key is
# absent, so the caller can tell "not reported" from "zero".
run_report_value() {
    printf '%s\n' "${AGG_OUT:-}" \
        | sed -n "s/.*GENOAR_RUN_REPORT .*\"$1\": *\([0-9][0-9]*\).*/\1/p" \
        | tail -1
}

describe_status() {
    case "$1" in
        0)   printf '✅ completed (exit 0)' ;;
        2)   printf '❌ failed - invalid arguments or page range (exit 2)' ;;
        3)   printf '⏭️  skipped - no pages in range (exit 3)' ;;
        130) printf '🛑 interrupted (exit 130)' ;;
        *)   printf '❌ failed (exit %s)' "$1" ;;
    esac
}

# Status monitoring. Overridable so a test does not wait half a minute for a
# loop whose containers have already exited.
POLL_SECONDS=${GENOAR_MONITOR_INTERVAL:-30}

echo "⏰ Status update every ${POLL_SECONDS} seconds..."
echo "   (Ctrl+C stops the monitor; the containers keep running.)"
echo ""

while true; do
    sleep "$POLL_SECONDS"

    echo "⏰ $(date '+%H:%M:%S') status update:"

    RUNNING=0
    SUCCEEDED=0
    SKIPPED=0
    FAILED=0
    for j in $STARTED_WORKERS; do
        STATE=$(container_state "$(worker_container_name "$j")")
        CODE=${STATE#* }
        case "$STATE" in
            running*)
                RUNNING=$((RUNNING + 1))
                echo "    Worker $j: 🟢 running"
                ;;
            exited*)
                echo "    Worker $j: $(describe_status "$CODE")"
                echo "$CODE" > "$WORKERS_DIR/worker-$j/exit_code"
                case "$CODE" in
                    0) SUCCEEDED=$((SUCCEEDED + 1)) ;;
                    3) SKIPPED=$((SKIPPED + 1)) ;;
                    *) FAILED=$((FAILED + 1)) ;;
                esac
                ;;
            *)
                echo "    Worker $j: ⚪ container not found"
                echo "1" > "$WORKERS_DIR/worker-$j/exit_code"
                FAILED=$((FAILED + 1))
                ;;
        esac
    done
    echo "  🔄 Workers running: $RUNNING"

    # What is in the output directory, which is every run that ever wrote
    # there. Not this run's tally: that is only known once the workers have
    # exited and the aggregator has merged their manifests, below.
    SMTX_COUNT=$(count_files "$OUTPUT_DIR/SMTX" '*.gz')
    SRR_COUNT=$(count_files "$OUTPUT_DIR/SRR" '*.txt')
    META_COUNT=$(count_files "$OUTPUT_DIR/META" '*_meta.txt')
    echo "  📁 Files in crawl_output, all runs - SMTX: $SMTX_COUNT, SRR: $SRR_COUNT, META: $META_COUNT"

    # Disk usage
    SIZE=$(du -sh crawl_output 2>/dev/null | cut -f1)
    echo "  💾 Data size: $SIZE"

    if [ "$RUNNING" -eq 0 ]; then
        echo ""
        echo "📊 Final results:"
        echo "  - Total data size: $SIZE"
        echo "  - Output directory: $OUTPUT_DIR"
        echo "  - Run directory: $RUN_DIR"
        echo ""
        echo "🔍 Worker summary: ✅ $SUCCEEDED  ⏭️  $SKIPPED  ❌ $FAILED"

        echo ""
        if [ "$FAILED" -gt 0 ]; then
            echo "❌ $FAILED worker(s) did not finish cleanly - check:"
            echo "     docker ps -a --filter label=$RUN_LABEL"
            echo "     docker logs $(worker_container_name '<n>')"
            echo "   No whole-range manifest was written: this run did not complete."
            exit 1
        fi

        if [ "$SUCCEEDED" -eq 0 ]; then
            echo "⚠️  Nothing was crawled: no worker had any page to process."
            exit 3
        fi

        # One place, once, after every container has exited. Its stdout is
        # captured for the one GENOAR_RUN_REPORT line, which says what this run
        # covered and collected; everything it logs still goes to stderr and
        # straight to the terminal.
        echo "🧮 Aggregating the run's workers..."
        AGG_RC=0
        AGG_OUT=""
        AGG_OUT=$(docker run --rm --platform "$DOCKER_PLATFORM" \
            --label "$RUN_LABEL" --label "genoar.role=aggregator" \
            -v "$OUTPUT_DIR:/data" \
            "$IMAGE_NAME" --aggregate --run-id "$RUN_ID" -o /data) || AGG_RC=$?
        if [ "$AGG_RC" -eq 3 ]; then
            echo "⚠️  The run covered no page: nothing on GEO fell in the requested range."
            exit 3
        fi
        if [ "$AGG_RC" -ne 0 ]; then
            echo "❌ The run's workers do not add up to the requested range; no"
            echo "   whole-range manifest was written."
            exit 1
        fi
        echo "📋 Run manifest: $RUN_DIR/crawl_manifest.json"

        # What this run produced, from the manifest just written, and what is
        # in crawl_output, which is every run that ever wrote there. Both are
        # worth showing; only the first says anything about this run, and only
        # the first may decide its exit status.
        RUN_SMTX=$(run_report_value collected_smtx)
        RUN_SRR=$(run_report_value collected_srr)
        RUN_META=$(run_report_value collected_meta)
        RUN_TOTAL=$(run_report_value collected_total)

        SMTX_COUNT=$(count_files "$OUTPUT_DIR/SMTX" '*.gz')
        SRR_COUNT=$(count_files "$OUTPUT_DIR/SRR" '*.txt')
        META_COUNT=$(count_files "$OUTPUT_DIR/META" '*_meta.txt')
        TOTAL_FILES=$((SMTX_COUNT + SRR_COUNT + META_COUNT))

        echo ""
        if is_count "$RUN_TOTAL"; then
            echo "  📁 Files collected by this run:"
            echo "    - SMTX files: ${RUN_SMTX:-0}"
            echo "    - SRR files: ${RUN_SRR:-0}"
            echo "    - META files: ${RUN_META:-0}"
            echo "    - Total: $RUN_TOTAL"
        else
            echo "  📁 Files collected by this run: not reported by the aggregator."
            printf '%s\n' "$AGG_OUT"
        fi
        echo ""
        echo "  📁 Files in $OUTPUT_DIR (all runs, including earlier ones):"
        echo "    - SMTX files: $SMTX_COUNT"
        echo "    - SRR files: $SRR_COUNT"
        echo "    - META files: $META_COUNT"
        echo "    - Total: $TOTAL_FILES"
        echo ""

        if ! is_count "$RUN_TOTAL"; then
            echo "⚠️  This run's own file count could not be read, so there is"
            echo "   nothing to show that it collected anything. The files above"
            echo "   may all be earlier runs'. Check:"
            echo "     docker logs $(worker_container_name '<n>')"
            exit 4
        fi

        if [ "$RUN_TOTAL" -eq 0 ]; then
            echo "⚠️  Every worker exited cleanly but this run collected 0 files."
            echo "   Check the worker logs before trusting this run."
            if [ "$TOTAL_FILES" -gt 0 ]; then
                echo "   The $TOTAL_FILES file(s) in $OUTPUT_DIR are earlier runs'."
            fi
            exit 4
        fi

        echo "🎉 All workers completed!"
        echo "   This run collected $RUN_TOTAL file(s) into $OUTPUT_DIR."
        break
    fi

    echo ""
done
