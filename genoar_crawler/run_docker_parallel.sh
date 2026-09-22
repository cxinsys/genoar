#!/bin/bash
#
# GENOAR Docker Parallel Execution Script
#
# Usage: ./run_docker_parallel.sh [num_workers] [total_pages] [target]
#
# Every container of a run is given its own identity (GENOAR_RUN_ID plus
# GENOAR_WORKER_ID) and therefore its own directory under
# crawl_output/runs/<run_id>/workers/worker-<n>/ for its checkpoint, manifest,
# downloads and Chrome profile. That identity has to come from here: inside a
# container the crawler is PID 1, so deriving it from the process id gave every
# container the same /data/.chrome/worker-1 and every Chrome after the first
# died on that profile's lock. When all containers have exited, one aggregator
# container checks the slices add up and writes the whole-range manifest.
#
# That same run id names and labels the containers, because the containers were
# the part that stayed global. Every container this run starts is called
# genoar-worker-<run_id>-<n> and carries the label genoar.run=<run_id>, and
# nothing here ever stops or removes a container that does not carry this run's
# label. Before that, startup ran
#     docker ps -aq --filter name=genoar-worker | xargs docker stop && docker rm
# which is a substring match over every container on the host: starting a run
# stopped and removed the live workers of anybody else's run.
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
#   GENOAR_SKIP_PAGE_PROBE=1   skip the corpus probe and trust [total_pages]
#
# [target] selects the crawler image to build and run. This repository ships
# exactly two: Dockerfile.amd64 (Google Chrome, x86_64) and Dockerfile.arm64
# (Chromium, ARM64). Accepted values:
#   auto              the host architecture, from `uname -m` (default)
#   amd64 | x86_64    force the x86_64 image
#   arm64 | aarch64   force the ARM64 image
#   linux | windows   x86_64 - kept for callers that pass an OS name
#   macos             the host architecture: Apple Silicon or Intel
#
# Worker exit codes, as defined by genoar_crawler.py:
#   0    pages were actually processed
#   1    the crawl failed
#   2    invalid arguments or page range
#   3    nothing to do - no page in the requested range exists on GEO
#   130  interrupted
#   anything else (137 for an OOM kill, ...) is a failure too
#
# This script's own exit status:
#   0  work was done and this run collected files
#   1  at least one worker failed, or this run's directory could not be created
#      at all - a read-only output tree or a full disk, which is a different
#      problem from a run id being taken
#   2  invalid arguments, or a run of this id already exists on this host: its
#      containers are here, its record is here, or another launcher holds it
#   3  no worker failed, but no worker had anything to crawl either
#   4  workers finished cleanly yet this run collected not a single file
#
# Container exit codes are read back with `docker inspect`; a container that
# died is never reported as completed.
#
# "This run" is meant literally in 0 and 4, exactly as in run_parallel_crawl.sh.
# crawl_output is persistent and shared across runs on purpose, so the files in
# it are not this run's evidence; the run manifest the aggregator writes is.

set -euo pipefail

echo "🚀 GENOAR Docker Parallel Crawling"

# Configuration
WORKERS=${1:-4}
TOTAL_PAGES=${2:-100}
TARGET=${3:-auto}  # see the usage note above

# Seconds between status updates. Overridable so tests do not wait a minute.
POLL_SECONDS=${GENOAR_MONITOR_INTERVAL:-45}
# Stagger between container launches.
START_DELAY=${GENOAR_WORKER_START_DELAY:-3}

is_positive_int() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ "$1" -ge 1 ]
}

# Resolve the target to an architecture, and the architecture to the image.
#
# The image has to be chosen by architecture, not by OS name: "macOS" is two
# architectures now, and an Apple Silicon Mac given the x86_64 image gets a
# slow emulated Chrome or no Chrome at all. `uname -m` is the truthful answer
# and is what `auto` (the default) asks.
#
# The OS names stay accepted because callers pass them: the Snakefile and
# scripts/entrypoint.sh both invoke this script with `linux`. `linux` and
# `windows` resolve to amd64 - the deployment servers and WSL2 hosts are
# x86_64, and Stage 3 (Cell Ranger) pins deployment to x86_64 anyway - while
# `macos` defers to the host, which is the case an OS name cannot answer.
host_arch() {
    case "$(uname -m)" in
        arm64|aarch64) printf 'arm64' ;;
        x86_64|amd64)  printf 'amd64' ;;
        *)             printf 'unsupported' ;;
    esac
}

case "$TARGET" in
    auto|macos)    ARCH=$(host_arch) ;;
    arm64|aarch64) ARCH="arm64" ;;
    amd64|x86_64)  ARCH="amd64" ;;
    linux|windows) ARCH="amd64" ;;
    *)
        echo "❌ Invalid target '$TARGET'."
        echo "   Use 'auto', 'amd64', 'arm64', 'linux', 'macos' or 'windows'."
        exit 2
        ;;
esac

if [ "$ARCH" = "unsupported" ]; then
    echo "❌ Unsupported host architecture: $(uname -m)"
    echo "   Only amd64 and arm64 crawler images exist in this repository."
    echo "   Pass 'amd64' or 'arm64' explicitly if you know which one emulates."
    exit 2
fi

IMAGE_NAME="genoar:$ARCH"
DOCKERFILE="Dockerfile.$ARCH"
DOCKER_PLATFORM="linux/$ARCH"

if [ ! -f "$DOCKERFILE" ]; then
    echo "❌ $DOCKERFILE not found. Run this script from genoar_crawler/."
    exit 2
fi

# Validate before any arithmetic: dividing by zero workers aborts the script
# with a message nobody can act on.
# GENOAR_RESUME=1 is refused here rather than ignored.
#
# Resuming means giving every worker back the slice it started on and letting it
# continue its own checkpoint. Only run_parallel_crawl.sh has a path that does
# that. Here the flag reached the end of the script without ever being read: a
# user who set it got a brand new crawl from page 1 and no indication that the
# thing they asked for had not happened. A flag whose name promises a
# continuation and whose effect is a fresh crawl is worse than one that is not
# supported, so this says so and stops.
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

if ! is_positive_int "$WORKERS"; then
    echo "❌ Number of workers must be a positive integer (got: '$WORKERS')"
    exit 2
fi
if ! is_positive_int "$TOTAL_PAGES"; then
    echo "❌ Total pages must be a positive integer (got: '$TOTAL_PAGES')"
    exit 2
fi
# Checked here rather than where it is used, so that every argument is validated
# before the run id is reserved. Reserving first and validating afterwards would
# refuse the run *after* it had claimed its id, and the corrected retry would
# then be turned away by the claim its own predecessor left behind.
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
# user meets in one half has to hold in the other - otherwise
# GENOAR_RUN_ID=LATEST crawls happily here and then fails Stage 3 as a
# configuration error, half a pipeline in. Compared case-sensitively, exactly as
# Stage 3 compares it, so 'latest' stays usable on both sides.
RUN_ID_RESERVED="LATEST"

# genoar_crawler.py's RUN_PLAN_FILE: the name of a run's plan, and so of its
# record. The aggregator refuses a run that has none.
RUN_PLAN_FILE="run.json"

# One identity for this whole run. Every worker directory and the merged
# manifest hang off it, and a second run started a second later cannot collide.
#
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

# Check if Docker is running
if ! docker info >/dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker Desktop."
    exit 1
fi

# Everything this run puts on the Docker daemon carries this label, and every
# question this script asks the daemon is asked through it.
#
# A label, not a name prefix. `docker ps --filter name=X` is a substring match
# with no anchored form, so `--filter name=genoar-worker` selected the workers
# of every run on the host - which is how the previous version of this script
# stopped and removed a live 90-minute crawl belonging to somebody else. A run
# id inside the name would narrow that, but only by convention: the filter
# would still match anything a person or another tool happened to name
# similarly, and matching is still substring, so run id "r1" would select run
# "r12" as well. `--filter label=genoar.run=<id>` is an exact key/value match
# against metadata only this script writes, and it cannot widen.
#
# The name still carries the run id as well, for a different reason: names are
# unique per host, so two concurrent runs both calling a container
# genoar-worker-1 would collide on the name itself and the second `docker run`
# would simply fail. The label decides ownership; the name avoids the clash.
RUN_LABEL="genoar.run=$RUN_ID"

worker_container_name() {
    printf 'genoar-worker-%s-%s' "$RUN_ID" "$1"
}

# Containers already carrying this run's label. Normally none: the run id is
# freshly minted for every run. They exist when GENOAR_RUN_ID was set by hand
# and a run of that name is still going, or crashed and left containers behind.
run_containers() {
    docker ps "$@" --filter "label=$RUN_LABEL" 2>/dev/null || true
}

# Where this run's records live. Derived here, before any check below, so all
# of them can be answered before anything is created.
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

# Three conditions, asked in this order, and the order is the point.
#
# A run of this id can be live, or finished-and-recorded, or gone except for its
# containers. Asked in any other order a user gets sent round in circles: told
# to set GENOAR_RECLAIM_RUN=1, complying, and then hitting a second, differently
# named refusal that the first message never mentioned. So the most urgent
# condition is asked first, the one with no way through second, and the one
# GENOAR_RECLAIM_RUN actually resolves last - which means that flag is only ever
# suggested in the situation where setting it is the answer.
#
# All three read the host; none of them is the reservation. A read is a question
# about the past, and between the answer and this run acting on it another run
# can do anything at all. The reservation is one atomic step and comes after
# them, below.
echo "🔎 Checking whether run '$RUN_ID' already exists on this host..."

# 1. Live. Nothing may proceed, and nothing may be touched.
RUNNING_SAME_RUN=$(run_containers -q)
if [ -n "$RUNNING_SAME_RUN" ]; then
    echo "❌ Run '$RUN_ID' is already running here:"
    run_containers --format '     {{.Names}}  ({{.Status}})'
    echo ""
    echo "   Two runs sharing one id share their container names, their run"
    echo "   directory and their manifest, so the second would overwrite the"
    echo "   first's evidence. Refusing to start."
    echo "   Let this script mint an id (unset GENOAR_RUN_ID), choose another"
    echo "   one, or wait for that run to finish."
    exit 2
fi

# 2. Recorded. The containers are the run's logs; run.json is its durable
# evidence, and it outlives them - `docker system prune` clears the first and
# leaves the second, which is exactly the case the container check alone missed.
# There is no flag through this one. This script cannot resume a run, so the
# only way to proceed would be to overwrite the record, and a run record is
# never overwritten. GENOAR_RECLAIM_RUN is named here anyway, to say plainly
# that it does not apply, because the alternative is a user setting it and
# hitting this same wall a second time.
if [ -e "$RUN_PLAN" ]; then
    refuse_recorded_run
    exit 2
fi

# 3. Containers but no record: a run that died before it promised anything, or
# whose output directory has since been cleared. Nothing here is evidence of
# what a run did, so discarding it is offered.
LEFTOVER_SAME_RUN=$(run_containers -aq)
if [ -n "$LEFTOVER_SAME_RUN" ]; then
    if [ "${GENOAR_RECLAIM_RUN:-}" = "1" ]; then
        echo "♻️  GENOAR_RECLAIM_RUN=1: discarding the stopped container(s) an"
        echo "   earlier run of id '$RUN_ID' left behind."
        run_containers -a --format '     removing {{.Names}}  ({{.Status}})'
        # Deliberately unquoted: one container id per line, all passed as
        # arguments. Only ids that carry this run's label reach this line.
        # shellcheck disable=SC2086
        docker rm $LEFTOVER_SAME_RUN >/dev/null 2>&1 || true
    else
        echo "❌ Run '$RUN_ID' already has containers here, stopped:"
        run_containers -a --format '     {{.Names}}  ({{.Status}})'
        echo ""
        echo "   They are an earlier run of this id that left no record behind."
        echo "   Starting on top of them would mix that run's exit codes into"
        echo "   this run's completion evidence, so this run refuses to reuse"
        echo "   them and refuses to delete them behind your back."
        echo "   Read them first:  docker logs <name>"
        echo "   Then either start a run with a fresh id (unset GENOAR_RUN_ID),"
        echo "   or re-run this with GENOAR_RECLAIM_RUN=1 to discard them."
        exit 2
    fi
fi

# ---------------------------------------------------------------------------
# 4. Reserving the run id, atomically, which is what the three checks above
#    cannot do however carefully they are ordered.
#
# Each of them reads the host and then acts on what it read. Between those two
# moments another launcher can start, read the same emptiness and act on it too.
# The previous round's record guard was exactly that shape -
#
#     if [ -e "$RUN_PLAN" ]; then ... refuse ... fi     # ... later ...
#     mkdir -p "$RUN_DIR/workers"
#
# - and against two launchers started at one instant it did nothing: 30 pairs
# out of 30 both started their containers, onto one run directory, one set of
# worker directories and one manifest.
#
# `mkdir` without `-p` is the whole fix. It creates the directory or fails
# because it is already there, in one step the kernel does not interleave, so of
# any number of racers exactly one gets it. The run directory *is* the claim: it
# is the thing that must be new, it is named by the id being claimed, and it
# stays, so the claim outlives the process that made it.
#
# A failed `mkdir` is not by itself a collision - a read-only output tree and a
# full disk fail the same call - so the failure path asks whether the directory
# is there now, and says "somebody else has this id" (exit 2) or "this could not
# be created" (exit 1) accordingly. A user whose disk is full must not be told
# to change their run id.
#
# Only the leaf is claimed. `runs/` is shared by every run that ever wrote here
# and is made with -p.
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
# A reservation with no plan written into it is evidence of nothing, and leaving
# one behind would refuse the next attempt at the same id for no reason - a user
# whose image build failed would be told their run id was taken by the run that
# just failed. `rmdir` removes it only while it is still empty, so a run that
# got as far as writing its plan keeps its claim.
release_run_claim() {
    local status=$?
    if [ "$RESERVED" -eq 1 ] && [ "$PLAN_WRITTEN" -eq 0 ]; then
        rmdir "$RUN_DIR" 2>/dev/null || true
    fi
    return "$status"
}
trap release_run_claim EXIT

# Build Docker image if it doesn't exist
if [[ "$(docker images -q "$IMAGE_NAME" 2> /dev/null)" == "" ]]; then
    echo "🔨 Building Docker image: $IMAGE_NAME"
    docker build --platform "$DOCKER_PLATFORM" -t "$IMAGE_NAME" -f "$DOCKERFILE" .
    echo "✅ Docker image built successfully"
else
    echo "✅ Docker image $IMAGE_NAME already exists"
fi

# The shared data area. SMTX/SRR/META hold every run ever made into this output
# directory and are never cleared: a new run is isolated by its run id, not by
# deleting results. They are nobody's claim, so they are created with -p; the
# run's own worker directory is created once its plan is on disk.
mkdir -p "$OUTPUT_DIR/SMTX" "$OUTPUT_DIR/SRR" "$OUTPUT_DIR/META" logs

# Ask GEO once how many pages it actually holds, then split that. Each worker
# still caps itself, so this is not what makes the run correct - it is what
# stops the later workers of a 100-page request being started against a
# 47-page corpus with nothing to do.
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
        echo "   $TOTAL_PAGES page(s) instead. Each worker still caps itself, so a"
        echo "   short corpus shows up as skipped workers rather than wrong data."
    fi
fi

# What is actually handed out. The user's own request is kept, so the merged
# manifest can say "asked for N, GEO had M" rather than silently shrinking.
DISTRIBUTED_PAGES=$TOTAL_PAGES
if is_positive_int "${AVAILABLE_PAGES:-}" && [ "$AVAILABLE_PAGES" -lt "$TOTAL_PAGES" ]; then
    DISTRIBUTED_PAGES=$AVAILABLE_PAGES
    echo "ℹ️  Requested $TOTAL_PAGES page(s); GEO holds $AVAILABLE_PAGES, so"
    echo "   $DISTRIBUTED_PAGES page(s) are distributed across the workers."
fi

# Split the pages across the workers, spreading the remainder over the first
# few workers instead of dumping it all on the last one. An end page of 0 means
# "crawl everything" to the crawler, so a worker must never be handed one by
# accident, and a worker with no pages at all must not be started.
PAGES_PER_WORKER=$((DISTRIBUTED_PAGES / WORKERS))
REMAINDER=$((DISTRIBUTED_PAGES % WORKERS))

echo "🔧 Configuration:"
echo "  - Run ID: $RUN_ID"
echo "  - Target: $TARGET (architecture: $ARCH, host: $(uname -m))"
echo "  - Workers: $WORKERS"
echo "  - Pages requested: $TOTAL_PAGES"
echo "  - Pages distributed: $DISTRIBUTED_PAGES"
echo "  - Pages per worker: ~$PAGES_PER_WORKER"
echo "  - Docker image: $IMAGE_NAME (from $DOCKERFILE)"
echo "  - Run directory: $RUN_DIR"

if [ "$DISTRIBUTED_PAGES" -lt "$WORKERS" ]; then
    echo ""
    echo "⚠️  Only $DISTRIBUTED_PAGES page(s) for $WORKERS workers."
    echo "   $((WORKERS - DISTRIBUTED_PAGES)) worker(s) will get no pages and are reported as skipped."
fi
echo ""

# The plan, on disk, before a worker starts. The aggregator reads it to know
# what the run promised; without it a run could only ever prove what it did.
cat > "$RUN_PLAN" <<EOF
{
  "run_id": "$RUN_ID",
  "requested_pages": {"start": 1, "end": $TOTAL_PAGES},
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

# No cleanup sweep happens here. A sweep of
#   docker ps -aq --filter name=genoar-worker   ->   docker stop / docker rm
# would stop and remove every container on the host whose name contains
# "genoar-worker", live workers of other people's runs included. The containers
# this run may touch are the ones carrying this run's label, and they were dealt
# with above, before anything on disk was written: either there were none (the
# normal case), or this run refused to start.

# Worker bookkeeping, indexed 1..WORKERS.
STATUS_PENDING=-1
WORKER_RANGE=()
WORKER_STATUS=()
WORKER_DIR=()

# Function to run a worker
run_worker() {
    local worker_id=$1
    local start_page=$2
    local end_page=$3
    local worker_dir="$WORKERS_DIR/worker-${worker_id}"

    mkdir -p "$worker_dir"
    cat > "$worker_dir/assignment.json" <<EOF
{"worker_id": "$worker_id", "start_page": $start_page, "end_page": $end_page}
EOF
    rm -f "$worker_dir/exit_code"

    echo "🔄 Starting Worker $worker_id: pages $start_page-$end_page"

    # GENOAR_RUN_ID and GENOAR_WORKER_ID are what keep these containers apart.
    # Without them the crawler falls back to its process id, which is 1 in every
    # container, and all of them collide in /data.
    #
    # The same identity goes on the container itself: into the name, so two
    # concurrent runs do not collide on "genoar-worker-1" and lose the second
    # `docker run`, and into the labels, which are what every later `docker ps`
    # in this script selects on.
    #
    # What `docker run -d` prints is the container id. It belongs beside the
    # worker it identifies, like everything else about that worker: a shared
    # path such as logs/worker_<n>.log has no run in it, so two concurrent runs
    # would overwrite each other's record of which container was whose. The
    # worker's actual log is `docker logs <name>`.
    docker run -d \
        --name "$(worker_container_name "$worker_id")" \
        --label "$RUN_LABEL" \
        --label "genoar.worker=$worker_id" \
        --label "genoar.role=worker" \
        --platform "$DOCKER_PLATFORM" \
        -v "$OUTPUT_DIR:/data" \
        -e GENOAR_RUN_ID="$RUN_ID" \
        -e GENOAR_WORKER_ID="$worker_id" \
        -e GENOAR_CHROME_DISABLE_HTTP2="${GENOAR_CHROME_DISABLE_HTTP2:-}" \
        --memory="2g" \
        --cpus="1.0" \
        --shm-size=2g \
        "$IMAGE_NAME" \
        "$start_page" "$end_page" true \
        > "$worker_dir/container_id" 2>&1

    WORKER_RANGE[worker_id]="$start_page-$end_page"
    WORKER_STATUS[worker_id]=$STATUS_PENDING
    WORKER_DIR[worker_id]="$worker_dir"
}

# Start all workers
echo "🚀 Starting $WORKERS Docker workers..."
NEXT_PAGE=1
for ((i = 1; i <= WORKERS; i++)); do
    PAGE_COUNT=$PAGES_PER_WORKER
    if [ "$i" -le "$REMAINDER" ]; then
        PAGE_COUNT=$((PAGE_COUNT + 1))
    fi

    if [ "$PAGE_COUNT" -eq 0 ]; then
        echo "⏭️  Worker $i: no pages left to assign, not started"
        WORKER_RANGE[i]="none"
        WORKER_STATUS[i]=3
        WORKER_DIR[i]=""
        continue
    fi

    START_PAGE=$NEXT_PAGE
    END_PAGE=$((START_PAGE + PAGE_COUNT - 1))
    NEXT_PAGE=$((END_PAGE + 1))

    run_worker "$i" "$START_PAGE" "$END_PAGE"
    sleep "$START_DELAY"
done

echo ""
echo "✅ All Docker workers started!"
echo ""

# These describe containers that are already running, so they belong here and
# not past the monitor hook below - a run whose monitor was skipped still needs
# to say where its containers are.
#
# Everything printed selects on this run's label or names one of this run's
# containers. None of it is a host-wide command: `--filter name=genoar-worker`
# and `docker stop $(docker ps -q ...)` taught the habit that stopped somebody
# else's crawl, and a printed hint is where the habit came from.
echo "📊 Monitor progress:"
echo "  - This run's containers: docker ps --filter label=$RUN_LABEL"
echo "  - View container logs: docker logs -f $(worker_container_name 1)"
echo "  - Check META downloads: docker logs $(worker_container_name 1) | grep 'META data saved'"
echo "  - Worker exit codes: cat $WORKERS_DIR/worker-*/exit_code"
echo "  - Count files: find crawl_output -type f | wc -l"
echo ""

# Wiring tests launch the workers against a stub docker and stop here; the
# monitoring loop below never exits on its own.
if [ "${GENOAR_SKIP_MONITOR:-}" = "1" ]; then
    exit 0
fi

# Count files matching a pattern. Never fails and never prints whitespace, so
# the caller can use the result in arithmetic without `set -e` surprises.
#
# As in run_parallel_crawl.sh: this counts what is on disk, which is every run
# that ever wrote into crawl_output, and never what this run collected. That
# question is answered by run_report_value below, off the run manifest.
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

# Turn a worker's exit code into something a human can act on.
describe_status() {
    case "$1" in
        0)   printf '✅ completed (exit 0)' ;;
        2)   printf '❌ failed - invalid arguments or page range (exit 2)' ;;
        3)   printf '⏭️  skipped - no pages in range (exit 3)' ;;
        130) printf '🛑 interrupted (exit 130)' ;;
        *)   printf '❌ failed (exit %s)' "$1" ;;
    esac
}

# "<running|exited|missing> <exit code>" for one worker container.
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

# Monitoring loop
echo "⏰ Monitoring Docker workers - a status update every ${POLL_SECONDS}s."
echo "   (Ctrl+C stops the monitor; the containers keep running.)"
echo ""

while true; do
    sleep "$POLL_SECONDS"

    echo "📊 Status Update - $(date '+%H:%M:%S')"

    RUNNING=0
    for ((i = 1; i <= WORKERS; i++)); do
        if [ "${WORKER_STATUS[i]}" -eq "$STATUS_PENDING" ]; then
            STATE=$(container_state "$(worker_container_name "$i")")
            case "$STATE" in
                running*)
                    # Never use ((RUNNING++)) here: it evaluates to 0 on the
                    # first increment, which returns a non-zero status and would
                    # kill this script under `set -e` while every worker is fine.
                    RUNNING=$((RUNNING + 1))
                    echo "    Worker $i (pages ${WORKER_RANGE[i]}): 🟢 running"
                    continue
                    ;;
                exited*)
                    # The container stopped. Read its real exit code - that is
                    # the whole point: a crashed worker and a finished one look
                    # identical from `docker ps` alone. The aggregator reads the
                    # same number back from the worker's directory.
                    EXIT_CODE=${STATE#exited }
                    WORKER_STATUS[i]=$EXIT_CODE
                    echo "$EXIT_CODE" > "${WORKER_DIR[i]}/exit_code"
                    ;;
                *)
                    echo "    Worker $i (pages ${WORKER_RANGE[i]}): ⚪ container not found"
                    WORKER_STATUS[i]=1
                    echo "1" > "${WORKER_DIR[i]}/exit_code"
                    continue
                    ;;
            esac
        fi

        if [ "${WORKER_RANGE[i]}" = "none" ]; then
            echo "    Worker $i: ⏭️  skipped - no pages assigned"
        else
            echo "    Worker $i (pages ${WORKER_RANGE[i]}): $(describe_status "${WORKER_STATUS[i]}")"
        fi
    done

    echo "  🐳 Containers running: $RUNNING/$WORKERS"

    SMTX_COUNT=$(count_files crawl_output/SMTX '*.gz')
    SRR_COUNT=$(count_files crawl_output/SRR '*.txt')
    META_COUNT=$(count_files crawl_output/META '*_meta.txt')
    echo "  📁 Files in crawl_output, all runs: SMTX=$SMTX_COUNT, SRR=$SRR_COUNT, META=$META_COUNT"
    echo ""

    if [ "$RUNNING" -eq 0 ]; then
        break
    fi
done

# Final summary - counts first, verdict last, and the verdict is the truth.
SUCCEEDED=0
SKIPPED=0
SKIPPED_EMPTY_RANGE=0
FAILED=0
FAILED_WORKERS=""

for ((i = 1; i <= WORKERS; i++)); do
    case "${WORKER_STATUS[i]}" in
        0) SUCCEEDED=$((SUCCEEDED + 1)) ;;
        3)
            SKIPPED=$((SKIPPED + 1))
            # A worker that ran and came back with "nothing in range" says
            # something about GEO; one that never got a range says something
            # about the worker count. Keep them apart.
            if [ "${WORKER_RANGE[i]}" != "none" ]; then
                SKIPPED_EMPTY_RANGE=$((SKIPPED_EMPTY_RANGE + 1))
            fi
            ;;
        *)
            FAILED=$((FAILED + 1))
            FAILED_WORKERS="$FAILED_WORKERS $i"
            ;;
    esac
done

echo ""
echo "📊 Final Summary:"
echo ""
echo "  Workers:"
for ((i = 1; i <= WORKERS; i++)); do
    if [ "${WORKER_RANGE[i]}" = "none" ]; then
        echo "    Worker $i: ⏭️  skipped - no pages assigned"
    else
        echo "    Worker $i (pages ${WORKER_RANGE[i]}): $(describe_status "${WORKER_STATUS[i]}")"
    fi
done
echo ""
echo "    ✅ Succeeded: $SUCCEEDED/$WORKERS"
echo "    ⏭️  Skipped (nothing to do): $SKIPPED/$WORKERS"
echo "    ❌ Failed: $FAILED/$WORKERS"
echo ""
echo "🔧 Cleanup commands (this run only - the label is what keeps them from"
echo "   reaching a run somebody else is in the middle of):"
echo "  - Stop this run's workers: docker stop \$(docker ps -q --filter label=$RUN_LABEL)"
echo "  - Remove this run's workers: docker rm \$(docker ps -aq --filter label=$RUN_LABEL)"
echo "  - Remove image: docker rmi $IMAGE_NAME"
echo ""
echo "📁 Output: $(pwd)/crawl_output"
echo ""

if [ "$SKIPPED_EMPTY_RANGE" -gt 0 ]; then
    echo "ℹ️  $SKIPPED_EMPTY_RANGE worker(s) found no pages in their assigned range."
    echo "   GEO most likely has fewer than $TOTAL_PAGES pages of results right now."
    echo "   Lower the page count, or let the crawler auto-detect the end page."
    echo ""
fi

if [ "$FAILED" -gt 0 ]; then
    echo "❌ $FAILED of $WORKERS workers failed:"
    for i in $FAILED_WORKERS; do
        echo "   - Worker $i: $(describe_status "${WORKER_STATUS[i]}") - docker logs $(worker_container_name "$i")"
    done
    echo "   No whole-range manifest was written: this run did not complete."
    exit 1
fi

if [ "$SUCCEEDED" -eq 0 ]; then
    echo "⚠️  Nothing was crawled: no worker had any page to process."
    echo "   This is not a successful run - check the requested page range."
    exit 3
fi

# One place, once, after every container has exited: check that the slices add
# up and write the run's manifest. A partially completed run gets no manifest.
#
# Its stdout is captured for the one GENOAR_RUN_REPORT line, which says what
# this run covered and collected; everything it logs still goes to stderr and
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
    echo "   whole-range manifest was written. See the messages above."
    exit 1
fi
echo "📋 Run manifest: $RUN_DIR/crawl_manifest.json"

# What this run produced, from the manifest the aggregator just wrote, and what
# is in crawl_output, which is every run that ever wrote there. Both are worth
# showing; only the first says anything about this run, and only the first may
# decide its exit status.
RUN_SMTX=$(run_report_value collected_smtx)
RUN_SRR=$(run_report_value collected_srr)
RUN_META=$(run_report_value collected_meta)
RUN_TOTAL=$(run_report_value collected_total)

SMTX_FINAL=$(count_files crawl_output/SMTX '*.gz')
SRR_FINAL=$(count_files crawl_output/SRR '*.txt')
META_FINAL=$(count_files crawl_output/META '*_meta.txt')
TOTAL_FILES=$((SMTX_FINAL + SRR_FINAL + META_FINAL))

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
echo "    - SMTX files: $SMTX_FINAL"
echo "    - SRR files: $SRR_FINAL"
echo "    - META files: $META_FINAL"
echo "    - Total: $TOTAL_FILES"
echo ""

if ! is_count "$RUN_TOTAL"; then
    echo "⚠️  This run's own file count could not be read, so there is nothing"
    echo "   to show that it collected anything. The files above may all be"
    echo "   earlier runs'. Check docker logs before trusting this run."
    exit 4
fi

if [ "$RUN_TOTAL" -eq 0 ]; then
    echo "⚠️  Workers finished cleanly but this run collected 0 files."
    echo "   Something is wrong upstream (downloads blocked, wrong volume mount,"
    echo "   or no matching records). Check docker logs before trusting this run."
    if [ "$TOTAL_FILES" -gt 0 ]; then
        echo "   The $TOTAL_FILES file(s) in $OUTPUT_DIR are earlier runs'."
    fi
    exit 4
fi

echo "🎉 All Docker workers completed: $SUCCEEDED succeeded, $SKIPPED skipped."
echo "   This run collected $RUN_TOTAL file(s) into $OUTPUT_DIR."
