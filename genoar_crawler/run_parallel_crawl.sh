#!/bin/bash
#
# GENOAR parallel crawling - one background Python worker per page slice.
#
# Usage: ./run_parallel_crawl.sh [workers] [total_pages] [output_dir]
#
# Every worker of a run gets its own directory under
# <output_dir>/runs/<run_id>/workers/worker-<n>/ and writes its checkpoint,
# manifest, downloads, Chrome profile and log there. When they have all exited,
# this script runs the aggregator once, and only the aggregator writes a
# manifest that claims the whole requested range. Nothing deletes an earlier
# run: a new run gets a new run id.
#
# Environment:
#   GENOAR_RUN_ID              name this run (default: a fresh timestamped id).
#                              Point it at an existing run together with
#                              GENOAR_RESUME=1 to continue that run.
#   GENOAR_RESUME=1            continue an existing run: each worker resumes from
#                              its own checkpoint. This is the only way to reuse
#                              a run id; without it an id that already has a
#                              run.json is refused rather than overwritten. The
#                              original run.json stands, and each resume is
#                              appended to runs/<id>/resumes.jsonl. A resume must
#                              ask for the same worker count and the same page
#                              request the plan records, because those are what
#                              decide which slice each checkpoint belongs to.
#                              The run must be here: GENOAR_RUN_ID has to name a
#                              run that already has a run.json, and an id that
#                              has none is refused (exit 2) rather than started
#                              as a new run. Mistyping the id of a crawl you are
#                              recovering must not silently restart it.
#   GENOAR_TOTAL_PAGES=<n>     skip the corpus probe and use this page count.
#   GENOAR_SKIP_PAGE_PROBE=1   skip the corpus probe and trust [total_pages].
#   GENOAR_MONITOR_INTERVAL    seconds between status updates (default 30).
#   GENOAR_WORKER_START_DELAY  seconds between worker launches (default 2).
#
# Worker exit codes, as defined by genoar_crawler.py:
#   0    pages were actually processed
#   1    the crawl failed
#   2    invalid arguments or page range
#   3    nothing to do - no page in the requested range exists on GEO
#   130  interrupted (Ctrl+C)
#   anything else (137, 143, ...) is a failure too
#
# This script's own exit status:
#   0  work was done, the run aggregated, and this run collected files
#   1  at least one worker failed, the run did not cover its range, or this
#      run's directory could not be created at all - a read-only output tree or
#      a full disk, which is a different problem from a run id being taken
#   2  invalid arguments, or this run id is not this run's to use: it already
#      has a record (see GENOAR_RESUME), another run holds it right now, the
#      resume asks for something the run's plan does not say, or the resume
#      names a run that is not recorded here at all
#   3  no worker failed, but no worker had anything to crawl either
#   4  workers finished cleanly yet this run collected not a single file
#
# A worker that dies is never reported as completed: the monitor harvests
# every worker's real exit status with `wait` before it says anything.
#
# "This run" is meant literally in 0 and 4. The output directory is persistent
# and shared - SMTX/SRR/META hold every run ever made into it, and must, since
# a new run may not erase an earlier one's results - so counting the files in
# it answered a different question, and one leftover file from any earlier run
# was enough to report a run that downloaded nothing as a success. The number
# that decides comes from the run manifest the aggregator writes.

set -euo pipefail

echo "🚀 GENOAR Parallel Crawling (Python-based)"

# Default values
WORKERS=${1:-4}
TOTAL_PAGES=${2:-100}
OUTPUT_DIR=${3:-crawl_output}

# Seconds between status updates. Overridable so tests do not wait half a minute.
POLL_SECONDS=${GENOAR_MONITOR_INTERVAL:-30}
# Stagger between worker launches, so four Chrome instances do not start at once.
START_DELAY=${GENOAR_WORKER_START_DELAY:-2}

PYTHON_BIN=${GENOAR_PYTHON:-python}

is_positive_int() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ "$1" -ge 1 ]
}

# A whole number, possibly zero - what a file count or a page number read back
# out of a plan may be, unlike a page count.
is_count() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    return 0
}

# Validate before any arithmetic: `$((TOTAL_PAGES / WORKERS))` with WORKERS=0
# aborts the whole script with a division-by-zero error message nobody can act on.
#
# All of it before the run id is reserved, too. Reserving first and validating
# afterwards would let `GENOAR_TOTAL_PAGES=nonsense` refuse the run *after* it
# had claimed its id, and the next attempt - with the argument corrected - would
# then be turned away by the claim its own predecessor left behind.
if ! is_positive_int "$WORKERS"; then
    echo "❌ Number of workers must be a positive integer (got: '$WORKERS')"
    exit 2
fi
if ! is_positive_int "$TOTAL_PAGES"; then
    echo "❌ Total pages must be a positive integer (got: '$TOTAL_PAGES')"
    exit 2
fi
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

# One identity for this whole run. Every worker directory, every log and the
# merged manifest hang off it, and a second run started a second later cannot
# collide with this one.
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
    # A resume names the run it continues, and this one names none. Minting an
    # id here and then resuming it is a contradiction: the id is new, so there
    # is nothing under it, and the run that was meant to be continued is not
    # touched. Said here rather than left to the "no such run" refusal below,
    # which would quote an id the caller never typed.
    if [ "${GENOAR_RESUME:-}" = "1" ]; then
        echo "❌ GENOAR_RESUME=1 is set and GENOAR_RUN_ID is not."
        echo "   A resume continues one named run, and no run was named. An id"
        echo "   minted here would be new, so there would be nothing under it"
        echo "   to continue and the run you meant would stay as it is."
        echo "   To continue a run:   GENOAR_RUN_ID=<id> GENOAR_RESUME=1 $0 $WORKERS $TOTAL_PAGES"
        echo "   To start a new run:  unset GENOAR_RESUME"
        exit 2
    fi
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

RUN_DIR="$OUTPUT_DIR/runs/$RUN_ID"
WORKERS_DIR="$RUN_DIR/workers"

# The run's durable record. run.json is written before any worker starts and is
# what the aggregator holds the run to, so its presence means an earlier run of
# this id got at least as far as promising something.
RUN_PLAN="$RUN_DIR/$RUN_PLAN_FILE"
RESUME_LOG="$RUN_DIR/resumes.jsonl"

# The run directory, once this run is the one inside it. See the reservation
# below: this is the lock, and it is a directory because `mkdir` is the test.
RUN_LOCK="$RUN_DIR/.holder"

# ---------------------------------------------------------------------------
# Reserving the run id.
#
# Reusing an id whose record exists must not overwrite that record silently.
# A check-then-create guard -
#
#     if [ -e "$RUN_PLAN" ]; then ... refuse ... fi     # ... later ...
#     mkdir -p "$RUN_DIR/workers"
#
# - only protects a run that has already finished, the case that was never
# dangerous, because its evidence is on disk and this run is about to walk past
# it anyway. Two runs of one id started at the same
# instant both saw no record, both passed, and both went on to share one
# run.json, one worker directory per worker, one checkpoint and one manifest.
# Started against a barrier, 30 pairs out of 30 both ran their workers.
#
# So the reservation is one step now. `mkdir` without `-p` either creates the
# directory or fails because it is already there, and the kernel does not
# interleave those, so of any number of racers exactly one gets it. The run
# directory *is* the reservation: it is the thing that has to be new, it is
# named by the id being claimed, and - unlike a lock file - it stays, so the
# claim outlives the process that made it, which is exactly what a run id needs.
#
# `mkdir` failing is not by itself a collision, though. A read-only output tree,
# a full disk and a missing parent all fail the same call, and telling a user
# their run id is taken when their disk is full sends them to change the one
# thing that is not the problem. So the failure path asks a second question -
# is the directory there now? - and answers "somebody else has this id" (exit 2)
# or "this directory could not be created" (exit 1).
#
# The parents are made with `-p` deliberately: `runs/` is shared by every run
# that ever wrote here and is nobody's to claim. Only the leaf is this run's.
# ---------------------------------------------------------------------------

# Two refusals, each written once, because the reservation can meet either of
# them and so can the resume path.
refuse_recorded_run() {
    echo "❌ Run '$RUN_ID' already has a record here:"
    echo "     $RUN_PLAN"
    echo "   That file says what that run set out to do, and the aggregator"
    echo "   judges the run against it. Starting again under the same id"
    echo "   would overwrite it, so this run refuses rather than destroy it."
    echo "   To continue that run:      GENOAR_RESUME=1"
    echo "   To start a separate run:   unset GENOAR_RUN_ID (one is minted)"
}

# The third refusal: a resume that has nothing to resume.
#
# GENOAR_RESUME=1 is not a preference, it is a statement about a run that
# exists - "continue *that* one". A run exists here once its run.json is
# written: that file is the plan the aggregator holds the run to, it records
# the split every checkpoint under the run belongs to, and the resume path
# above reads its worker count, requested end and distributed end straight out
# of it. Nothing below it can be continued without one. A run directory with no
# plan is a reservation, not a run - the same distinction refuse_claimed_run
# draws - so it does not satisfy this either.
#
# Ignoring the flag and starting a fresh run under the typed id is how someone
# recovering a long crawl after a crash silently restarted it from page 1: the
# id they meant still sits there untouched, and the one they typed now has a
# run of its own.
refuse_absent_run() {
    echo "❌ GENOAR_RESUME=1 says to continue run '$RUN_ID', and no such run is"
    echo "   recorded here:"
    echo "     $RUN_PLAN"
    echo "   A run exists once its $RUN_PLAN_FILE is written. That file is the"
    echo "   plan the aggregator holds the run to and it records the split every"
    echo "   checkpoint under the run belongs to, so without it there is nothing"
    echo "   to give the workers back and nothing to continue."
    if [ -d "$RUN_DIR" ]; then
        echo "   The directory is there but holds no plan: another launcher may"
        echo "   have claimed this id moments ago, or one died before it wrote"
        echo "   one. Neither is a run to resume."
    fi
    list_recorded_runs
    echo "   Check the id for a typo, or drop GENOAR_RESUME to start a new run."
}

# What is actually here to resume. A mistyped id is the case this refusal
# exists for, so the ids that do exist are worth naming; a few of them is
# enough to spot the transposed digit, and the path is printed either way.
list_recorded_runs() {
    local index="$OUTPUT_DIR/runs"
    local plans=""
    local plan=""
    local name=""
    local shown=0

    if [ -d "$index" ]; then
        # No `head` in the pipeline: it would exit early on a long list and,
        # under `set -o pipefail`, turn a directory full of runs into "no run is
        # recorded here". The cap is applied while printing instead.
        plans=$(find "$index" -mindepth 2 -maxdepth 2 -name "$RUN_PLAN_FILE" \
            2>/dev/null | sort) || plans=""
    fi
    if [ -z "$plans" ]; then
        echo "   No run is recorded in $index at all."
        return 0
    fi

    echo "   Runs recorded in $index:"
    while IFS= read -r plan; do
        [ -n "$plan" ] || continue
        name=${plan%/*}
        name=${name##*/}
        shown=$((shown + 1))
        if [ "$shown" -gt 10 ]; then
            echo "     - ... and more; see $index"
            break
        fi
        echo "     - $name"
    done <<EOF
$plans
EOF
}

refuse_claimed_run() {
    echo "❌ Run '$RUN_ID' is already claimed here:"
    echo "     $RUN_DIR"
    echo "   That directory is a reservation of this id that is not this run's:"
    echo "   another launcher took it a moment ago and has not written its"
    echo "   $RUN_PLAN_FILE yet, or one died before it wrote one. Two runs under"
    echo "   one id share every worker directory, checkpoint and manifest below"
    echo "   it, so this run refuses rather than move in beside it."
    echo "   To start a separate run:   unset GENOAR_RUN_ID (one is minted)"
    echo "   If no run holds it, remove that directory and start again."
}

if ! mkdir -p "$OUTPUT_DIR/runs" 2>/dev/null; then
    echo "❌ The run index could not be created: $OUTPUT_DIR/runs"
    echo "   This is not a run id collision - nothing has claimed '$RUN_ID'."
    echo "   Check that $OUTPUT_DIR exists, is writable and has space."
    exit 1
fi

# Set once the reservation is this run's, so the cleanup below knows whether it
# is allowed to give the id back.
RESERVED=0
LOCK_HELD=0
PLAN_WRITTEN=0
RESUMING=0

# A resume of a run that is not here is refused before anything is reserved, so
# a mistyped id leaves nothing behind - not even the empty directory the
# reservation below would otherwise create and then have to take back.
if [ "${GENOAR_RESUME:-}" = "1" ] && [ ! -e "$RUN_PLAN" ]; then
    refuse_absent_run
    exit 2
fi

if mkdir "$RUN_DIR" 2>/dev/null; then
    # And the check that does not depend on the one above having been right.
    # The plan could have been removed between the two, and a resume that
    # created its own run directory is by definition resuming nothing.
    if [ "${GENOAR_RESUME:-}" = "1" ]; then
        # Nothing has been written into it: the trap that gives a claim back is
        # not installed yet, so this hands the id back itself.
        rmdir "$RUN_DIR" 2>/dev/null || true
        refuse_absent_run
        exit 2
    fi
    RESERVED=1
elif [ ! -d "$RUN_DIR" ]; then
    echo "❌ The run directory could not be created: $RUN_DIR"
    echo "   It is not there afterwards either, so this is not a run id"
    echo "   collision - nothing has claimed '$RUN_ID'. A read-only output"
    echo "   tree, a full disk or a missing parent all end here."
    exit 1
elif [ -e "$RUN_PLAN" ]; then
    if [ "${GENOAR_RESUME:-}" = "1" ]; then
        RESUMING=1
    else
        refuse_recorded_run
        exit 2
    fi
else
    # A directory with no plan in it. For a resume that is refuse_absent_run's
    # case - there is no run here - and the pre-check above has already dealt
    # with it; what is left is a fresh run meeting somebody else's claim.
    refuse_claimed_run
    exit 2
fi

# Giving the id back, when this run turns out never to have used it.
#
# A reservation with no plan written into it is evidence of nothing, and leaving
# one behind would refuse the next attempt at the same id for no reason at all -
# a user who typo'd an argument would be told their run id was taken by the run
# that just refused them. `rmdir` removes it only while it is still empty, so a
# run that got as far as writing its plan or its worker directories keeps its
# claim, which is the case where the claim means something.
release_run_claim() {
    local status=$?
    if [ "$LOCK_HELD" -eq 1 ]; then
        LOCK_HELD=0
        rmdir "$RUN_LOCK" 2>/dev/null || true
    fi
    if [ "$RESERVED" -eq 1 ] && [ "$PLAN_WRITTEN" -eq 0 ]; then
        rmdir "$RUN_DIR" 2>/dev/null || true
    fi
    return "$status"
}
trap release_run_claim EXIT

# The second half of the same question, for the path where the directory cannot
# be the answer.
#
# A resume enters a run directory that by definition already exists, so it has
# nothing to create atomically and the reservation above says nothing about it.
# Two resumes started together would put two sets of workers onto one set of
# checkpoints - the same defect, one door along. What is new in that case is
# occupancy, not the id, so the lock is a directory *inside* the run: same
# primitive, applied to the object that is new. A fresh run takes it too, so a
# resume cannot walk into a run that is still going.
#
# It is released when this script exits, which is also when its workers are
# gone: they are children of this shell and the monitor waits for all of them.
if mkdir "$RUN_LOCK" 2>/dev/null; then
    LOCK_HELD=1
elif [ -d "$RUN_LOCK" ]; then
    echo "❌ Run '$RUN_ID' is being run right now by another launcher:"
    echo "     $RUN_LOCK"
    echo "   Its workers own the checkpoints under this run. A second launcher"
    echo "   in the same run would hand them a page range they did not start,"
    echo "   so this one refuses."
    echo "   Wait for that run to finish, or start a separate run"
    echo "   (unset GENOAR_RUN_ID)."
    echo "   If nothing is running, that directory is stale: remove it."
    exit 2
else
    echo "❌ The run lock could not be created: $RUN_LOCK"
    echo "   This is not a collision - check that $RUN_DIR is writable."
    exit 1
fi

# The shared data area. SMTX/SRR/META hold every run ever made into this output
# directory and are never cleared, because a new run may not erase an earlier
# one's results. They are not part of the reservation and are created with -p.
mkdir -p "$OUTPUT_DIR/SMTX" "$OUTPUT_DIR/SRR" "$OUTPUT_DIR/META"

# One number out of a JSON file this script wrote. The newlines are removed
# first, so the same reader works on the flat form written below and on the
# indented form genoar_crawler.py writes; each of these keys appears once in a
# plan, so the match is unambiguous. Prints nothing when the key is absent or
# is not a number - `"available_pages": null` included - so the caller can tell
# "not recorded" from "zero".
json_number() {
    tr -d '\n' < "$2" \
        | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p" \
        | head -1
}

# requested_pages is an object, so its "end" is read out of that object rather
# than out of the file: a plain search would find whichever "end" came first.
json_requested_end() {
    tr -d '\n' < "$1" \
        | sed -n 's/.*"requested_pages"[^}]*"end"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
        | head -1
}

# ---------------------------------------------------------------------------
# What a resume is held to.
#
# A resume kept the original plan - correctly, it is what the aggregator
# measures the run against - and then rebuilt the worker assignments from the
# *current* WORKERS and page arguments. Nothing compared the two. Ask for three
# workers where the run had two, or for 40 pages where it asked for 100, and
# every checkpoint under the run is handed to whatever slice today's numbers
# make: worker 2 opens the checkpoint it wrote for pages 51-100 and continues it
# through pages 21-40, and the run's manifest then claims pages nobody crawled.
#
# What counts as a difference that matters is anything the split is computed
# from, because the split is what binds a checkpoint to a page range:
#
#   * the worker count - it is the divisor, so one more worker moves every
#     boundary;
#   * the requested end page - the dividend, same;
#   * the distributed end page - the dividend when GEO holds fewer pages than
#     were requested. This one is not asked of the caller at all: it is taken
#     from the plan and GEO is not re-probed, because GEO's corpus changes daily
#     and a larger answer today would move the boundaries just as surely as a
#     different argument would. The run is being held to its plan, so the plan's
#     own split is the one that stands.
#
# And then the check that does not depend on having reasoned any of that
# correctly: each worker's assignment.json records the slice it was actually
# given, and the recomputed slice has to equal it.
#
# The items per page, the output directory and the run id are not on the list:
# the first two do not enter the split, and the third is the run.
# ---------------------------------------------------------------------------
AVAILABLE_PAGES=""
if [ "$RESUMING" -eq 1 ]; then
    PLAN_WORKERS=$(json_number workers "$RUN_PLAN")
    PLAN_REQUESTED_END=$(json_requested_end "$RUN_PLAN")
    PLAN_DISTRIBUTED_END=$(json_number distributed_end "$RUN_PLAN")

    if ! is_positive_int "${PLAN_WORKERS:-}" \
        || ! is_count "${PLAN_REQUESTED_END:-}" \
        || ! is_count "${PLAN_DISTRIBUTED_END:-}"; then
        echo "❌ Run '$RUN_ID' cannot be resumed: its $RUN_PLAN_FILE does not"
        echo "   record how the run was split."
        echo "     $RUN_PLAN"
        echo "   Read back - workers: '${PLAN_WORKERS:-(absent)}', requested end:"
        echo "   '${PLAN_REQUESTED_END:-(absent)}', distributed end:"
        echo "   '${PLAN_DISTRIBUTED_END:-(absent)}'."
        echo "   A resume has to give every worker the slice it had the first"
        echo "   time, and a plan that does not say what the slices were cannot"
        echo "   be checked. Start a separate run (unset GENOAR_RUN_ID)."
        exit 2
    fi

    RESUME_DIFFS=()
    if [ "$WORKERS" != "$PLAN_WORKERS" ]; then
        RESUME_DIFFS[${#RESUME_DIFFS[@]}]="workers: the plan says $PLAN_WORKERS, this resume asks for $WORKERS"
    fi
    if [ "$TOTAL_PAGES" != "$PLAN_REQUESTED_END" ]; then
        RESUME_DIFFS[${#RESUME_DIFFS[@]}]="pages requested: the plan says 1-$PLAN_REQUESTED_END, this resume asks for 1-$TOTAL_PAGES"
    fi
    if [ "${#RESUME_DIFFS[@]}" -gt 0 ]; then
        echo "❌ This resume is not the run it would be entering:"
        for difference in "${RESUME_DIFFS[@]}"; do
            echo "     - $difference"
        done
        echo ""
        echo "   Run '$RUN_ID' was split by those numbers, and every checkpoint"
        echo "   under it belongs to a slice that split produced. Resuming with"
        echo "   different ones would hand a worker's checkpoint to a page range"
        echo "   it never started, and the run would then claim pages nobody"
        echo "   crawled."
        echo "   Resume it as it was:      $0 $PLAN_WORKERS $PLAN_REQUESTED_END $OUTPUT_DIR"
        echo "   Or start a separate run:  unset GENOAR_RUN_ID (one is minted)"
        exit 2
    fi

    AVAILABLE_PAGES=$(json_number available_pages "$RUN_PLAN")
    echo "📄 Resuming: the plan's own split stands - $PLAN_DISTRIBUTED_END page(s)"
    echo "   over $PLAN_WORKERS worker(s). GEO is not re-probed, because a"
    echo "   different answer today would move the boundaries the checkpoints"
    echo "   under this run were written against."
elif [ -n "${GENOAR_TOTAL_PAGES:-}" ]; then
    AVAILABLE_PAGES=$GENOAR_TOTAL_PAGES
    echo "📄 Using GENOAR_TOTAL_PAGES=$AVAILABLE_PAGES (corpus probe skipped)"
elif [ "${GENOAR_SKIP_PAGE_PROBE:-}" = "1" ]; then
    echo "📄 Corpus probe skipped (GENOAR_SKIP_PAGE_PROBE=1)"
else
    # Ask GEO once how many pages it actually holds, then split that. Each
    # worker still caps itself, so this is not what makes the run correct - it
    # is what stops the later workers of a 100-page request being started
    # against a 47-page corpus with nothing to do.
    echo "📄 Asking GEO how many pages the query currently holds..."
    PROBE_OUT=""
    PROBE_OUT=$("$PYTHON_BIN" genoar_crawler.py --report-pages -o "$OUTPUT_DIR" 2>&1) || PROBE_OUT=""
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
#
# A resume takes it from the plan instead of deriving it again: see above.
if [ "$RESUMING" -eq 1 ]; then
    DISTRIBUTED_PAGES=$PLAN_DISTRIBUTED_END
else
    DISTRIBUTED_PAGES=$TOTAL_PAGES
    if is_positive_int "${AVAILABLE_PAGES:-}" && [ "$AVAILABLE_PAGES" -lt "$TOTAL_PAGES" ]; then
        DISTRIBUTED_PAGES=$AVAILABLE_PAGES
        echo "ℹ️  Requested $TOTAL_PAGES page(s); GEO holds $AVAILABLE_PAGES, so"
        echo "   $DISTRIBUTED_PAGES page(s) are distributed across the workers."
    fi
fi

# Split the pages across the workers, spreading the remainder over the first
# few workers instead of dumping it all on the last one. Two things depend on
# getting this right: an end page of 0 means "crawl everything" to the crawler,
# so a worker must never be handed one by accident, and a worker with no pages
# at all must be reported as skipped rather than started.
PAGES_PER_WORKER=$((DISTRIBUTED_PAGES / WORKERS))
REMAINDER=$((DISTRIBUTED_PAGES % WORKERS))

# The slices themselves, computed once here and then used twice: to check a
# resume against what the run actually handed out the first time, and to start
# the workers. Computing them inside the launch loop instead is how a resume
# redistributes a run silently: by the time a worker is started on a different
# range there is nothing left to compare it with.
#
# A worker with no pages gets 0-0, which is not a range: the crawler reads an
# end page of 0 as "crawl everything", so such a worker is never started.
SLICE_START=()
SLICE_END=()
NEXT_PAGE=1
for ((i = 1; i <= WORKERS; i++)); do
    PAGE_COUNT=$PAGES_PER_WORKER
    if [ "$i" -le "$REMAINDER" ]; then
        PAGE_COUNT=$((PAGE_COUNT + 1))
    fi
    if [ "$PAGE_COUNT" -eq 0 ]; then
        SLICE_START[i]=0
        SLICE_END[i]=0
        continue
    fi
    SLICE_START[i]=$NEXT_PAGE
    SLICE_END[i]=$((NEXT_PAGE + PAGE_COUNT - 1))
    NEXT_PAGE=$((SLICE_END[i] + 1))
done

# The check that does not rely on having got the reasoning above right: what
# each worker was actually given, as it recorded it before it started.
if [ "$RESUMING" -eq 1 ]; then
    ASSIGNMENT_DIFFS=()
    for ((i = 1; i <= WORKERS; i++)); do
        RECORDED_ASSIGNMENT="$WORKERS_DIR/worker-$i/assignment.json"
        [ -e "$RECORDED_ASSIGNMENT" ] || continue
        WAS_START=$(json_number start_page "$RECORDED_ASSIGNMENT")
        WAS_END=$(json_number end_page "$RECORDED_ASSIGNMENT")
        if [ "${WAS_START:-}" != "${SLICE_START[i]}" ] \
            || [ "${WAS_END:-}" != "${SLICE_END[i]}" ]; then
            ASSIGNMENT_DIFFS[${#ASSIGNMENT_DIFFS[@]}]="worker $i: started on pages ${WAS_START:-?}-${WAS_END:-?}, this resume would give it ${SLICE_START[i]}-${SLICE_END[i]}"
        fi
    done
    if [ "${#ASSIGNMENT_DIFFS[@]}" -gt 0 ]; then
        echo "❌ This resume would move workers onto pages they never started:"
        for difference in "${ASSIGNMENT_DIFFS[@]}"; do
            echo "     - $difference"
        done
        echo ""
        echo "   Each worker's checkpoint under $WORKERS_DIR belongs to the"
        echo "   range in its assignment.json. Continuing it through a different"
        echo "   range leaves the pages in between crawled by nobody, and the"
        echo "   run's manifest would still claim them."
        echo "   Start a separate run instead: unset GENOAR_RUN_ID."
        exit 2
    fi
fi

echo "🔧 Configuration:"
echo "  - Run ID: $RUN_ID"
echo "  - Workers: $WORKERS"
echo "  - Pages requested: $TOTAL_PAGES"
echo "  - Pages distributed: $DISTRIBUTED_PAGES"
echo "  - Pages per worker: ~$PAGES_PER_WORKER"
echo "  - Output directory: $OUTPUT_DIR"
echo "  - Run directory: $RUN_DIR"

if [ "$DISTRIBUTED_PAGES" -lt "$WORKERS" ]; then
    echo ""
    echo "⚠️  Only $DISTRIBUTED_PAGES page(s) for $WORKERS workers."
    echo "   $((WORKERS - DISTRIBUTED_PAGES)) worker(s) will get no pages and are reported as skipped."
fi
echo ""

# The plan, on disk, before a worker starts. The aggregator reads it to know
# what the run promised; without it a run could only ever prove what it did.
#
# On a resume the original plan stands, untouched. It is what the run promised,
# and the aggregator measures coverage against it - rewriting it here would let
# a resume quietly redefine what the run had claimed it would do, so a run that
# fell short could be made to look complete by resuming it with a smaller
# request. The resume records itself beside the plan instead, one line per
# continuation, so a reader can tell the original run from what came after it.
if [ "$RESUMING" -eq 1 ]; then
    echo "♻️  Resuming run '$RUN_ID'. Its $RUN_PLAN_FILE stands as written;"
    echo "   this continuation is appended to $RESUME_LOG."
    printf '{"resumed_at": "%s", "workers": %s, "requested_end": %s, "distributed_end": %s, "available_pages": %s}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$WORKERS" "$TOTAL_PAGES" \
        "$DISTRIBUTED_PAGES" "${AVAILABLE_PAGES:-null}" >> "$RESUME_LOG"
else
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
fi

# From here the run has said what it set out to do, so its reservation stands
# even if the rest of this script fails: see release_run_claim above.
PLAN_WRITTEN=1

mkdir -p "$WORKERS_DIR"

# The top-level manifest points at the newest completed run. Clear it now so a
# run that fails cannot leave the previous run's manifest standing as this
# run's completion; each run's own manifest stays under runs/<id>/ either way.
rm -f "$OUTPUT_DIR/crawl_manifest.json"

# Worker bookkeeping, indexed 1..WORKERS.
STATUS_PENDING=-1
WORKER_PID=()
WORKER_RANGE=()
WORKER_STATUS=()
WORKER_DIR=()

# Start one worker and remember its PID. Called from the main shell (never a
# subshell) so that $! stays a child of this shell and `wait` can reach it.
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

    local resume_flag=()
    if [ "${GENOAR_RESUME:-}" = "1" ] && [ -f "$worker_dir/checkpoint.json" ]; then
        resume_flag=(--resume)
        echo "♻️  Worker $worker_id resumes from $worker_dir/checkpoint.json"
    fi

    echo "🔄 Starting worker $worker_id: pages $start_page-$end_page"

    GENOAR_RUN_ID="$RUN_ID" GENOAR_WORKER_ID="$worker_id" \
        "$PYTHON_BIN" genoar_crawler.py "$start_page" "$end_page" true \
            -o "$OUTPUT_DIR" "${resume_flag[@]+"${resume_flag[@]}"}" \
        > "$worker_dir/worker.log" 2>&1 &

    WORKER_PID[worker_id]=$!
    WORKER_RANGE[worker_id]="$start_page-$end_page"
    WORKER_STATUS[worker_id]=$STATUS_PENDING
    WORKER_DIR[worker_id]="$worker_dir"
    echo "${WORKER_PID[worker_id]}" > "$worker_dir/pid"
}

# Start all workers, on the slices computed and checked above.
for ((i = 1; i <= WORKERS; i++)); do
    if [ "${SLICE_START[i]}" -eq 0 ]; then
        echo "⏭️  Worker $i: no pages left to assign, not started"
        WORKER_PID[i]=0
        WORKER_RANGE[i]="none"
        WORKER_STATUS[i]=3
        WORKER_DIR[i]=""
        continue
    fi

    run_worker "$i" "${SLICE_START[i]}" "${SLICE_END[i]}"
    sleep "$START_DELAY"
done

echo ""
echo "✅ All workers started!"

# Wiring tests launch the workers against a stub interpreter and stop here; the
# monitoring loop below only ends when every worker has exited.
if [ "${GENOAR_SKIP_MONITOR:-}" = "1" ]; then
    exit 0
fi
echo ""
echo "📊 Monitor progress:"
echo "  - View logs: tail -f $WORKERS_DIR/worker-*/worker.log"
echo "  - Check running workers: ps aux | grep genoar_crawler.py"
echo "  - Worker exit codes: cat $WORKERS_DIR/worker-*/exit_code"
echo "  - Count files: find $OUTPUT_DIR -type f | wc -l"
echo ""

# Count files matching a pattern. Never fails and never prints whitespace, so
# the caller can use the result in arithmetic without `set -e` surprises.
#
# This counts what is in the directory, which is every run that ever wrote
# there: SMTX/SRR/META are shared on purpose, because a new run must not erase
# an earlier one's results. So it answers "what is on disk", never "what did
# this run collect" - that question is answered by the run manifest, through
# run_report_value below.
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

# Monitoring loop
echo "⏰ Monitoring workers - a status update every ${POLL_SECONDS}s."
echo "   (Ctrl+C stops the monitor; it also interrupts the workers started from this terminal.)"
echo ""

while true; do
    sleep "$POLL_SECONDS"

    echo "📊 Status Update - $(date '+%H:%M:%S')"

    RUNNING=0
    for ((i = 1; i <= WORKERS; i++)); do
        if [ "${WORKER_STATUS[i]}" -eq "$STATUS_PENDING" ]; then
            PID=${WORKER_PID[i]}
            if kill -0 "$PID" 2>/dev/null; then
                # Never use ((RUNNING++)) here: it evaluates to 0 on the first
                # increment, which returns a non-zero status and would kill this
                # script under `set -e` while every worker is perfectly healthy.
                RUNNING=$((RUNNING + 1))
                echo "  Worker $i (pages ${WORKER_RANGE[i]}): 🟢 running (PID: $PID)"
                continue
            fi

            # The process is gone. Collect its real exit status - this is the
            # whole point: a dead worker and a finished one look identical
            # otherwise. The aggregator reads the same number back from disk.
            EXIT_CODE=0
            wait "$PID" 2>/dev/null || EXIT_CODE=$?
            WORKER_STATUS[i]=$EXIT_CODE
            echo "$EXIT_CODE" > "${WORKER_DIR[i]}/exit_code"
        fi

        if [ "${WORKER_RANGE[i]}" = "none" ]; then
            echo "  Worker $i: ⏭️  skipped - no pages assigned"
        else
            echo "  Worker $i (pages ${WORKER_RANGE[i]}): $(describe_status "${WORKER_STATUS[i]}")"
        fi
    done

    echo "  Active workers: $RUNNING/$WORKERS"

    SMTX_COUNT=$(count_files "$OUTPUT_DIR/SMTX" '*.gz')
    SRR_COUNT=$(count_files "$OUTPUT_DIR/SRR" '*.txt')
    META_COUNT=$(count_files "$OUTPUT_DIR/META" '*.txt')
    echo "  Files in $OUTPUT_DIR, all runs - SMTX: $SMTX_COUNT, SRR: $SRR_COUNT, META: $META_COUNT"
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

case "$OUTPUT_DIR" in
    /*) OUTPUT_PATH=$OUTPUT_DIR ;;
    *)  OUTPUT_PATH="$(pwd)/$OUTPUT_DIR" ;;
esac

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
echo "📁 Output directory: $OUTPUT_PATH"
echo "📋 Run directory: $RUN_DIR"
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
        echo "   - Worker $i: $(describe_status "${WORKER_STATUS[i]}") - see $WORKERS_DIR/worker-${i}/worker.log"
    done
    echo "   No whole-range manifest was written: this run did not complete."
    exit 1
fi

if [ "$SUCCEEDED" -eq 0 ]; then
    echo "⚠️  Nothing was crawled: no worker had any page to process."
    echo "   This is not a successful run - check the requested page range."
    exit 3
fi

# One place, once, after every worker has exited: check that the slices add up
# and write the run's manifest. A partially completed run gets no manifest.
#
# Its stdout is captured for the one GENOAR_RUN_REPORT line, which says what
# this run covered and collected; everything it logs still goes to stderr and
# straight to the terminal.
echo "🧮 Aggregating the run's workers..."
AGG_RC=0
AGG_OUT=""
AGG_OUT=$("$PYTHON_BIN" genoar_crawler.py --aggregate --run-id "$RUN_ID" -o "$OUTPUT_DIR") || AGG_RC=$?

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
# is in the output directory, which is every run that ever wrote there. Both
# are worth showing; only the first says anything about this run, and only the
# first may decide its exit status.
RUN_SMTX=$(run_report_value collected_smtx)
RUN_SRR=$(run_report_value collected_srr)
RUN_META=$(run_report_value collected_meta)
RUN_TOTAL=$(run_report_value collected_total)

SMTX_COUNT=$(count_files "$OUTPUT_DIR/SMTX" '*.gz')
SRR_COUNT=$(count_files "$OUTPUT_DIR/SRR" '*.txt')
META_COUNT=$(count_files "$OUTPUT_DIR/META" '*.txt')
TOTAL_FILES=$((SMTX_COUNT + SRR_COUNT + META_COUNT))

echo ""
if is_count "$RUN_TOTAL"; then
    echo "  Files collected by this run:"
    echo "    - SMTX files: ${RUN_SMTX:-0}"
    echo "    - SRR files: ${RUN_SRR:-0}"
    echo "    - META files: ${RUN_META:-0}"
    echo "    - Total: $RUN_TOTAL"
else
    echo "  Files collected by this run: not reported by the aggregator."
    printf '%s\n' "$AGG_OUT"
fi
echo ""
echo "  Files in $OUTPUT_PATH (all runs, including earlier ones):"
echo "    - SMTX files: $SMTX_COUNT"
echo "    - SRR files: $SRR_COUNT"
echo "    - META files: $META_COUNT"
echo "    - Total: $TOTAL_FILES"
echo ""

if ! is_count "$RUN_TOTAL"; then
    echo "⚠️  This run's own file count could not be read, so there is nothing"
    echo "   to show that it collected anything. The files above may all be"
    echo "   earlier runs'. Check the worker logs before trusting this run."
    exit 4
fi

if [ "$RUN_TOTAL" -eq 0 ]; then
    echo "⚠️  Workers finished cleanly but this run collected 0 files."
    echo "   Something is wrong upstream (downloads blocked, wrong output directory,"
    echo "   or no matching records). Check the worker logs before trusting this run."
    if [ "$TOTAL_FILES" -gt 0 ]; then
        echo "   The $TOTAL_FILES file(s) in the output directory are earlier runs'."
    fi
    exit 4
fi

echo "🎉 All workers completed: $SUCCEEDED succeeded, $SKIPPED skipped."
echo "   This run collected $RUN_TOTAL file(s) into $OUTPUT_PATH."
