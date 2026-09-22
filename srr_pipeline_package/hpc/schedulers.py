#!/usr/bin/env python3
"""
One place that knows what each batch scheduler wants.

Both halves of the Stage 3 handoff read this module. `prepare_hpc_handoff.py`
renders the job script from it. `run_hpc_stage3.py` builds the submit, poll and
report commands from it and parses the answers with it. A second copy of these
rules is what produced the situation this module exists to end: a PBS path
nobody ran and a Slurm bundle that only one site ever saw.

Slurm
-----
The Slurm output here reproduces the bundle that was validated against K-BDS
(the one-sample pilot). The directive block, the submission sequence and the
`sacct --format` fields are the pilot's, in the pilot's order. A config that
names the pilot's settings generates the pilot's `#SBATCH` block byte for byte.
`cycle_test/tests/test_hpc_scheduler_support.py` pins that, so a change to
either side fails a test instead of surfacing on a cluster months later.

Three Slurm facts shape the code.

Slurm reads `#SBATCH` lines before any shell runs, so a variable written there
is never expanded. Every directive carries a concrete value, resolved when the
bundle is generated.

Slurm's directive parser splits on whitespace, so a working directory holding a
space truncates `--output` and `--error` without a word of complaint. The
generator refuses such a path instead.

Slurm records the final state, the elapsed time and the peak memory in the
accounting database, and `squeue` forgets the job as soon as it leaves the
queue. Peak memory is readable from `sacct` and from nowhere else.

PBS
---
The PBS directive block is unchanged from the version that shipped before Slurm
support. The job body is shared between the two schedulers, which does change
the PBS script. The change is listed in the README and asserted in the tests.
The exit status the job reports is the pipeline's own, on PBS as before.
"""

import re
import site_settings

# The dialects this module renders. "slurm" is the default because it is the
# one verified end to end: the Slurm path has submitted, run and been read back
# on a real cluster. The PBS dialects are covered by the rendering tests but
# have not been verified by an actual submission. All three are selected by
# name; nothing here branches on the site.
SCHEDULERS = ("slurm", "pbspro", "torque")
DEFAULT_SCHEDULER = "slurm"

# Slurm job states that mean the job is over.
SLURM_TERMINAL = {
    "COMPLETED", "FAILED", "TIMEOUT", "OUT_OF_MEMORY", "CANCELLED",
    "NODE_FAIL", "PREEMPTED", "BOOT_FAIL", "DEADLINE", "REVOKED",
    "SPECIAL_EXIT",
}

# Slurm job states that are a failure whatever exit code accompanies them. A job
# the OOM killer takes is the case that matters: Slurm records OUT_OF_MEMORY and
# an ExitCode of 0:125, so the exit code alone reads as clean.
SLURM_FAILED = SLURM_TERMINAL - {"COMPLETED"}

# What sacct is asked for, and in this order. This is the command the pilot
# prints for the operator, so the fields an operator reads by hand and the
# fields this code parses are the same fields.
SACCT_FORMAT = "JobID,State,ExitCode,Elapsed,MaxRSS,ReqMem"

# Exit codes the generated job body uses for its own failures. They sit outside
# the pipeline's contract (0, 1, 2, 3, 4) on purpose. Exit 4 means "partial" to
# the wrapper, so a job body that reported a missing bind path as 4 would be
# read as a run that analysed some of its samples.
EXIT_NOT_VISIBLE = 41
# Retention did not free what the plan assumed. Outside the pipeline's own
# contract, like the other job-level codes, so it can never be read as a
# partial analysis.
EXIT_RETENTION = 43
# A different array task has already invalidated the array's disk plan. This
# is not the failed task's pipeline status; it says this task deliberately did
# not start.
EXIT_ARRAY_STOPPED = 44
EXIT_SIGNALLED = 42


def _array_fail_stop_enabled(cfg: dict) -> bool:
    """Every array shares a quota and stops after an earlier task fails."""
    return bool(array_size(cfg))


def scheduler_of(cfg: dict) -> str:
    """The dialect this config asks for."""
    value = cfg.get("scheduler") or DEFAULT_SCHEDULER
    return str(value).strip()


def is_slurm(cfg: dict) -> bool:
    return scheduler_of(cfg) == "slurm"


def validate(cfg: dict) -> None:
    """Raise ValueError on a config this module cannot render."""
    name = scheduler_of(cfg)
    if name not in SCHEDULERS:
        raise ValueError(
            "scheduler must be one of " + ", ".join(f"'{s}'" for s in SCHEDULERS)
            + f" (got '{name}')")
    if name == "slurm":
        base = str(cfg.get("remote_base", ""))
        if re.search(r"\s", base):
            raise ValueError(
                f"remote_base contains whitespace: '{base}'. Slurm splits "
                "#SBATCH lines on whitespace, so it cannot express this path in "
                "--output or --error. Choose a remote_base without spaces.")

    def positive_int(key):
        raw = cfg.get(key)
        if isinstance(raw, bool):
            raise ValueError(f"{key} must be a positive integer")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a positive integer") from None
        if value <= 0 or (isinstance(raw, float) and not raw.is_integer()):
            raise ValueError(f"{key} must be a positive integer")
        return value

    # These values describe one allocation and the Cell Ranger process inside
    # it.  Accepting a smaller allocation than the process is configured to use
    # only moves a deterministic configuration error into an expensive remote
    # queue.  Validate the relationship before any bundle files are written.
    ncpus = positive_int("ncpus")
    threads = positive_int("cellranger_threads")
    # `mem_mb` is the exact Slurm value preserved from the validated pilot.
    # The existing PBS contract is expressed in `mem_gb`, so validate the
    # amount that each renderer will actually request rather than a key it
    # ignores.
    use_mem_mb = name == "slurm" and cfg.get("mem_mb") not in (None, "")
    requested_mem_mb = (positive_int("mem_mb") if use_mem_mb
                        else positive_int("mem_gb") * 1024)
    cellranger_mem_mb = positive_int("cellranger_mem") * 1024
    if ncpus < threads:
        raise ValueError(
            f"ncpus ({ncpus}) must be at least cellranger_threads ({threads})")
    if requested_mem_mb <= cellranger_mem_mb:
        key = "mem_mb" if use_mem_mb else "mem_gb"
        raise ValueError(
            f"{key} must request more memory than cellranger_mem "
            f"({positive_int('cellranger_mem')} GB) so the job has overhead")
    try:
        site_settings.walltime(cfg.get("walltime"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"walltime {exc}") from None


def job_script_name(cfg: dict) -> str:
    """The bundle file the scheduler is handed."""
    return "run_stage3.slurm" if is_slurm(cfg) else "run_stage3.pbs"


def job_name(cfg: dict) -> str:
    """The name the job carries in the queue."""
    return "genoar_stage3" if is_slurm(cfg) else "genoar_cellranger"


def mem_mb(cfg: dict) -> int:
    """Job memory in MB.

    `mem_mb` wins when it is set. It exists so a site can request the exact
    figure its operator quoted. `mem_gb` is the older key and stays supported.
    """
    if cfg.get("mem_mb"):
        return int(cfg["mem_mb"])
    return int(cfg["mem_gb"]) * 1024


def results_dir(cfg: dict) -> str:
    """Persistent remote results shared by run-scoped work directories."""
    return str(cfg.get("_genoar_results_dir") or
               (str(cfg["remote_base"]).rstrip("/") + "/results"))


# ---------------------------------------------------------------------------
# Directive blocks
# ---------------------------------------------------------------------------

def render_directives(cfg: dict) -> str:
    """The scheduler directive block, shebang included, without a trailing blank line."""
    validate(cfg)
    if is_slurm(cfg):
        return _render_sbatch(cfg)
    return _render_pbs(cfg)


def array_size(cfg: dict):
    """How many array tasks this bundle is for, or None for a single job.

    A corpus does not fit in one job -- not on the disk and not inside the wall
    clock -- so it is split, and the split is submitted as an array rather than
    as N submissions. The operator running it is doing us a favour and cannot
    be asked to type qsub seventy times.
    """
    count = cfg.get("array_count")
    return int(count) if count else None


def can_throttle_array(cfg: dict) -> bool:
    """Whether the job script itself can cap how many tasks run at once.

    Slurm's `--array=1-N%K` does. PBS Pro has no directive for it -- the limit
    is a server-side queue setting, not something a submitted script can ask
    for -- so a PBS bundle has to be planned for every task running together.
    Emitting the number in the documentation while the scheduler ignores it is
    how a quota sized for one task meets seventy.
    """
    return is_slurm(cfg)


def max_concurrent(cfg: dict) -> int:
    """How many array tasks may run at once. One unless the site says otherwise.

    One by default because the tasks share a filesystem and a quota, and the
    quota is what the batch size was calculated against: two tasks at once need
    twice the disk the plan asked for. A site with room says so explicitly.
    """
    return int(cfg.get("max_concurrent_jobs") or 1)


def _render_sbatch(cfg: dict) -> str:
    """The pilot's #SBATCH block, from a config instead of from preflight.env.

    Order matters here only because it is the order the validated bundle used.
    Holding to it keeps the two comparable line by line.
    """
    base = cfg["remote_base"]
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name={job_name(cfg)}",
        f"#SBATCH --partition={cfg['queue']}",
        "#SBATCH --nodes=1",
        "#SBATCH --ntasks=1",
        f"#SBATCH --cpus-per-task={int(cfg['ncpus'])}",
        f"#SBATCH --mem={mem_mb(cfg)}M",
        f"#SBATCH --time={cfg['walltime']}",
        f"#SBATCH --output={base}/logs/slurm-%j.out",
        f"#SBATCH --error={base}/logs/slurm-%j.err",
    ]
    count = array_size(cfg)
    if count:
        # %A_%a, so a failed task can be found by its number rather than by
        # reading seventy identically named logs.
        lines[-2] = f"#SBATCH --output={base}/logs/slurm-%A_%a.out"
        lines[-1] = f"#SBATCH --error={base}/logs/slurm-%A_%a.err"
        lines.append(f"#SBATCH --array=1-{count}%{max_concurrent(cfg)}")
    if cfg.get("account"):
        lines.append(f"#SBATCH --account={cfg['account']}")
    if cfg.get("qos"):
        lines.append(f"#SBATCH --qos={cfg['qos']}")
    # Mail is a product addition. The pilot was hand-delivered and watched, so
    # it had no use for it. It comes last, which keeps a config without an email
    # address identical to the pilot's block.
    if cfg.get("email"):
        lines.append("#SBATCH --mail-type=BEGIN,END,FAIL")
        lines.append(f"#SBATCH --mail-user={cfg['email']}")
    return "\n".join(lines)


def _render_pbs(cfg: dict) -> str:
    ncpus, mem, wt = int(cfg["ncpus"]), int(cfg["mem_gb"]), cfg["walltime"]
    if scheduler_of(cfg) == "pbspro":
        resources = f"#PBS -l select=1:ncpus={ncpus}:mem={mem}gb\n#PBS -l walltime={wt}"
    else:  # torque
        resources = f"#PBS -l nodes=1:ppn={ncpus}\n#PBS -l mem={mem}gb\n#PBS -l walltime={wt}"
    email_lines = (f"#PBS -m abe\n#PBS -M {cfg['email']}"
                   if cfg.get("email") else "# (no email notification configured)")
    count = array_size(cfg)
    # Named output files under an array would have every subjob writing the
    # same two paths. Left unset, PBS names them per subjob itself.
    output_lines = ("# (array subjobs use PBS's own per-task output names)"
                    if count else "#PBS -o pbs_stage3.out\n#PBS -e pbs_stage3.err")
    array_line = ""
    if count:
        # PBS Pro and Torque use different array directives and index
        # variables. Both are rendered here so the transfer script's ordinary
        # `qsub run_stage3.pbs` really submits every planned batch.
        array_flag = "-J" if scheduler_of(cfg) == "pbspro" else "-t"
        array_line = f"\n#PBS {array_flag} 1-{count}"
        array_line += ("\n# This PBS dialect has no portable per-script "
                       "concurrency cap. If the site\n# needs a limit, ask the "
                       "queue administrator for one; the plan for this "
                       "bundle\n# assumes every task may run at the same time.")
    return (f"""#!/usr/bin/env bash
#PBS -N {job_name(cfg)}
#PBS -q {cfg['queue']}
{resources}
{email_lines}
{output_lines}{array_line}""")


# ---------------------------------------------------------------------------
# Job body
# ---------------------------------------------------------------------------

def _render_batch_block(cfg: dict, run_id: str) -> str:
    """One array task's share of the work, or nothing at all.

    A bundle with no batches is what it always was: one job over everything in
    data/sra. With batches, each task takes the list named for its index and
    works only on that.

    Every task gets its own input view, its own results root, its own log
    directory and its own run id. Not tidiness -- the pipeline refuses a run id
    that already names a run, which is precisely what seventy tasks sharing one
    id would be, and the eligibility reports it writes at the root of the
    results tree would be seventy tasks writing one file.

    The inputs are linked, not copied. A batch's share of a corpus is measured
    in hundreds of gigabytes, and copying it to arrange it is the disk the
    batch size was calculated to save.
    """
    if not array_size(cfg):
        return ""
    # SLURM_ARRAY_JOB_ID is CONSTANT across a requeue, so on its own it made
    # every automatic recovery -- node failure, preemption, scontrol requeue --
    # reproduce the first attempt's run id exactly. The pipeline then refused
    # the run, correctly, and a recoverable interruption became a lost batch.
    # SLURM_RESTART_COUNT is what distinguishes them.
    if is_slurm(cfg):
        attempt_setup = (
            'GENOAR_ATTEMPT="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-1}}'
            'r${SLURM_RESTART_COUNT:-0}"')
    else:
        # PBS job ids commonly include an array expression and server name,
        # e.g. 1234[7].server (PBS Pro) or 1234-7.server (Torque). '[' and ']'
        # are outside the pipeline's run-id alphabet. The leading numeric job
        # id is the stable submission identity; the batch number below already
        # distinguishes subjobs.
        attempt_setup = """GENOAR_ATTEMPT_RAW="${PBS_JOBID:-1}"
GENOAR_ATTEMPT="${GENOAR_ATTEMPT_RAW%%[!0-9]*}"
[ -n "$GENOAR_ATTEMPT" ] || GENOAR_ATTEMPT=1"""
    batch_expr = (
        "${SLURM_ARRAY_TASK_ID:-${PBS_ARRAY_INDEX:-"
        "${PBS_ARRAYID:-${GENOAR_BATCH:-1}}}}")
    stop_check = (f"""
# A failed array task can leave expanded reads or partial output that
# the plan did not budget for. Tasks already running cannot be recalled here,
# but anything that starts afterwards must not add another batch to that disk.
if ! mkdir -p "$LOG_DIR"; then
  echo "[job] FATAL: cannot create this task's log directory at $LOG_DIR" >&2
  exit {EXIT_NOT_VISIBLE}
fi
if [ -n "${{ARRAY_STOP_DIR:-}}" ] && [ -d "$ARRAY_STOP_DIR" ]; then
  echo "[job] FATAL: an earlier array task failed; see $ARRAY_STOP_DIR/failure.txt" >&2
  echo "[job] this task stopped before staging inputs because the disk plan is no longer valid" >&2
  STAGE="stopped after an earlier array failure"
  exit {EXIT_ARRAY_STOPPED}
fi
""" if _array_fail_stop_enabled(cfg) else "")
    return f"""
# --- this task's batch ------------------------------------------------------
STAGE="selecting this task's batch"
GENOAR_BATCH="{batch_expr}"

# The per-task paths are set before anything here can exit. A task that dies
# looking for its batch list still has to write its own job_status.txt, and
# while this came after the check every task wrote the base one, so the last
# failure erased what the others had recorded.
# The scheduler's job id is part of the run id, so a task re-submitted after a
# failure is a new run rather than a collision. The pipeline refuses an id that
# already has a record -- correctly -- and without this the documented way to
# re-run one failed task could not work once the pipeline had started.
{attempt_setup}
RUN_ID="{run_id}-b$GENOAR_BATCH-$GENOAR_ATTEMPT"

# Named for the run, and emptied first. A fixed path that nothing cleaned meant
# the previous plan's batch 1 was still staged when this plan's batch 1 arrived,
# and step 2 found both.
SRA_DIR="$RUN_DIR/data/staged/$RUN_ID"
RESULTS_DIR="$SHARED_RESULTS/batch_$GENOAR_BATCH"
LOG_DIR="$RUN_DIR/logs/batch_$GENOAR_BATCH-$GENOAR_ATTEMPT"
PIPELINE_LOGS="$LOG_DIR"
PIPELINE_RESULTS="$RESULTS_DIR"
{stop_check}
rm -rf "$SRA_DIR"
if ! mkdir -p "$SRA_DIR" "$RESULTS_DIR" "$LOG_DIR"; then
  # Unchecked, this is what a full quota looks like: the task dies and the
  # exit trap writes its record into a directory that does not exist.
  echo "[job] FATAL: cannot create this task's directories under $RUN_DIR" >&2
  echo "[job] the usual cause is the quota being full" >&2
  exit {EXIT_NOT_VISIBLE}
fi

BATCH_FILE="$RUN_DIR/batches/$(printf 'batch_%03d.txt' "$GENOAR_BATCH")"
if [ ! -f "$BATCH_FILE" ]; then
  echo "[job] FATAL: task $GENOAR_BATCH has no sample list at $BATCH_FILE" >&2
  echo "[job] transfer_and_submit.sh uploads batches/; a bundle built without" >&2
  echo "[job] --batches has no lists and must not be submitted as an array" >&2
  exit {EXIT_NOT_VISIBLE}
fi

# A real directory per sample, holding links to that sample's files -- not a
# link to the sample's directory. Step 2 finds its work with `find -type d`,
# which does not match a symlink, so staging by linked directory produced a
# batch that then processed nothing at all: exit 0, zero samples, no error
# anywhere to say why.
#
# Hard links where the filesystem allows them, symlinks where it does not.
# Either way the bytes are not copied, and a batch is hundreds of gigabytes.
staged=0
missing_inputs=""
while IFS= read -r sample; do
  [ -n "$sample" ] || continue
  src=""
  if [ -d "$RUN_DIR/data/sra/$sample" ]; then
    src="$RUN_DIR/data/sra/$sample"
  elif [ -f "$RUN_DIR/data/sra/$sample.sra" ]; then
    src="$RUN_DIR/data/sra/$sample.sra"
  fi
  if [ -z "$src" ]; then
    missing_inputs="$missing_inputs $sample"
    continue
  fi
  mkdir -p "$SRA_DIR/$sample"
  if [ -d "$src" ]; then
    for one in "$src"/*; do
      [ -e "$one" ] || continue
      ln "$one" "$SRA_DIR/$sample/$(basename "$one")" 2>/dev/null \
        || ln -sfn "$one" "$SRA_DIR/$sample/$(basename "$one")" || true
    done
  else
    ln "$src" "$SRA_DIR/$sample/$(basename "$src")" 2>/dev/null \
      || ln -sfn "$src" "$SRA_DIR/$sample/$(basename "$src")" || true
  fi
  staged=$((staged + 1))
done < "$BATCH_FILE"

# Refused, not warned. A short batch still reports N/N against its own
# manifest, so the shortfall shows up only when the whole corpus is added back
# together -- if anyone adds it. The batch is the unit of work, and an
# incomplete batch is not a smaller unit of work.
if [ -n "$missing_inputs" ]; then
  echo "[job] FATAL: batch $GENOAR_BATCH has no input for:$missing_inputs" >&2
  echo "[job] every sample the plan put in this batch has to be here, because" >&2
  echo "[job] this task's own accounting cannot see what was never staged" >&2
  exit {EXIT_NOT_VISIBLE}
fi
if [ "$staged" -eq 0 ]; then
  echo "[job] FATAL: batch $GENOAR_BATCH staged no samples" >&2
  exit {EXIT_NOT_VISIBLE}
fi
echo "[job] batch $GENOAR_BATCH: $staged sample(s) staged from $BATCH_FILE"
"""


def render_job_script(cfg: dict, run_id: str) -> str:
    """The whole job script: directive block, then the body both schedulers share.

    The body follows the pilot's job_body.sh. It arms an exit trap before
    anything can fail, checks that the compute node sees what the submitting
    node saw, and hands the run id to the pipeline. It ends on the pipeline's
    own exit code, so the scheduler records the code the pipeline's contract
    defines.
    """
    base = cfg["remote_base"]
    shared_results = results_dir(cfg)
    submit_dir = "${SLURM_SUBMIT_DIR:-$PWD}" if is_slurm(cfg) else "${PBS_O_WORKDIR:-$PWD}"
    job_id_var = "${SLURM_JOB_ID:-none}" if is_slurm(cfg) else "${PBS_JOBID:-none}"
    module_line = (f"module load {cfg['module_load']}" if cfg.get("module_load")
                   else "# (no module load configured)")
    batch_block = _render_batch_block(cfg, run_id)
    array_stop_setup = ('ARRAY_STOP_DIR="$RUN_DIR/.genoar-array-stop"'
                        if _array_fail_stop_enabled(cfg)
                        else 'ARRAY_STOP_DIR=""')
    record_array_failure = (f"""
  # The directory creation is the shared marker and is atomic on the shared
  # filesystem. Preserve the first real failure; tasks stopped by that marker
  # use {EXIT_ARRAY_STOPPED} and must not replace its cause.
  if [ "$rc" -ne 0 ] && [ "$rc" -ne {EXIT_ARRAY_STOPPED} ]; then
    if mkdir "$ARRAY_STOP_DIR" 2>/dev/null; then
      {{
        echo "run_id=$RUN_ID"
        echo "batch=${{GENOAR_BATCH:-none}}"
        echo "job_id={job_id_var}"
        echo "stage_reached=$STAGE"
        echo "pipeline_exit=$PIPELINE_RC"
        echo "job_exit=$rc"
      }} > "$ARRAY_STOP_DIR/failure.txt" 2>/dev/null || true
    fi
  fi
""" if _array_fail_stop_enabled(cfg) else "")
    # The container sees the staged copy, not the original, so releasing the
    # input from inside it removes a hard link and frees no blocks at all.
    # Removing the original is the job's to do, and only once the pipeline has
    # said the batch is done.
    # The retention check is not conditional on release_input: a rotation that
    # did not free what the plan assumed leaves the next task in the array
    # facing a disk nobody accounted for, whatever this task was going to do
    # with its own inputs.
    retention_check = ("""
RETENTION_MARK="$RESULTS_DIR/runs/$RUN_ID/retention_incomplete"
if [ -f "$RETENTION_MARK" ]; then
  echo "[job] FATAL: retention did not complete; see $RETENTION_MARK" >&2
  echo "[job] the inputs are left in place and this task stops here, because" >&2
  echo "[job] the disk the rest of the array was planned against is not free" >&2
  STAGE="retention incomplete"
  exit %d
fi
""" % EXIT_RETENTION) if array_size(cfg) else ""
    release_originals = (f"""
# --- the inputs this batch is finished with ---------------------------------
if [ "$PIPELINE_RC" = "0" ] && [ -n "${{BATCH_FILE:-}}" ]; then
  STAGE="releasing this batch's inputs"
  freed=0
  release_failed=""
  while IFS= read -r sample; do
    [ -n "$sample" ] || continue
    if rm -rf "$RUN_DIR/data/sra/$sample" "$RUN_DIR/data/sra/$sample.sra"; then
      freed=$((freed + 1))
    else
      release_failed="$release_failed $sample"
    fi
  done < "$BATCH_FILE"
  if [ -n "$release_failed" ]; then
    echo "[job] FATAL: failed to release input for:$release_failed" >&2
    echo "[job] the staged copies are left in place so this failed rotation is visible" >&2
    STAGE="input release failed"
    exit {EXIT_RETENTION}
  fi
  echo "[job] released the inputs of $freed sample(s); the staged copies go too,"
  echo "[job] because a hard link left behind frees nothing."
fi
""" if array_size(cfg) and site_settings.yes_or_no(cfg.get("release_input", False))
        else "")
    cleanup_staging = ("""
# The staged tree is task-scoped working data, independent of whether the site
# keeps the original inputs. A successful task has no later reader for it.
if [ "$PIPELINE_RC" = "0" ]; then
  STAGE="releasing this task's staged inputs"
  if ! rm -rf "$SRA_DIR"; then
    echo "[job] FATAL: failed to release the staged inputs at $SRA_DIR" >&2
    exit {EXIT_RETENTION}
  fi
  STAGE="complete"
fi

""".format(EXIT_RETENTION=EXIT_RETENTION) if array_size(cfg) else "")
    return f"""{render_directives(cfg)}

# Not `set -e`. This body reports what happened, so it has to survive the thing
# that happened. Every command that can fail is checked where it is called.
set -uo pipefail
cd "{submit_dir}"

RUN_DIR="{base}"
LOG_DIR="$RUN_DIR/logs"
SHARED_RESULTS="{shared_results}"
RESULTS_DIR="$SHARED_RESULTS"
RUN_ID="{run_id}"
{array_stop_setup}

# What the pipeline is handed. Relative, exactly as the validated bundle had
# them: the body has already cd'd to the submitting directory, and pinning
# these to RUN_DIR instead would send the output somewhere else on any site
# where the two are not the same place. An array task overrides all three.
SRA_DIR="./data/sra"
PIPELINE_LOGS="./logs"
PIPELINE_RESULTS="$SHARED_RESULTS"
mkdir -p "$LOG_DIR" "$RESULTS_DIR"

STAGE="starting"
PIPELINE_RC="not-reached"

# Armed before anything can fail, so the job cannot end without saying why. The
# scheduler's own accounting is not always there to ask: sacct needs an
# accounting database, and qstat forgets a finished job.
finish() {{
  rc=$?
{record_array_failure}
  {{
    echo "run_id=$RUN_ID"
    echo "job_id={job_id_var}"
    echo "stage_reached=$STAGE"
    echo "pipeline_exit=$PIPELINE_RC"
    echo "job_exit=$rc"
    echo "node=$(hostname 2>/dev/null)"
    echo "finished_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  }} > "$LOG_DIR/job_status.txt" 2>/dev/null
  # Free space at the moment of the failure. It answers most "why did it die"
  # questions and is unrecoverable afterwards.
  df -Pk "$RUN_DIR" > "$LOG_DIR/disk_at_exit.txt" 2>&1
}}
trap finish EXIT

# The scheduler sends SIGTERM before SIGKILL at the wall-clock limit. SIGKILL
# reaches no trap, which is why the wrapper also reads the scheduler's own
# record of the job.
on_signal() {{ echo "[job] received a signal during '$STAGE'" >&2; exit {EXIT_SIGNALLED}; }}
trap on_signal TERM INT HUP

{module_line}
{batch_block}
# A compute node does not always mount what the submitting node mounts. Asking
# now costs a second. Finding out later costs the queue time.
STAGE="checking compute-node visibility"
missing=""
for p in "{cfg['remote_sif']}" "{cfg['remote_ref']}" "{cfg['remote_cellranger']}" \\
         "$SRA_DIR" ./config.yaml ./run_singularity_pipeline.sh; do
  [ -e "$p" ] || missing="$missing $p"
done
if [ -n "$missing" ]; then
  echo "[job] FATAL: this compute node does not see:$missing" >&2
  echo "[job] the submitting node saw these, so this is a mount or path difference" >&2
  exit {EXIT_NOT_VISIBLE}
fi

# Give the pipeline this run's id. Stage 3 records what it produced under
# results/runs/<run id>/, and sharing the id is what lets the wrapper ask "what
# did THIS run produce" instead of inferring it from what appeared on disk.
# Both engines read the *ENV_ prefix. --env is newer than some sites' Singularity.
export SINGULARITYENV_GENOAR_RUN_ID="$RUN_ID"
export APPTAINERENV_GENOAR_RUN_ID="$RUN_ID"

STAGE="running the pipeline"
echo "[job] run id: $RUN_ID"
bash run_singularity_pipeline.sh \\
  --sif        "{cfg['remote_sif']}" \\
  --sra        "$SRA_DIR" \\
  --config     ./config.yaml \\
  --ref        "{cfg['remote_ref']}" \\
  --cellranger "{cfg['remote_cellranger']}" \\
  --logs       "$PIPELINE_LOGS" \\
  --results    "$PIPELINE_RESULTS"
PIPELINE_RC=$?
STAGE="complete"
echo "[job] the pipeline exited $PIPELINE_RC"
{retention_check}{release_originals}{cleanup_staging}

# The scheduler records this. It is the pipeline's own code, on the pipeline's
# own contract: 0 did the work, 1 failed, 2 misconfigured, 3 nothing to do, 4 partial.
exit "$PIPELINE_RC"
"""


# ---------------------------------------------------------------------------
# Commands the wrapper runs on the cluster
# ---------------------------------------------------------------------------

def test_only_command(cfg: dict):
    """A submission the scheduler judges without queueing anything, or None.

    Slurm answers this before a job exists, which is where a partition, an
    account, a QOS or a resource limit the site refuses is cheapest to find.
    PBS has no equivalent that is safe to rely on, so the PBS path skips it.
    """
    if not is_slurm(cfg):
        return None
    return f"cd {cfg['remote_base']} && sbatch --test-only {job_script_name(cfg)}"


def submit_command(cfg: dict) -> str:
    tool = "sbatch" if is_slurm(cfg) else "qsub"
    return f"cd {cfg['remote_base']} && {tool} {job_script_name(cfg)}"


def parse_job_id(cfg: dict, stdout: str):
    """The job id the scheduler printed, or None when it printed none."""
    text = (stdout or "").strip()
    if not text:
        return None
    if is_slurm(cfg):
        found = re.findall(r"Submitted batch job (\d+)", text)
        return found[-1] if found else None
    # PBS Pro prints "12345.headnode", Torque prints "12345".
    return text.split()[0]


def poll_command(cfg: dict, jobid: str) -> str:
    if is_slurm(cfg):
        return f"squeue -h -j {jobid} -o %T"
    return f"qstat {jobid}"


def parse_poll(cfg: dict, returncode: int, stdout: str, jobid: str) -> tuple:
    """Return (done, state) from the queue listing."""
    text = (stdout or "").strip()
    if is_slurm(cfg):
        # squeue drops a job the moment it leaves the queue, and errors once the
        # id is purged. Either answer means the job is no longer waiting.
        if returncode != 0 or not text:
            return True, "gone"
        state = text.splitlines()[0].strip()
        if state in SLURM_TERMINAL:
            return True, state
        return False, state
    if returncode != 0:
        # The job is no longer known to PBS, so it finished or was purged.
        return True, "gone"
    short = jobid_short(jobid)
    state = "?"
    for line in (stdout or "").splitlines():
        parts = line.split()
        if parts and parts[0].split(".")[0] == short and len(parts) >= 5:
            state = parts[-2]
    if state in ("C", "F"):  # Completed / Finished
        return True, state
    return False, state


def jobid_short(jobid: str) -> str:
    return str(jobid).split(".")[0]


def report_command(cfg: dict, jobid: str) -> str:
    """The command that reports a job the queue has already forgotten."""
    if is_slurm(cfg):
        # -n -P make the same fields parsable. The fields are the ones the
        # operator is told to run by hand, so both read the same numbers.
        return f"sacct -j {jobid} --format={SACCT_FORMAT} -n -P"
    return f"qstat -x -f {jobid}"


def human_report_command(cfg: dict, jobid: str) -> str:
    """The same query, in the form a person types."""
    if is_slurm(cfg):
        return f"sacct -j {jobid} --format={SACCT_FORMAT}"
    return f"qstat -x -f {jobid}"


def empty_report() -> dict:
    return {"exit_status": None, "state": None, "elapsed": None,
            "max_rss": None, "max_rss_kib": None, "req_mem": None,
            "signal": None, "source": None}


def parse_report(cfg: dict, returncode: int, stdout: str, jobid: str) -> dict:
    """What the scheduler says about a finished job.

    Every field is optional. sacct needs an accounting database the site may not
    run, qstat forgets finished jobs, and neither is reachable if SSH drops. A
    field this cannot read stays None, and the caller says so rather than
    guessing.
    """
    report = empty_report()
    if returncode != 0:
        return report
    if is_slurm(cfg):
        return _parse_sacct(stdout, jobid, report)
    return _parse_qstat(stdout, report)


def _parse_sacct(stdout: str, jobid: str, report: dict) -> dict:
    short = jobid_short(jobid)
    rows = []
    for line in (stdout or "").splitlines():
        fields = line.split("|")
        if len(fields) < 6:
            continue
        row_id = fields[0].strip()
        if row_id != short and not row_id.startswith(short + "."):
            continue
        rows.append([f.strip() for f in fields])
    if not rows:
        return report
    report["source"] = "sacct"

    head = next((r for r in rows if r[0] == short), rows[0])
    # "CANCELLED by 1234" names the user who cancelled it.
    report["state"] = head[1].split(" by ")[0].strip() or None
    exit_code, signal = _split_exit_code(head[2])
    report["exit_status"] = exit_code
    report["signal"] = signal
    report["elapsed"] = head[3] or None
    report["req_mem"] = head[5] or None

    # MaxRSS is recorded per step. The batch step holds the pipeline's own peak.
    best_kib, best_text = None, None
    for row in rows:
        kib = _to_kib(row[4])
        if kib is not None and (best_kib is None or kib > best_kib):
            best_kib, best_text = kib, row[4]
    report["max_rss"] = best_text
    report["max_rss_kib"] = best_kib
    return report


def _split_exit_code(value: str) -> tuple:
    """Slurm writes ExitCode as "<exit>:<signal>"."""
    text = (value or "").strip()
    if not text:
        return None, None
    parts = text.split(":")
    try:
        exit_code = int(parts[0])
    except ValueError:
        return None, None
    signal = None
    if len(parts) > 1:
        try:
            signal = int(parts[1])
        except ValueError:
            signal = None
    return exit_code, signal


_UNITS = {"K": 1, "M": 1024, "G": 1024 ** 2, "T": 1024 ** 3}


def _to_kib(value):
    """Normalise a scheduler's memory figure to KiB, or None if it cannot be.

    Both dialects state a unit: sacct writes MaxRSS as `98765432K` or `4G`, and
    PBS writes `resources_used.mem = 94371840kb`. A bare number is therefore
    not a figure in a known unit, it is a figure in an unknown one — and
    guessing wrong is out by a factor of 1024 in a number that gets read as
    "how close did this job come to its memory limit". The raw string is still
    reported alongside; only the derived number is withheld.
    """
    text = (value or "").strip()
    if not text:
        return None
    # Zero is the one figure that reads the same in every unit, so it does not
    # need one. sacct reports a bare 0 for a job that recorded no RSS.
    if re.fullmatch(r"0+(?:\.0+)?", text):
        return 0
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGTkmgt])[Bb]?", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).upper()
    return int(number * _UNITS[unit])


def _parse_qstat(stdout: str, report: dict) -> dict:
    """PBS reports the same three facts under different names, when it reports them."""
    for line in (stdout or "").splitlines():
        text = line.strip()
        lower = text.lower()
        if lower.startswith("exit_status"):
            try:
                report["exit_status"] = int(text.split("=", 1)[1].strip())
                report["source"] = "qstat"
            except (IndexError, ValueError):
                pass
        elif lower.startswith("resources_used.mem"):
            try:
                raw = text.split("=", 1)[1].strip()
            except IndexError:
                continue
            report["max_rss"] = raw
            report["max_rss_kib"] = _to_kib(raw)
            report["source"] = report["source"] or "qstat"
        elif lower.startswith("resources_used.walltime"):
            try:
                report["elapsed"] = text.split("=", 1)[1].strip()
            except IndexError:
                continue
            report["source"] = report["source"] or "qstat"
        elif lower.startswith("job_state"):
            try:
                report["state"] = text.split("=", 1)[1].strip()
            except IndexError:
                continue
    return report


def state_is_failure(cfg: dict, state) -> bool:
    """A job state that is a failure whatever exit code came with it.

    The out-of-memory case is why this exists. Slurm records OUT_OF_MEMORY with
    an ExitCode of 0:125, so a reader who trusts the exit code alone calls a
    killed job clean.
    """
    if not state or not is_slurm(cfg):
        return False
    return state in SLURM_FAILED


def watch_command(cfg: dict, user: str, jobid: str = None) -> str:
    """What a person runs to watch the job."""
    if is_slurm(cfg):
        return f"squeue -j {jobid}" if jobid else f"squeue -u {user}"
    return f"qstat {jobid}" if jobid else f"qstat -u {user}"


def submit_tool(cfg: dict) -> str:
    return "sbatch" if is_slurm(cfg) else "qsub"


def cancel_command(cfg: dict, jobid: str) -> str:
    return f"scancel {jobid}" if is_slurm(cfg) else f"qdel {jobid}"
