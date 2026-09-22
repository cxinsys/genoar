"""Reserving a run id is one atomic step, not a check followed by a create.

The defect this pins, and how it was found. The previous round added a run
record guard to all three launchers:

    if [ -e "$RUN_PLAN" ]; then ... refuse ... fi
       ...later...
    mkdir -p "$RUN_DIR/workers"

Check, then create. That is only a guard against a run that *finished* earlier
- the case that was never dangerous, because a finished run's evidence is
already on disk and nothing was going to touch it. Two runs of one id started at
the same instant both see no record, both pass, and both go on to share one
`run.json`, one set of worker directories, one checkpoint per worker and one
merged manifest. Reproduced with the harness below: 30 pairs started against a
barrier, 30 pairs where *both* launchers ran their workers.

The fix is the classic primitive: `mkdir` without `-p` creates the directory or
fails, in one step the kernel does not interleave, so of two racers exactly one
gets the directory. The run directory itself is the reservation - it is the
object that must be new - and it stays, so the claim outlives the process that
made it.

`mkdir` failing is not on its own a collision, though: a read-only output tree,
a full disk and a missing parent all fail the same call. So the failure path
asks a second question - is the directory there now? - and answers "somebody
else has this id" (exit 2) or "this id could not be created here" (exit 1)
accordingly. A user who cannot write to the output directory must not be told
their run id is taken.

What is asserted here:
  * of two same-id launchers started at one instant, exactly one proceeds and
    the other exits 2, every trial;
  * the loser starts no worker and writes nothing into the run;
  * a run directory with no plan in it - a run that died before it promised
    anything, or a racer's claim - is refused rather than reused;
  * a filesystem that cannot hold the run directory is reported as that, not as
    a taken id;
  * the generated-id path, which mints a fresh id per run, still never refuses
    itself, even when two runs start together;
  * `GENOAR_RESUME=1` still enters an existing run, and two resumes of one run
    started together are held apart the same way.
"""

import itertools
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

CRAWLER_DIR = Path(__file__).resolve().parent.parent

ALL_LAUNCHERS = ["run_crawl.sh", "run_parallel_crawl.sh", "run_docker_parallel.sh"]

# How many pairs each stress test starts. The barrier below makes one pair
# enough to reproduce - both racers are already inside bash and past every fork
# when the starter file appears - but a race test that passes by luck is worse
# than none, so several run by default and the count is raisable for a longer
# soak: GENOAR_RACE_TRIALS=30 python3 -m pytest ...
TRIALS = int(os.environ.get("GENOAR_RACE_TRIALS", "6"))

# Both racers spin here rather than sleeping, so the gap between them entering
# the launcher is scheduler noise rather than timer granularity. `exec` keeps
# stdin, which run_crawl.sh reads its answers from.
RACER = r"""#!/bin/bash
while [ ! -e "$RACE_BARRIER" ]; do :; done
exec bash "$RACE_SCRIPT" "$@"
"""

# A `docker` and a `python` that answer well enough for a launcher to reach its
# worker launch. Nothing here needs a daemon, a browser or GEO.
STUB = r"""#!/bin/sh
case "$1" in
    images) echo "sha256:stub" ;;
    info)   exit 0 ;;
    inspect) echo "false 0" ;;
    ps)     exit 0 ;;
    run)
        case "$*" in
            *--report-pages*) echo 'GENOAR_PAGE_REPORT {"total_results": 1, "items_per_page": 500, "total_pages": 4}' ;;
            *) echo "stub-container-id" ;;
        esac
        ;;
    *) : ;;
esac
exit 0
"""

RUN_ID = "contended"
WORKERS = 2
PAGES = 4


@pytest.fixture
def arena(tmp_path):
    """A fresh, numbered workspace per trial - launchers, stubs and a racer."""
    counter = itertools.count()

    def build():
        root = tmp_path / f"trial-{next(counter)}"
        work = root / "work"
        work.mkdir(parents=True)
        for name in (*ALL_LAUNCHERS, "Dockerfile.amd64", "Dockerfile.arm64"):
            shutil.copy(CRAWLER_DIR / name, work / name)

        binaries = root / "bin"
        binaries.mkdir()
        for name in ("docker", "python"):
            stub = binaries / name
            stub.write_text(STUB)
            stub.chmod(0o755)

        racer = root / "racer.sh"
        racer.write_text(RACER)
        racer.chmod(0o755)

        answers = root / "answers"
        answers.write_text(f"{WORKERS}\n{PAGES}\ny\n")

        return {
            "root": root,
            "dir": work,
            "bin": binaries,
            "racer": racer,
            "answers": answers,
            "barrier": root / "GO",
        }

    return build


def _environment(arena, run_id, extra):
    environment = dict(os.environ)
    environment.update({
        "PATH": f"{arena['bin']}:{environment['PATH']}",
        "RACE_BARRIER": str(arena["barrier"]),
        "GENOAR_SKIP_MONITOR": "1",
        "GENOAR_WORKER_START_DELAY": "0",
        "GENOAR_TOTAL_PAGES": str(PAGES),
    })
    if run_id is None:
        environment.pop("GENOAR_RUN_ID", None)
    else:
        environment["GENOAR_RUN_ID"] = run_id
    environment.update({k: str(v) for k, v in (extra or {}).items()})
    return environment


def race(arena, script, run_id=RUN_ID, racers=2, **extra):
    """Start `racers` launchers that all enter the script at the same instant.

    Returns one (exit code, output) pair per racer.
    """
    environment = _environment(arena, run_id, extra)
    environment["RACE_SCRIPT"] = str(arena["dir"] / script)
    args = [] if script == "run_crawl.sh" else [str(WORKERS), str(PAGES)]

    processes = []
    handles = []
    for _ in range(racers):
        if script == "run_crawl.sh":
            stdin = open(arena["answers"])
            handles.append(stdin)
        else:
            stdin = subprocess.DEVNULL
        processes.append(subprocess.Popen(
            ["bash", str(arena["racer"]), *args],
            cwd=str(arena["dir"]), stdin=stdin,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=environment,
        ))

    # Both are spinning on the barrier by now; releasing it starts them within
    # microseconds of each other.
    time.sleep(0.4)
    arena["barrier"].write_text("go\n")

    outcomes = []
    for process in processes:
        output, _ = process.communicate(timeout=180)
        outcomes.append((process.returncode, output))
    for handle in handles:
        handle.close()
    return outcomes


def launch(arena, script, run_id=RUN_ID, **extra):
    """One launcher, on its own, with no barrier in the way."""
    environment = _environment(arena, run_id, extra)
    args = [] if script == "run_crawl.sh" else [str(WORKERS), str(PAGES)]
    stdin = arena["answers"].read_text() if script == "run_crawl.sh" else ""
    completed = subprocess.run(
        ["bash", script, *args], cwd=str(arena["dir"]), input=stdin,
        text=True, capture_output=True, env=environment, timeout=180,
    )
    return completed.returncode, completed.stdout + completed.stderr


STARTED_WORKER = re.compile(r"Starting [Ww]orker (\d+): pages (\S+)")


def workers_started(outcomes):
    """Every worker every racer said it was starting.

    Read from each launcher's own output rather than from the filesystem, for
    the reason the defect exists at all: two racers write their worker
    directories to the same paths, so what is on disk afterwards cannot tell
    one launcher from two. Each racer's stdout can, and it is captured whole
    before this is called, so there is no timing in it.
    """
    if not isinstance(outcomes, list):
        outcomes = [outcomes]
    return [match.groups()
            for _, output in outcomes
            for match in STARTED_WORKER.finditer(output)]


def run_dir(arena, run_id=RUN_ID):
    return arena["dir"] / "crawl_output" / "runs" / run_id


def run_ids(arena):
    runs = arena["dir"] / "crawl_output" / "runs"
    return sorted(p.name for p in runs.iterdir()) if runs.is_dir() else []


@pytest.mark.parametrize("script", ALL_LAUNCHERS)
class TestOnlyOneRunGetsAnId:
    """The reproduction, run the way the reviewer ran it."""

    def test_two_launchers_started_together_leave_exactly_one_winner(
        self, arena, script
    ):
        both_won = 0
        verdicts = []
        for _ in range(TRIALS):
            trial = arena()
            outcomes = race(trial, script)
            codes = sorted(code for code, _ in outcomes)
            verdicts.append(codes)
            if codes == [0, 0]:
                both_won += 1

        assert both_won == 0, (
            f"{both_won}/{TRIALS} pairs of '{script}' both claimed run "
            f"'{RUN_ID}': {verdicts}"
        )
        assert all(codes == [0, 2] for codes in verdicts), verdicts

    def test_the_loser_starts_no_worker(self, arena, script):
        """Two winners means twice the workers on one run's directories - the
        same checkpoint, the same downloads, the same worker manifest."""
        for _ in range(TRIALS):
            trial = arena()
            outcomes = race(trial, script)

            started = workers_started(outcomes)
            assert len(started) == WORKERS, (
                f"{len(started)} workers started for a {WORKERS}-worker run: "
                f"{started}"
            )

    def test_the_loser_says_the_id_is_taken_and_offers_a_way_on(
        self, arena, script
    ):
        trial = arena()
        outcomes = race(trial, script)
        losers = [output for code, output in outcomes if code == 2]

        assert len(losers) == 1, [code for code, _ in outcomes]
        assert RUN_ID in losers[0]
        assert "unset GENOAR_RUN_ID" in losers[0]

    def test_the_run_keeps_one_plan_and_one_set_of_workers(self, arena, script):
        trial = arena()
        race(trial, script)

        assert (run_dir(trial) / "run.json").exists()
        assert not (run_dir(trial) / "resumes.jsonl").exists()
        workers = sorted(p.name for p in (run_dir(trial) / "workers").iterdir())
        assert workers == [f"worker-{n}" for n in range(1, WORKERS + 1)]

    def test_four_launchers_started_together_leave_exactly_one_winner(
        self, arena, script
    ):
        """Two is the case that was reported; the primitive has to hold for
        any number, and four makes an accidental pass much less likely."""
        trial = arena()
        outcomes = race(trial, script, racers=4)

        codes = sorted(code for code, _ in outcomes)
        assert codes == [0, 2, 2, 2], codes
        assert len(workers_started(outcomes)) == WORKERS


@pytest.mark.parametrize("script", ALL_LAUNCHERS)
class TestAClaimedIdIsNotReusable:
    def test_a_run_directory_without_a_plan_is_still_a_claim(self, arena, script):
        """What a racer that lost, or a run that died before it wrote its plan,
        leaves behind. It is not evidence of anything, but it is somebody's
        claim on the id, and starting on top of it is how two runs come to
        share one directory."""
        trial = arena()
        claimed = run_dir(trial)
        claimed.mkdir(parents=True)
        # The pointer at the newest completed run. Every launcher deletes it as
        # it starts, so a refusal that happens even one line late destroys the
        # last completed run's top-level evidence.
        pointer = trial["dir"] / "crawl_output" / "crawl_manifest.json"
        pointer.write_text('{"run_id": "an-earlier-run"}')

        outcome = launch(trial, script)

        assert outcome[0] == 2, outcome[1]
        assert "already" in outcome[1]
        assert workers_started(outcome) == []
        assert sorted(p.name for p in claimed.iterdir()) == []
        assert json.loads(pointer.read_text())["run_id"] == "an-earlier-run"

    @pytest.mark.skipif(os.geteuid() == 0,
                        reason="root writes through a read-only directory")
    def test_a_directory_that_cannot_be_created_is_not_a_taken_id(
        self, arena, script
    ):
        """`mkdir` fails for a full disk and a read-only tree as readily as for
        a name already taken. Telling a user their run id is in use when their
        output directory is read-only sends them to change the one thing that
        is not the problem."""
        trial = arena()
        runs = trial["dir"] / "crawl_output" / "runs"
        runs.mkdir(parents=True)
        runs.chmod(0o555)
        try:
            outcome = launch(trial, script)
        finally:
            runs.chmod(0o755)

        code, output = outcome
        assert code == 1, f"exit {code}, expected 1\n{output}"
        assert "already has a record" not in output
        assert "could not be created" in output
        assert workers_started(outcome) == []


@pytest.mark.parametrize("script", ALL_LAUNCHERS)
class TestTheGeneratedIdPathNeverRefusesItself:
    """A launcher that mints its own id must not be slowed by a rule aimed at
    reuse - and two started at one instant mint different ids, because the id
    carries the pid."""

    def test_two_launchers_without_an_id_started_together_both_run(
        self, arena, script
    ):
        trial = arena()
        outcomes = race(trial, script, run_id=None)

        assert [code for code, _ in outcomes] == [0, 0], [
            output for _, output in outcomes
        ]
        assert len(run_ids(trial)) == 2, run_ids(trial)
        assert len(workers_started(outcomes)) == 2 * WORKERS


class TestResumeStillEntersAnExistingRun:
    """The reservation may not close the one door that is supposed to be open.
    Only run_parallel_crawl.sh can resume; the container launchers cannot, and
    are covered by test_run_record_protection.py."""

    def test_a_resume_after_the_run_has_finished_is_allowed(self, arena):
        trial = arena()
        assert launch(trial, "run_parallel_crawl.sh")[0] == 0
        plan = (run_dir(trial) / "run.json").read_text()

        code, output = launch(trial, "run_parallel_crawl.sh", GENOAR_RESUME="1")

        assert code == 0, output
        assert (run_dir(trial) / "run.json").read_text() == plan
        assert (run_dir(trial) / "resumes.jsonl").exists()

    def test_two_resumes_started_together_leave_exactly_one_winner(self, arena):
        """A resume enters a directory that already exists, so the directory
        cannot be its reservation. Two resumes racing would land two sets of
        workers on one set of checkpoints - the same defect, one door along."""
        trial = arena()
        assert launch(trial, "run_parallel_crawl.sh")[0] == 0

        outcomes = race(trial, "run_parallel_crawl.sh", GENOAR_RESUME="1")

        codes = sorted(code for code, _ in outcomes)
        assert codes == [0, 2], [output for _, output in outcomes]
        resumes = (run_dir(trial) / "resumes.jsonl").read_text().splitlines()
        assert len([line for line in resumes if line.strip()]) == 1

    def test_a_resume_cannot_enter_a_run_that_is_still_starting(self, arena):
        """The window the reservation opened: the winner has the directory but
        has not written its plan yet."""
        trial = arena()
        run_dir(trial).mkdir(parents=True)

        outcome = launch(trial, "run_parallel_crawl.sh", GENOAR_RESUME="1")

        assert outcome[0] == 2, outcome[1]
        assert workers_started(outcome) == []
