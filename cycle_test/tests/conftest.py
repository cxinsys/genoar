"""A fake cluster for the Stage 3 HPC tests.

No cluster is available to this repository, and nothing that reads this file
needs one. The `fake_cluster` fixture puts stand-ins for `ssh`, `scp`, `rsync`,
`sbatch`, `squeue`, `sacct`, `qsub` and `qstat` on PATH and gives back a
directory that plays the part of the remote filesystem.

The fake `ssh` runs the command it is handed locally. That is what makes this
worth more than patching: the commands the wrapper builds are the commands that
execute, and the text the wrapper parses is the text a scheduler prints. A
change to a command string or to an output format fails a test here.

The fake `sbatch` and `qsub` write the run record the pipeline would have
written, under the run id they read out of the generated job script. One
submission therefore exercises the whole identification path: the generator
names the run, the job script carries the name, and the wrapper reads the record
by that name.

Everything the fakes do is steered by environment variables, so a test states
its scenario and never reaches inside the code under test.

| Variable | Effect |
|---|---|
| `GENOAR_FAKE_SSH_RC` | ssh fails with this code |
| `GENOAR_FAKE_TESTONLY_RC` | `sbatch --test-only` refuses the request |
| `GENOAR_FAKE_SUBMIT_RC` | the submit command fails |
| `GENOAR_FAKE_SUBMIT_OUT` | what the submit command prints |
| `GENOAR_FAKE_JOB_ID` | the job id it reports |
| `GENOAR_FAKE_QUEUE_RUNNING` | how many polls report the job still queued |
| `GENOAR_FAKE_SQUEUE_RC` | squeue fails once the job is gone |
| `GENOAR_FAKE_SACCT_RC` | sacct is unavailable |
| `GENOAR_FAKE_SACCT_OUT` | the accounting rows sacct prints |
| `GENOAR_FAKE_QSTAT_DETAIL` | what `qstat -x -f` prints |
| `GENOAR_FAKE_QSTAT_RC` | `qstat -x -f` fails, as it does for a purged job |
| `GENOAR_FAKE_RECORD` | a file the job copies in as its outcome.json |
"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

HPC_DIR = Path(__file__).resolve().parents[2] / "srr_pipeline_package" / "hpc"
if str(HPC_DIR) not in sys.path:
    sys.path.insert(0, str(HPC_DIR))


# Slurm accounting rows, in the six fields the wrapper asks sacct for.
SACCT_COMPLETED = (
    "4711|COMPLETED|0:0|02:14:31||120000M\n"
    "4711.batch|COMPLETED|0:0|02:14:31|98765432K|120000M\n"
)
SACCT_FAILED = (
    "4711|FAILED|1:0|00:03:12||120000M\n"
    "4711.batch|FAILED|1:0|00:03:12|524288K|120000M\n"
)
SACCT_OOM = (
    "4711|OUT_OF_MEMORY|0:125|01:02:03||120000M\n"
    "4711.batch|OUT_OF_MEMORY|0:125|01:02:03|122879000K|120000M\n"
)
SACCT_PARTIAL_EXIT = (
    "4711|COMPLETED|4:0|04:00:00||120000M\n"
    "4711.batch|COMPLETED|4:0|04:00:00|60000000K|120000M\n"
)


def qstat_detail(exit_status=0, jobid="4711.headnode"):
    """One `qstat -x -f` answer for a finished PBS job."""
    return (f"Job Id: {jobid}\n"
            "    job_state = F\n"
            f"    Exit_status = {exit_status}\n"
            "    resources_used.mem = 94371840kb\n"
            "    resources_used.walltime = 02:14:31\n")


FAKES = {
    # Runs the remote command locally, so the wrapper's own command strings are
    # what execute. Options carrying a value are dropped by name.
    "ssh": r"""#!/usr/bin/env bash
if [ "${GENOAR_FAKE_SSH_RC:-0}" != "0" ]; then
  echo "ssh: connect to host port 22: Connection refused" >&2
  exit "${GENOAR_FAKE_SSH_RC}"
fi
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    -p|-o|-i) shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
set -- "${args[@]}"
shift            # the destination
[ $# -eq 0 ] && exit 0
exec bash -c "$*"
""",
    "scp": r"""#!/usr/bin/env bash
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    -P|-o|-i) shift 2 ;;
    -r) shift ;;
    *) args+=("${1#*@*:}"); shift ;;
  esac
done
n=${#args[@]}
dest="${args[$((n-1))]}"
unset "args[$((n-1))]"
mkdir -p "$dest" 2>/dev/null
cp -R "${args[@]}" "$dest"
""",
    "rsync": r"""#!/usr/bin/env bash
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    -e) shift 2 ;;
    -*) shift ;;
    *) args+=("${1#*@*:}"); shift ;;
  esac
done
src="${args[0]}"
dst="${args[1]}"
mkdir -p "$dst"
[ -d "$src" ] || exit 0
cp -R "$src". "$dst" 2>/dev/null
exit 0
""",
    "sbatch": r"""#!/usr/bin/env bash
if [ "$1" = "--test-only" ]; then
  rc="${GENOAR_FAKE_TESTONLY_RC:-0}"
  if [ "$rc" != "0" ]; then
    echo "sbatch: error: Batch job submission failed: Invalid partition name specified" >&2
    exit "$rc"
  fi
  echo "sbatch: Job 4711 to start at 2026-08-13T12:00:00 using 16 processors"
  exit 0
fi
rc="${GENOAR_FAKE_SUBMIT_RC:-0}"
if [ "$rc" != "0" ]; then
  echo "sbatch: error: Batch job submission failed: Requested node configuration is not available" >&2
  exit "$rc"
fi
bash "$GENOAR_FAKE_WRITE_RECORD" "$1"
echo "${GENOAR_FAKE_SUBMIT_OUT:-Submitted batch job ${GENOAR_FAKE_JOB_ID:-4711}}"
""",
    "qsub": r"""#!/usr/bin/env bash
rc="${GENOAR_FAKE_SUBMIT_RC:-0}"
if [ "$rc" != "0" ]; then
  echo "qsub: Job rejected by all possible destinations" >&2
  exit "$rc"
fi
bash "$GENOAR_FAKE_WRITE_RECORD" "$1"
echo "${GENOAR_FAKE_SUBMIT_OUT:-${GENOAR_FAKE_JOB_ID:-4711}.headnode}"
""",
    # The job the fake schedulers "run": it leaves the record behind, under the
    # run id written into the job script.
    "write_record.sh": r"""#!/usr/bin/env bash
[ -n "${GENOAR_FAKE_RECORD:-}" ] || exit 0
run_id="$(sed -n 's/^RUN_ID="\(.*\)"$/\1/p' "$1" | head -1)"
[ -n "$run_id" ] || run_id="run-this-one"
shared_results="$(sed -n 's/^SHARED_RESULTS="\(.*\)"$/\1/p' "$1" | head -1)"
if [ -z "$shared_results" ]; then
  echo "fake scheduler: generated job script names no SHARED_RESULTS" >&2
  exit 91
fi
mkdir -p "$shared_results/runs/$run_id"
cp "$GENOAR_FAKE_RECORD" "$shared_results/runs/$run_id/outcome.json"
""",
    "squeue": r"""#!/usr/bin/env bash
counter="${GENOAR_FAKE_STATE_DIR:-/tmp}/poll.count"
seen=0
[ -f "$counter" ] && seen="$(cat "$counter")"
seen=$((seen + 1))
echo "$seen" > "$counter"
if [ "$seen" -le "${GENOAR_FAKE_QUEUE_RUNNING:-0}" ]; then
  echo "RUNNING"
  exit 0
fi
exit "${GENOAR_FAKE_SQUEUE_RC:-0}"
""",
    "sacct": r"""#!/usr/bin/env bash
rc="${GENOAR_FAKE_SACCT_RC:-0}"
if [ "$rc" != "0" ]; then
  echo "sacct: error: Problem talking to the database" >&2
  exit "$rc"
fi
printf '%s' "${GENOAR_FAKE_SACCT_OUT:-}"
""",
    "qstat": r"""#!/usr/bin/env bash
if [ "$1" = "-x" ]; then
  printf '%s' "${GENOAR_FAKE_QSTAT_DETAIL:-}"
  exit "${GENOAR_FAKE_QSTAT_RC:-0}"
fi
counter="${GENOAR_FAKE_STATE_DIR:-/tmp}/poll.count"
seen=0
[ -f "$counter" ] && seen="$(cat "$counter")"
seen=$((seen + 1))
echo "$seen" > "$counter"
if [ "$seen" -le "${GENOAR_FAKE_QUEUE_RUNNING:-0}" ]; then
  echo "Job id            Name          User  Time Use S Queue"
  echo "${GENOAR_FAKE_JOB_ID:-4711}.headnode  genoar_cellranger  myid  00:00:01 R normal"
  exit 0
fi
# PBS no longer knows the job, which is how a finished job looks.
exit 1
""",
}


class FakeCluster:
    """The stand-in cluster one test drives."""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.env = monkeypatch
        self.sra = tmp_path / "sra"
        self.sra.mkdir(exist_ok=True)
        (self.sra / "SRR22351028.sra").write_text("fake")
        self.results = tmp_path / "stage3_results"
        self.bundle = tmp_path / "hpc_handoff"
        self.remote = tmp_path / "remote"

    def config(self, scheduler="slurm", **over):
        cfg = {
            "host": "kbds.example.kr",
            "user": "myid",
            "remote_base": str(self.remote),
            "remote_sif": str(self.tmp / "genoar.sif"),
            "remote_ref": str(self.tmp / "ref"),
            "remote_cellranger": str(self.tmp / "cellranger"),
            "transfer_tool": "rsync",
            "scheduler": scheduler,
            "queue": "cpu" if scheduler == "slurm" else "normal",
            "ncpus": 16,
            "mem_mb": 120000,
            "mem_gb": 120,
            "walltime": "24:00:00",
            "cellranger_threads": 16,
            "cellranger_mem": 100,
        }
        cfg.update(over)
        return cfg

    def plant_record(self, data):
        """Hand the fake scheduler the record its job leaves behind."""
        path = self.tmp / "record.json"
        path.write_text(json.dumps(data) if isinstance(data, dict) else data)
        self.env.setenv("GENOAR_FAKE_RECORD", str(path))
        return path

    def plant_earlier_runs(self, *run_ids):
        """Records already on the persistent results volume, from earlier runs."""
        runs = self.remote / "results" / "runs"
        for name in run_ids:
            (runs / name).mkdir(parents=True, exist_ok=True)
            (runs / name / "outcome.json").write_text(json.dumps({
                "run_id": name, "counts": {"expected": 2, "completed": 2},
                "expected_samples": ["SRR0", "SRR1"], "samples": {}}))

    def setenv(self, name, value):
        self.env.setenv(name, str(value))


@pytest.fixture
def fake_cluster(tmp_path, monkeypatch):
    """Stand-in scheduler binaries on PATH and a directory playing the remote host."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in FAKES.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GENOAR_FAKE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("GENOAR_FAKE_WRITE_RECORD", str(bindir / "write_record.sh"))
    monkeypatch.setenv("GENOAR_FAKE_SACCT_OUT", SACCT_COMPLETED)
    monkeypatch.setenv("GENOAR_FAKE_QSTAT_DETAIL", qstat_detail(0))
    return FakeCluster(tmp_path, monkeypatch)
