#!/usr/bin/env python3
"""
Run Stage 3 (Cell Ranger) on an HPC, with fallback to the manual handoff bundle.

Generates the handoff bundle, and with --execute also runs it over SSH: upload,
submit, poll, retrieve. Falls back to printing the manual bundle steps when SSH
or the scheduler is unavailable. The automatic path needs key/agent SSH
(BatchMode).

The scheduler comes from hpc_config.yaml. Slurm is the default and is the dialect
that has run on a real cluster. PBS Pro and Torque stay available by name. Every
rule about what a scheduler wants lives in schedulers.py, which also renders the
job script, so the bundle and this runner never disagree about the cluster.

What this wrapper claims
------------------------
`success` reports the analysis. It is True when this run's own record says every
expected sample has verified Cell Ranger output. A job the scheduler accepted has
produced nothing yet, so submission is reported on its own, in `submitted`.

The wrapper has two sources of evidence once a cluster job finishes. The run
record the pipeline writes to results/runs/<run_id>/outcome.json, retrieved with
the rest of the results. And the scheduler's own account of the job, which is the
orchestrator's verdict on the same run. Where both are readable the weaker of the
two decides, so a disagreement never produces the stronger claim.

Neither is guaranteed. Retrieval can fail, an old image may write no record, and
a queue forgets finished jobs. A run this wrapper cannot describe is reported as
`unknown`. That is not a success and it is not a failure, and the message says so.

What the scheduler measures
---------------------------
Sizing the real service needs the numbers a job leaves behind. Slurm records the
final state, the elapsed time and the peak resident memory in its accounting
database, and only after the job has left the queue, so `sacct` is where they
come from. PBS reports the same three under `resources_used`. Both are printed
and both are returned. A site that runs no accounting reports none of them, and
the wrapper says which ones it could not read.

The SSH handling has not been verified against a real cluster. The Slurm job
script, submission sequence and sacct handling reproduce a bundle that has run on
K-BDS.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import schedulers  # noqa: E402
from prepare_hpc_handoff import (  # noqa: E402
    load_config, generate_bundle, bundle_run_id, new_run_id,
    run_scoped_config,
)

# Where the pipeline records what a run achieved, under the results volume.
RUN_RECORD_DIRNAME = "runs"
RUN_OUTCOME_NAME = "outcome.json"

# How much each verdict claims, strongest first. Two sources of evidence are
# graded on this scale and the weaker one wins.
CLAIM_RANK = {
    "success": 5,
    "partial": 4,
    "nothing_processed": 3,
    "config_error": 2,
    "failed": 1,
    "unknown": 0,
    "not_attempted": 0,
}

# The orchestrator's exit code, as run_docker_pipeline.sh defines it. Exit 0 is
# absent on purpose: a steps 1-7 image also exits 0 after analysing nothing, so
# a zero exit on its own is not evidence that the analysis happened.
EXIT_STATUS = {2: "config_error", 3: "nothing_processed", 4: "partial"}

# What this wrapper exits with under --execute, on the pipeline's own contract:
# 0 did the work, 1 failed, 2 misconfigured, 3 valid and nothing to do, 4 partial.
# `unknown` exits 1. A run the wrapper cannot describe must not read as done.
STATUS_EXIT = {
    "success": 0,
    "partial": 4,
    "nothing_processed": 3,
    "config_error": 2,
    "failed": 1,
    "unknown": 1,
    "not_attempted": 1,
}


def _ssh_opts(cfg: dict) -> list:
    opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    if cfg.get("ssh_key"):
        opts += ["-i", str(cfg["ssh_key"])]
    return opts


def _host(cfg: dict) -> str:
    return f"{cfg['user']}@{cfg['host']}"


def _port(cfg: dict) -> str:
    return str(cfg.get("port", 22))


def _ssh(cfg: dict) -> list:
    return ["ssh", "-p", _port(cfg), *_ssh_opts(cfg)]


def _scp(cfg: dict) -> list:
    return ["scp", "-P", _port(cfg), *_ssh_opts(cfg)]


def _rsh(cfg: dict) -> str:
    # -e argument for rsync (remote shell with port + options)
    return "ssh -p " + _port(cfg) + " " + " ".join(_ssh_opts(cfg))


def _run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True)


def _remote(cfg: dict, command: str) -> subprocess.CompletedProcess:
    return _run([*_ssh(cfg), _host(cfg), command])


def preflight(cfg: dict) -> tuple:
    r = _run([*_ssh(cfg), _host(cfg), "true"])
    return r.returncode == 0, (r.stderr or "").strip()


def upload(cfg: dict, bundle: Path, sra_dir: Path) -> None:
    host, base = _host(cfg), cfg["remote_base"]
    remote_results = schedulers.results_dir(cfg)
    _run([*_ssh(cfg), host,
          f"mkdir -p {base}/data/sra {base}/logs {remote_results}"]).check_returncode()
    if cfg["transfer_tool"] == "rsync":
        _run(["rsync", "-a", "-e", _rsh(cfg), f"{sra_dir}/", f"{host}:{base}/data/sra/"]).check_returncode()
    else:
        _run([*_scp(cfg), "-r", f"{sra_dir}/.", f"{host}:{base}/data/sra/"]).check_returncode()
    for name in ("config.yaml", schedulers.job_script_name(cfg), "run_singularity_pipeline.sh"):
        _run([*_scp(cfg), str(bundle / name), f"{host}:{base}/"]).check_returncode()


def test_submission(cfg: dict) -> None:
    """Have the scheduler judge the request before anything is queued.

    Slurm answers this from `sbatch --test-only`. A partition the account cannot
    use, a walltime over the limit or a memory request no node can satisfy is
    refused here, in a second, instead of after the upload and the wait. PBS has
    no equivalent to rely on, so this does nothing on the PBS path.
    """
    command = schedulers.test_only_command(cfg)
    if command is None:
        return
    r = _remote(cfg, command)
    if r.returncode != 0:
        detail = ((r.stderr or "") + (r.stdout or "")).strip() or "no reason given"
        raise RuntimeError(
            "sbatch --test-only rejected the request before queueing it: "
            f"{detail}. The partition, walltime, memory, account or QOS is not "
            "permitted here")
    print("[hpc] the scheduler accepted the request (sbatch --test-only)")


def submit(cfg: dict) -> str:
    r = _remote(cfg, schedulers.submit_command(cfg))
    r.check_returncode()
    out = (r.stdout or "").strip()
    if not out:
        raise RuntimeError(f"{schedulers.submit_tool(cfg)} produced no job id")
    jobid = schedulers.parse_job_id(cfg, out)
    if not jobid:
        raise RuntimeError(
            f"{schedulers.submit_tool(cfg)} reported success and printed no job "
            f"id, so nothing was queued: {out}")
    return jobid


def job_status(cfg: dict, jobid: str) -> tuple:
    """Return (done, state) from the queue."""
    r = _remote(cfg, schedulers.poll_command(cfg, jobid))
    return schedulers.parse_poll(cfg, r.returncode, r.stdout or "", jobid)


def job_report(cfg: dict, jobid: str) -> dict:
    """What the scheduler says about the finished job.

    Slurm answers from the accounting database, which is the only place the peak
    memory of a finished job is written. PBS answers from `qstat -x -f`. Fields
    the site does not record come back as None.
    """
    r = _remote(cfg, schedulers.report_command(cfg, jobid))
    return schedulers.parse_report(cfg, r.returncode, r.stdout or "", jobid)


def poll(cfg: dict, jobid: str, interval: int, max_hours: int) -> bool:
    deadline = time.monotonic() + max_hours * 3600
    while True:
        done, state = job_status(cfg, jobid)
        print(f"[hpc] job {jobid}: state={state}")
        if done:
            return True
        if time.monotonic() > deadline:
            print(f"[hpc] timed out after {max_hours}h waiting for job {jobid}")
            return False
        time.sleep(interval)


def retrieve(cfg: dict, results_dir: str) -> None:
    host, base = _host(cfg), cfg["remote_base"]
    remote_results = schedulers.results_dir(cfg)
    logs_dir = str(Path(results_dir) / "logs")
    Path(results_dir).mkdir(parents=True, exist_ok=True)
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    if cfg["transfer_tool"] == "rsync":
        _run(["rsync", "-a", "-e", _rsh(cfg),
              f"{host}:{remote_results}/", f"{results_dir}/"]).check_returncode()
        # logs are under /work/logs, not results, so pull them too for diagnosis
        _run(["rsync", "-a", "-e", _rsh(cfg), f"{host}:{base}/logs/", f"{logs_dir}/"]).check_returncode()
    else:
        _run([*_scp(cfg), "-r", f"{host}:{remote_results}/.",
              f"{results_dir}/"]).check_returncode()
        _run([*_scp(cfg), "-r", f"{host}:{base}/logs/.", f"{logs_dir}/"]).check_returncode()


def remote_run_ids(cfg: dict) -> set:
    """Run ids already under the remote results volume.

    That volume is persistent, so it holds records from earlier runs. Listing it
    before submitting names the records this run did not write.
    """
    remote_results = schedulers.results_dir(cfg)
    r = _remote(
        cfg, f"ls -1 {remote_results}/{RUN_RECORD_DIRNAME} 2>/dev/null")
    if r.returncode != 0:
        return set()
    return {line.strip() for line in (r.stdout or "").splitlines() if line.strip()}


def local_run_ids(results_dir) -> set:
    """Run ids under a local results directory."""
    runs = Path(results_dir) / RUN_RECORD_DIRNAME
    if not runs.is_dir():
        return set()
    return {p.name for p in runs.iterdir() if p.is_dir()}


def read_run_record(results_dir, known_before, run_id=None) -> tuple:
    """Read this run's outcome record. Returns (record, run_id, problem).

    The bundle names the run, and the job script hands that name to the
    pipeline, so the record is read by name. A wrapper that does not know the
    name falls back to elimination: the run directory that was not there before
    the job ran. None means the record did not come back. Several means the
    wrapper cannot tell which one is this run, and it says that instead of
    picking one.
    """
    runs = Path(results_dir) / RUN_RECORD_DIRNAME
    if run_id:
        return _load_record(runs / run_id / RUN_OUTCOME_NAME, run_id)
    new = sorted(local_run_ids(results_dir) - set(known_before))
    if not new:
        return None, None, f"no run record for this run was retrieved to {runs}"
    if len(new) > 1:
        return None, None, (
            f"{len(new)} new run records were retrieved to {runs} "
            f"({', '.join(new)}); this wrapper cannot tell which one is this run"
        )
    return _load_record(runs / new[0] / RUN_OUTCOME_NAME, new[0])


def _load_record(path: Path, run_id: str) -> tuple:
    try:
        with open(path) as fh:
            record = json.load(fh)
    except FileNotFoundError:
        return None, run_id, (
            f"no run record for this run ({run_id}) was retrieved to "
            f"{path.parent.parent}. The job wrote none, or the retrieval did "
            "not bring it back")
    except (OSError, ValueError) as e:
        return None, run_id, f"could not read {path}: {e}"
    if not isinstance(record, dict):
        return None, run_id, f"{path} does not hold a run record"
    return record, run_id, None


def grade_record(record: dict) -> tuple:
    """(status, message) from the counts the pipeline wrote about its own run."""
    counts = record.get("counts") or {}
    expected = int(counts.get("expected", len(record.get("expected_samples") or [])))
    completed = int(counts.get("completed", 0))
    adopted = int(counts.get("adopted", 0))
    if completed > expected:
        return "failed", (
            f"the run record counts {completed} completed sample(s) against "
            f"{expected} expected; the two disagree, so neither is trusted"
        )
    if expected == 0:
        return "nothing_processed", "the run was given no sample to process"
    if completed == expected:
        return "success", (
            f"all {expected} expected sample(s) have verified Cell Ranger output"
        )
    if completed > 0:
        return "partial", (
            f"{completed} of {expected} expected sample(s) have verified "
            "Cell Ranger output"
        )
    if adopted > 0:
        return "partial", (
            f"0 of {expected} expected sample(s) have verified Cell Ranger output; "
            f"{adopted} hold pre-existing output adopted on request, which is "
            "unverified and counts as no analysis"
        )
    return "nothing_processed", (
        f"0 of {expected} expected sample(s) was analysed"
    )


def grade_job_report(cfg: dict, report) -> tuple:
    """(status, message) from what the scheduler recorded about the job.

    None when the scheduler says nothing this wrapper can use. A zero exit is one
    such case: the orchestrator also exits 0 from a steps 1-7 image that analysed
    nothing, so zero alone does not evidence an analysis.

    A job state can override the exit code. Slurm records OUT_OF_MEMORY with an
    ExitCode of 0:125, so a reader who trusts the code alone calls a killed job
    clean. The same holds for a job the wall clock ends and one a node failure
    ends.
    """
    if not report:
        return None, None
    state = report.get("state")
    if schedulers.state_is_failure(cfg, state):
        detail = {
            "OUT_OF_MEMORY": "the out-of-memory killer ended it; request more memory",
            "TIMEOUT": "it reached the wall-clock limit; request more time",
            "CANCELLED": "it was cancelled",
            "NODE_FAIL": "the node failed under it",
            "PREEMPTED": "it was preempted",
        }.get(state, "it did not complete")
        return "failed", f"the scheduler recorded the job as {state}, so {detail}"
    signal = report.get("signal")
    if signal:
        return "failed", f"the job was killed by signal {signal}"
    exit_status = report.get("exit_status")
    if exit_status is None or exit_status == 0:
        return None, None
    status = EXIT_STATUS.get(exit_status, "failed")
    return status, f"the job exited {exit_status}, which the pipeline reports as {status}"


def analysis_verdict(cfg: dict, record, report) -> tuple:
    """(status, message) for one finished cluster job, from what can be seen.

    The record is the fuller account and decides. A worse verdict from the
    scheduler overrides it, because the two describe one run and the weaker claim
    is the safe one. The scheduler alone carries a failure verdict and no success
    verdict.
    """
    from_record = grade_record(record) if record is not None else (None, None)
    from_job = grade_job_report(cfg, report)

    if from_record[0] and from_job[0]:
        if CLAIM_RANK.get(from_job[0], 0) < CLAIM_RANK.get(from_record[0], 0):
            return from_job[0], (
                f"{from_job[1]}. The run record says {from_record[1]}. "
                "The weaker of the two is reported."
            )
        return from_record[0], f"the run record says {from_record[1]}"
    if from_record[0]:
        return from_record[0], f"the run record says {from_record[1]}"
    if from_job[0]:
        return from_job[0], f"no run record was read for this run, and {from_job[1]}"
    return "unknown", (
        "no run record was read for this run, and the scheduler's account of the "
        "job adds nothing. This wrapper cannot say what the analysis achieved. "
        "That is not a success. The analysis may still have completed"
    )


def _print_manual(cfg: dict, bundle: Path) -> None:
    submit_tool = schedulers.submit_tool(cfg)
    watch = schedulers.watch_command(cfg, cfg["user"])
    print("[hpc] --- manual (semi-auto) fallback ---")
    print(f"[hpc] bundle ready at: {bundle}/")
    print(f"[hpc]   1) bash {bundle}/transfer_and_submit.sh   # upload + {submit_tool}")
    print(f"[hpc]   2) wait for the job ({watch}), or use email notification")
    print(f"[hpc]   3) bash {bundle}/retrieve_results.sh      # pull results back")


def run_hpc_stage3(cfg: dict, sra_dir, out_dir, results_dir: str = "stage3_results",
                   local_sif: str = "genoar-srr_step9.sif", execute: bool = False,
                   poll_interval: int = 60, max_hours: int = 24) -> dict:
    """Generate the bundle and (optionally) run it end-to-end with fallback.

    `success` is True only when the analysis is evidenced as complete. Every
    other field reports one observable fact on its own: `submitted` for the job
    the scheduler accepted, `job_exit` and `job_state` for how it finished,
    `elapsed` and `max_rss` for what it used, `analysis_status` and
    `analysis_message` for what the run achieved.
    """
    result = {
        # The analysis succeeded. Nothing else sets this.
        "success": False,
        "mode": "manual",
        "bundle_dir": None,
        "job_id": None,
        "error": None,
        # The scheduler accepted the job. A submitted job has produced nothing yet.
        "submitted": False,
        # success, partial, nothing_processed, config_error, failed, unknown, or
        # not_attempted when no job was run at all.
        "analysis_status": "not_attempted",
        "analysis_message": "no analysis was attempted",
        "scheduler": schedulers.scheduler_of(cfg),
        "job_exit": None,
        "job_state": None,
        "elapsed": None,
        "max_rss": None,
        "max_rss_kib": None,
        "req_mem": None,
        "run_id": None,
        "run_record": None,
        "run_record_problem": None,
        "counts": None,
    }
    # The automatic path must use the same run-scoped remote root written into
    # the generated scripts. Otherwise it uploads/submits/retrieves through the
    # reusable parent from hpc_config while the manual bundle names
    # <parent>/runs/<run-id>, recreating the stale-input merge this isolation
    # exists to prevent.
    run_id = new_run_id()
    cfg = run_scoped_config(cfg, run_id)
    bundle = generate_bundle(
        cfg, sra_dir, out_dir, results_dir, local_sif, run_id=run_id)
    result["bundle_dir"] = str(bundle)
    run_id = bundle_run_id(bundle)
    print(f"[hpc] handoff bundle: {bundle}/")
    print(f"[hpc] scheduler: {result['scheduler']}; "
          f"job script: {schedulers.job_script_name(cfg)}; run id: {run_id}")

    if not execute:
        _print_manual(cfg, bundle)
        result["analysis_message"] = (
            "the handoff bundle was written and no job was run; "
            "submit it to the cluster to analyse anything"
        )
        _report(cfg, result, results_dir)
        return result

    ok, err = preflight(cfg)
    if not ok:
        print(f"[hpc] SSH preflight failed ({err or 'unreachable'}) -> falling back to manual bundle.")
        _print_manual(cfg, bundle)
        result["error"] = f"ssh preflight failed: {err}"
        result["analysis_message"] = (
            "SSH to the cluster failed, so no job was submitted and nothing was "
            "analysed; the bundle is ready for a manual run"
        )
        _report(cfg, result, results_dir)
        return result

    try:
        # Taken before the job runs, so the record it writes can be told apart
        # from the records already on the persistent results volume.
        known_runs = remote_run_ids(cfg) | local_run_ids(results_dir)
        print("[hpc] uploading inputs...")
        upload(cfg, bundle, Path(sra_dir).resolve())
        test_submission(cfg)
        jobid = submit(cfg)
        result["job_id"] = jobid
        result["submitted"] = True
        print(f"[hpc] submitted job {jobid}; polling every {poll_interval}s (max {max_hours}h)...")
        finished = poll(cfg, jobid, poll_interval, max_hours)
        if not finished:
            result["error"] = "poll timeout"
            result["analysis_status"] = "unknown"
            result["analysis_message"] = (
                f"job {jobid} had not finished after {max_hours}h, so this wrapper "
                "stopped waiting. The job may still be running on the cluster"
            )
            _print_manual(cfg, bundle)
            _report(cfg, result, results_dir)
            return result
        report = job_report(cfg, jobid)  # best-effort; fields are None when unread
        result["job_exit"] = report.get("exit_status")
        result["job_state"] = report.get("state")
        result["elapsed"] = report.get("elapsed")
        result["max_rss"] = report.get("max_rss")
        result["max_rss_kib"] = report.get("max_rss_kib")
        result["req_mem"] = report.get("req_mem")
        print("[hpc] job finished; retrieving results (partial results are kept on failure)...")
        retrieve(cfg, results_dir)
        result["mode"] = "auto"

        record, found_id, problem = read_run_record(results_dir, known_runs, run_id)
        result["run_id"] = found_id
        result["run_record_problem"] = problem
        if record is not None:
            result["run_record"] = str(
                Path(results_dir) / RUN_RECORD_DIRNAME / found_id / RUN_OUTCOME_NAME)
            result["counts"] = record.get("counts")

        status, message = analysis_verdict(cfg, record, report)
        result["analysis_status"] = status
        result["analysis_message"] = message
        result["success"] = status == "success"
        _report(cfg, result, results_dir)
        return result
    except Exception as e:
        print(f"[hpc] auto-execution failed ({e}) -> falling back to manual bundle.")
        _print_manual(cfg, bundle)
        result["error"] = str(e)
        if result["submitted"]:
            result["analysis_status"] = "unknown"
            result["analysis_message"] = (
                f"the job was submitted and this wrapper then failed with: {e}. "
                "What the job achieved was not observed"
            )
        else:
            result["analysis_message"] = (
                f"no job was submitted, because this wrapper failed with: {e}"
            )
        _report(cfg, result, results_dir)
        return result


def _print_resources(cfg: dict, result: dict) -> None:
    """Print what the job used, and name what the scheduler did not record.

    These are the numbers that size the next request. Without them somebody has
    to be asked to run sacct by hand days later, when the accounting row may
    already be gone.
    """
    if not result["submitted"]:
        return
    elapsed = result.get("elapsed")
    max_rss = result.get("max_rss")
    req_mem = result.get("req_mem")
    known = []
    if elapsed:
        known.append(f"elapsed={elapsed}")
    if max_rss:
        known.append(f"peak memory={max_rss}")
    if req_mem:
        known.append(f"requested={req_mem}")
    if result.get("job_state"):
        known.append(f"state={result['job_state']}")
    if known:
        print(f"[hpc] resources: {', '.join(known)}")
    if not elapsed or not max_rss:
        missing = " and ".join(
            n for n, v in (("elapsed time", elapsed), ("peak memory", max_rss)) if not v)
        command = schedulers.human_report_command(cfg, result.get("job_id") or "<job id>")
        print(f"[hpc] the scheduler did not report {missing} for this job. "
              f"Ask it again with: {command}")
        if schedulers.is_slurm(cfg):
            print("[hpc] sacct answers only where the site runs Slurm accounting, "
                  "and it forgets a job once the row is purged.")


def _report(cfg: dict, result: dict, results_dir: str) -> None:
    """Print what is known about the analysis, in the terms the caller reads."""
    status = result["analysis_status"]
    print(f"[hpc] submitted: {'yes' if result['submitted'] else 'no'}"
          + (f" (job {result['job_id']})" if result["job_id"] else ""))
    _print_resources(cfg, result)
    if result.get("run_record"):
        print(f"[hpc] run record: {result['run_record']}")
    elif result.get("run_record_problem"):
        print(f"[hpc] run record: {result['run_record_problem']}")
    print(f"[hpc] analysis: {status}. {result['analysis_message']}.")

    if status == "success":
        print(f"[hpc] done. results -> {results_dir}")
        return
    if status == "unknown":
        print("[hpc] This wrapper is not reporting a success and is not reporting "
              "a failure. Read the run record on the cluster to find out which it was: "
              f"results/{RUN_RECORD_DIRNAME}/<run_id>/{RUN_OUTCOME_NAME}.")
        return
    if status in ("partial", "failed"):
        print(f"[hpc] Completed samples are retrieved. Check "
              f"{results_dir}/reports/step8_failed.txt and {results_dir}/logs/ "
              "(failed samples are prepared for retry under results/retry_failed/).")


def exit_code_for(result: dict, execute: bool) -> int:
    """Process exit code for one wrapper run.

    Without --execute the wrapper was asked for a bundle, so writing the bundle
    is the whole job. With --execute it was asked for an analysis, and it exits
    on the pipeline's contract: 0 did the work, 1 failed, 2 misconfigured, 3
    valid and nothing to do, 4 partial.
    """
    if not execute:
        return 0 if result.get("bundle_dir") else 1
    return STATUS_EXIT.get(result.get("analysis_status"), 1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run Stage 3 on HPC (auto with fallback)")
    ap.add_argument("--sra-dir", type=Path, required=True)
    ap.add_argument("--hpc-config", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("hpc_handoff"))
    ap.add_argument("--results-dir", type=str, default="stage3_results")
    ap.add_argument("--local-sif", type=str, default="genoar-srr_step9.sif")
    ap.add_argument("--execute", action="store_true",
                    help="Attempt full automation over SSH (else just generate the bundle)")
    ap.add_argument("--poll-interval", type=int, default=60, help="queue poll interval in seconds")
    ap.add_argument("--max-hours", type=int, default=24, help="max hours to wait for the job")
    ap.add_argument("--json", type=Path, default=None,
                    help="Write the result dict to this path, for a calling program")
    args = ap.parse_args()

    cfg = load_config(args.hpc_config)
    res = run_hpc_stage3(cfg, args.sra_dir, args.out, args.results_dir, args.local_sif,
                         execute=args.execute, poll_interval=args.poll_interval, max_hours=args.max_hours)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    sys.exit(exit_code_for(res, args.execute))


if __name__ == "__main__":
    main()
