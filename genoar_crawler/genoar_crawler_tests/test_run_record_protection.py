"""A run record is never overwritten, and never deleted by an opt-out.

`runs/<id>/run.json` is written before any worker starts and is what the
aggregator holds a run to. It is the durable evidence of what a run set out to
do - durable in a way the containers are not, because `docker system prune`
clears those and leaves this.

The defect this pins: reusing an id whose record existed silently rewrote it.
There were three paths and one of them was invisible from the terminal - a
fresh id, a deliberate `GENOAR_RESUME=1`, and "start again on top of an existing
run", which looked exactly like the first and destroyed the earlier run's
record. Reproduced against `run_parallel_crawl.sh`, which had no guard at all:
two runs of one id, and the second's `created_at` replaced the first's.

The two container launchers refused on live or leftover *containers* but not on
the record, so pruning the containers left the record unprotected.

The rules asserted here:
  * an id whose record exists is refused, exit 2, record untouched;
  * `GENOAR_RESUME=1` is the one way through, and only where resuming is a
    thing the script can do;
  * a resume leaves the original plan standing and records itself beside it, so
    a reader can tell the original run from what came after;
  * `GENOAR_RECLAIM_RUN=1` discards containers and never a record;
  * the checks are ordered so `GENOAR_RECLAIM_RUN` is only ever suggested where
    setting it is actually the answer.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

CRAWLER_DIR = Path(__file__).resolve().parent.parent

ALL_LAUNCHERS = ["run_crawl.sh", "run_parallel_crawl.sh", "run_docker_parallel.sh"]
CONTAINER_LAUNCHERS = ["run_crawl.sh", "run_docker_parallel.sh"]

# A daemon with nothing on it. This is also the pruned host: the containers of
# an earlier run are gone, and only its record remains.
STUB_EMPTY = r"""#!/bin/sh
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

# A daemon still holding the stopped containers of an earlier run of this id.
STUB_LEFTOVERS = r"""#!/bin/sh
case "$1" in
    images) echo "sha256:stub" ;;
    info)   exit 0 ;;
    inspect) echo "false 0" ;;
    ps)
        all=0
        for a in "$@"; do case "$a" in -a|-aq) all=1 ;; esac; done
        [ "$all" = 1 ] || exit 0
        case "$*" in
            *--format*) echo "genoar-worker-reuse-me-1  (Exited (137) 2 hours ago)" ;;
            *) echo "ctr-abc123" ;;
        esac
        ;;
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

RUN_ID = "reuse-me"


@pytest.fixture
def workspace(tmp_path):
    def build(stub=STUB_EMPTY):
        work = tmp_path / "work"
        if not work.exists():
            work.mkdir()
            for name in (*ALL_LAUNCHERS, "Dockerfile.amd64", "Dockerfile.arm64"):
                shutil.copy(CRAWLER_DIR / name, work / name)
        binaries = tmp_path / "bin"
        binaries.mkdir(exist_ok=True)
        for name in ("docker", "python"):
            target = binaries / name
            target.write_text(stub)
            target.chmod(0o755)
        return {"dir": work, "bin": binaries}
    return build


def launch(ws, script, run_id=RUN_ID, **env):
    environment = dict(os.environ)
    environment.update({
        "PATH": f"{ws['bin']}:{environment['PATH']}",
        "GENOAR_SKIP_MONITOR": "1",
        "GENOAR_WORKER_START_DELAY": "0",
        "GENOAR_TOTAL_PAGES": "4",
    })
    if run_id is None:
        environment.pop("GENOAR_RUN_ID", None)
    else:
        environment["GENOAR_RUN_ID"] = run_id
    environment.update({k: str(v) for k, v in env.items()})

    args = [] if script == "run_crawl.sh" else ["2", "4"]
    stdin = "2\n4\ny\n" if script == "run_crawl.sh" else ""
    return subprocess.run(
        ["bash", script, *args],
        cwd=str(ws["dir"]), input=stdin, text=True,
        capture_output=True, env=environment, timeout=120,
    )


def record_path(ws, run_id=RUN_ID):
    return ws["dir"] / "crawl_output" / "runs" / run_id / "run.json"


def run_ids(ws):
    runs = ws["dir"] / "crawl_output" / "runs"
    return sorted(p.name for p in runs.iterdir()) if runs.is_dir() else []


@pytest.mark.parametrize("script", ALL_LAUNCHERS)
class TestAnExistingRecordIsNotOverwritten:
    def test_a_second_run_of_the_same_id_is_refused(self, workspace, script):
        ws = workspace()
        assert launch(ws, script).returncode == 0
        before = record_path(ws).read_text()
        time.sleep(1.1)  # created_at has second resolution

        second = launch(ws, script)

        assert second.returncode == 2, second.stdout
        assert "already has a record" in second.stdout
        assert record_path(ws).read_text() == before, "the record was rewritten"

    def test_the_refusal_names_the_file_and_a_way_forward(self, workspace, script):
        ws = workspace()
        launch(ws, script)
        second = launch(ws, script)

        # run_parallel_crawl.sh takes its output directory as an argument and
        # prints it as given, so the path on screen may be relative. What has
        # to be there is which file the refusal is about.
        assert "runs/reuse-me/run.json" in second.stdout
        assert "unset GENOAR_RUN_ID" in second.stdout

    def test_no_worker_starts_and_nothing_else_is_written(self, workspace, script):
        """The refusal lands before the run directory is touched a second
        time, so a refused run cannot disturb the one it collided with."""
        ws = workspace()
        launch(ws, script)
        run_dir = record_path(ws).parent
        before = sorted(p.name for p in run_dir.iterdir())

        launch(ws, script)

        assert sorted(p.name for p in run_dir.iterdir()) == before


class TestResumeIsTheOneWayThrough:
    """`GENOAR_RESUME=1` already meant "point this at an existing run and
    continue it". Making it the only way to reuse an id does not redefine it;
    it removes the third, silent path that its documentation never described."""

    def test_a_resume_is_allowed_and_keeps_the_original_plan(self, workspace):
        ws = workspace()
        assert launch(ws, "run_parallel_crawl.sh").returncode == 0
        original = record_path(ws).read_text()
        time.sleep(1.1)

        resumed = launch(ws, "run_parallel_crawl.sh", GENOAR_RESUME="1")

        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        assert record_path(ws).read_text() == original, (
            "a resume must not rewrite the plan it is resuming"
        )

    def test_each_resume_is_recorded_beside_the_plan(self, workspace):
        """A reader has to be able to tell the original run from what came
        after it, so the continuations are appended, never merged in."""
        ws = workspace()
        launch(ws, "run_parallel_crawl.sh")
        launch(ws, "run_parallel_crawl.sh", GENOAR_RESUME="1")
        launch(ws, "run_parallel_crawl.sh", GENOAR_RESUME="1")

        log = record_path(ws).parent / "resumes.jsonl"
        lines = [json.loads(line) for line in log.read_text().splitlines() if line]
        assert len(lines) == 2
        for entry in lines:
            assert "resumed_at" in entry
            assert entry["requested_end"] == 4

    def test_the_plan_still_says_what_the_original_run_promised(self, workspace):
        """The aggregator measures coverage against the plan. If a resume could
        rewrite it, a run that fell short could be made to look complete by
        resuming it with a smaller request."""
        ws = workspace()
        launch(ws, "run_parallel_crawl.sh")
        plan = json.loads(record_path(ws).read_text())

        launch(ws, "run_parallel_crawl.sh", GENOAR_RESUME="1")

        assert json.loads(record_path(ws).read_text()) == plan
        assert plan["requested_pages"] == {"start": 1, "end": 4}

    @pytest.mark.parametrize("script", CONTAINER_LAUNCHERS)
    def test_resume_does_not_open_a_door_the_script_cannot_walk_through(
        self, workspace, script
    ):
        """Neither container launcher can resume a run. Honouring the flag
        there would mean starting fresh workers over an existing record - the
        very thing being prevented - under a name that promises otherwise."""
        ws = workspace()
        launch(ws, script)
        before = record_path(ws).read_text()

        second = launch(ws, script, GENOAR_RESUME="1")

        assert second.returncode == 2
        assert record_path(ws).read_text() == before


class TestAResumeIsAStatementAboutARunThatExists:
    """The defect this pins. `GENOAR_RESUME=1` says "continue *that* run", and
    an id with no run behind it was not an error: the launcher reserved the
    typed id, wrote a fresh plan into it and crawled from page 1. Someone
    recovering a long crawl after a crash, who transposes two digits, silently
    restarts it - and the run they meant sits untouched next to it, so nothing
    on screen or on disk says what happened.

    A run exists here once its run.json is written. That file is the plan the
    aggregator holds the run to and the record of the split every checkpoint
    under the run belongs to; the resume path reads its worker count, requested
    end and distributed end straight out of it. A run directory without one is
    a reservation, not a run, and cannot be continued either.
    """

    def test_a_resume_of_an_id_that_has_no_run_is_refused(self, workspace):
        ws = workspace()

        completed = launch(ws, "run_parallel_crawl.sh", run_id="brand-new",
                           GENOAR_RESUME="1")

        assert completed.returncode == 2, completed.stdout + completed.stderr
        assert "no such run is" in completed.stdout
        assert "Starting worker" not in completed.stdout

    def test_the_refused_resume_creates_nothing(self, workspace):
        """Not even the empty directory the reservation would have made: the
        next attempt with the id typed correctly must not meet a claim its own
        predecessor left behind."""
        ws = workspace()

        launch(ws, "run_parallel_crawl.sh", run_id="brand-new",
               GENOAR_RESUME="1")

        assert not record_path(ws, "brand-new").exists()
        assert run_ids(ws) == []

    def test_a_mistyped_id_does_not_disturb_the_run_it_meant(self, workspace):
        """The reported sequence, end to end."""
        ws = workspace()
        assert launch(ws, "run_parallel_crawl.sh",
                      run_id="pilot-2026-08").returncode == 0
        before = record_path(ws, "pilot-2026-08").read_text()

        typo = launch(ws, "run_parallel_crawl.sh", run_id="pilot-2026-80",
                      GENOAR_RESUME="1")

        assert typo.returncode == 2
        assert run_ids(ws) == ["pilot-2026-08"]
        assert record_path(ws, "pilot-2026-08").read_text() == before

    def test_the_refusal_says_which_runs_are_actually_here(self, workspace):
        """A transposed digit is the case this exists for, so the ids that do
        exist are what the user needs to see."""
        ws = workspace()
        launch(ws, "run_parallel_crawl.sh", run_id="pilot-2026-08")

        typo = launch(ws, "run_parallel_crawl.sh", run_id="pilot-2026-80",
                      GENOAR_RESUME="1")

        assert "- pilot-2026-08" in typo.stdout

    def test_it_says_so_when_no_run_is_recorded_at_all(self, workspace):
        ws = workspace()

        completed = launch(ws, "run_parallel_crawl.sh", run_id="brand-new",
                           GENOAR_RESUME="1")

        assert "No run is recorded" in completed.stdout

    def test_a_claim_with_no_plan_in_it_is_not_a_run_to_resume(self, workspace):
        """A run directory whose launcher died before writing its plan, or one
        that another launcher took a moment ago. Neither has a split to give
        the workers back."""
        ws = workspace()
        (ws["dir"] / "crawl_output" / "runs" / "half-claimed").mkdir(parents=True)

        completed = launch(ws, "run_parallel_crawl.sh", run_id="half-claimed",
                           GENOAR_RESUME="1")

        assert completed.returncode == 2
        assert "holds no plan" in completed.stdout

    def test_a_resume_that_names_no_run_is_refused(self, workspace):
        """Without GENOAR_RUN_ID the launcher mints an id. Resuming a freshly
        minted id is a contradiction, and the loudest form of this defect:
        a full crawl from page 1 under an id nobody typed."""
        ws = workspace()

        completed = launch(ws, "run_parallel_crawl.sh", run_id=None,
                           GENOAR_RESUME="1")

        assert completed.returncode == 2, completed.stdout + completed.stderr
        assert "GENOAR_RUN_ID is not" in completed.stdout
        assert run_ids(ws) == []

    def test_the_run_that_is_there_still_resumes(self, workspace):
        """The refusal is about absence, not about resuming."""
        ws = workspace()
        assert launch(ws, "run_parallel_crawl.sh").returncode == 0

        resumed = launch(ws, "run_parallel_crawl.sh", GENOAR_RESUME="1")

        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        assert "Resuming run" in resumed.stdout


@pytest.mark.parametrize("script", CONTAINER_LAUNCHERS)
class TestAFlagThatIsSilentlyIgnoredIsItsOwnProblem:
    """Neither container launcher can resume, and neither read the flag at all:
    setting it produced a brand new crawl from page 1 with nothing to say that
    the continuation asked for had not happened. Where an existing record made
    them refuse, they refused for a different reason and never mentioned it."""

    def test_the_flag_is_refused_rather_than_ignored(self, workspace, script):
        ws = workspace()

        completed = launch(ws, script, run_id="brand-new", GENOAR_RESUME="1")

        assert completed.returncode == 2, completed.stdout + completed.stderr
        assert "cannot resume a run" in completed.stdout
        assert not record_path(ws, "brand-new").exists()

    def test_it_points_at_the_launcher_that_can(self, workspace, script):
        ws = workspace()

        completed = launch(ws, script, run_id="brand-new", GENOAR_RESUME="1")

        assert "run_parallel_crawl.sh" in completed.stdout

    def test_without_the_flag_the_same_run_starts_normally(self, workspace,
                                                            script):
        ws = workspace()

        completed = launch(ws, script, run_id="brand-new")

        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert record_path(ws, "brand-new").exists()


@pytest.mark.parametrize("script", CONTAINER_LAUNCHERS)
class TestReclaimingContainersIsNotPermissionToDiscardEvidence:
    def test_reclaim_does_not_remove_or_rewrite_the_record(self, workspace,
                                                           script):
        ws = workspace()
        launch(ws, script)
        before = record_path(ws).read_text()

        second = launch(ws, script, GENOAR_RECLAIM_RUN="1")

        assert second.returncode == 2
        assert record_path(ws).exists()
        assert record_path(ws).read_text() == before

    def test_the_record_refusal_says_the_flag_will_not_help(self, workspace,
                                                            script):
        """Otherwise a user is told to set GENOAR_RECLAIM_RUN=1 by one message,
        complies, and hits a second, differently named wall."""
        ws = workspace()
        launch(ws, script)
        second = launch(ws, script, GENOAR_RECLAIM_RUN="1")

        assert "never a run record" in second.stdout


@pytest.mark.parametrize("script", CONTAINER_LAUNCHERS)
class TestTheChecksAreOrderedSoTheAdviceIsAlwaysUsable:
    """Three conditions - live, recorded, containers-without-a-record - and
    only the last is one `GENOAR_RECLAIM_RUN=1` resolves. The order decides
    which message a user gets, and therefore whether the advice in it works."""

    # The offer, as a fragment that survives the two scripts wrapping their
    # lines differently. The record refusal names the same flag but says the
    # opposite about it ("never a run record"), so this does not match there.
    RECLAIM_OFFER = "GENOAR_RECLAIM_RUN=1 to discard them"

    def test_a_record_wins_over_leftover_containers(self, workspace, script):
        ws = workspace(STUB_LEFTOVERS)
        record_path(ws).parent.mkdir(parents=True)
        record_path(ws).write_text('{"run_id": "reuse-me"}')

        completed = launch(ws, script)

        assert completed.returncode == 2
        assert "already has a record" in completed.stdout
        assert self.RECLAIM_OFFER not in completed.stdout, (
            "offering a flag that cannot resolve this sends the user in circles"
        )

    def test_the_record_refusal_still_mentions_the_containers(self, workspace,
                                                              script):
        """They are not removed and not hidden - the user should know they are
        there, just not be told a flag will deal with them."""
        ws = workspace(STUB_LEFTOVERS)
        record_path(ws).parent.mkdir(parents=True)
        record_path(ws).write_text('{"run_id": "reuse-me"}')

        completed = launch(ws, script)

        assert "containers are still here too" in completed.stdout

    def test_containers_without_a_record_do_offer_the_flag(self, workspace,
                                                           script):
        ws = workspace(STUB_LEFTOVERS)

        completed = launch(ws, script)

        assert completed.returncode == 2
        assert self.RECLAIM_OFFER in completed.stdout


@pytest.mark.parametrize("script", ALL_LAUNCHERS)
class TestThePrunedHostIsTheCaseContainersAloneMissed:
    def test_a_record_with_no_containers_left_is_still_protected(self,
                                                                 workspace,
                                                                 script):
        """`docker system prune` clears the containers and leaves the record.
        The container check saw an empty daemon and waved the run through, and
        the run then overwrote the record it could not see."""
        ws = workspace(STUB_EMPTY)  # nothing on the daemon at all
        launch(ws, script)
        before = record_path(ws).read_text()

        second = launch(ws, script)

        assert second.returncode == 2
        assert record_path(ws).read_text() == before


@pytest.mark.parametrize("script", ALL_LAUNCHERS)
class TestTheGeneratedIdPathIsUnaffected:
    def test_two_runs_without_an_id_both_succeed(self, workspace, script):
        """The default path mints a fresh id per run, so it can never collide
        with a record - and must not be slowed down by a rule aimed at reuse."""
        ws = workspace()
        first = launch(ws, script, run_id=None)
        time.sleep(1.1)
        second = launch(ws, script, run_id=None)

        assert first.returncode == 0, first.stdout + first.stderr
        assert second.returncode == 0, second.stdout + second.stderr
        assert len(run_ids(ws)) == 2, run_ids(ws)

    def test_a_fresh_explicit_id_still_works(self, workspace, script):
        ws = workspace()
        completed = launch(ws, script, run_id="a-brand-new-id")

        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert record_path(ws, "a-brand-new-id").exists()
