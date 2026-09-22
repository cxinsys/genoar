"""A resume re-enters the run it is resuming, or it does not run.

The defect this pins. `run_parallel_crawl.sh` got the plan right - a resume
leaves `run.json` exactly as the original run wrote it, because the aggregator
measures coverage against it and a rewritable plan is a run that can be made to
look complete by resuming it with a smaller request. But it then rebuilt the
worker assignments from the *current* WORKERS and page arguments, and nothing
compared the two:

    PAGES_PER_WORKER=$((DISTRIBUTED_PAGES / WORKERS))
    REMAINDER=$((DISTRIBUTED_PAGES % WORKERS))

Ask for three workers where the run had two, or 40 pages where it asked for 100,
and every checkpoint under the run is handed to whatever slice today's numbers
make. Worker 2 opens the checkpoint it wrote for pages 51-100 and continues it
through pages 21-40; the pages in between are crawled by nobody, and the merged
manifest still claims them, because the plan it is measured against never
changed. That is whole-range completion evidence for a run that did not cover
its range - the one thing none of these launchers may produce.

What counts as a difference that matters is anything the split is computed from,
because the split is what binds a checkpoint to a page range:

  * the worker count - the divisor;
  * the requested end page - the dividend;
  * the distributed end page - the dividend when GEO holds fewer pages than were
    requested. That one is not asked of the caller at all any more: it is taken
    from the plan and GEO is not re-probed, because GEO's corpus changes daily
    and a larger answer today moves the boundaries just as surely as a different
    argument would.

Everything else is left alone. The output directory and the items-per-page do
not enter the split; the run id *is* the run; the monitor interval and the start
delay are about this process, not about what any worker was given.

And then the check that does not depend on having reasoned that correctly:
every worker recorded the slice it was actually given in `assignment.json`
before it started, and the recomputed slice has to equal it.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

CRAWLER_DIR = Path(__file__).resolve().parent.parent

SCRIPT = "run_parallel_crawl.sh"

STUB = r"""#!/bin/sh
case "$1" in
    images) echo "sha256:stub" ;;
    info)   exit 0 ;;
    inspect) echo "false 0" ;;
    ps)     exit 0 ;;
    run)
        case "$*" in
            *--report-pages*) echo 'GENOAR_PAGE_REPORT {"total_results": 1, "items_per_page": 500, "total_pages": 8}' ;;
            *) echo "stub-container-id" ;;
        esac
        ;;
    *) : ;;
esac
exit 0
"""

RUN_ID = "long-crawl"


@pytest.fixture
def workspace(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    for name in (SCRIPT, "run_crawl.sh", "run_docker_parallel.sh",
                 "Dockerfile.amd64", "Dockerfile.arm64"):
        shutil.copy(CRAWLER_DIR / name, work / name)

    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("docker", "python"):
        stub = binaries / name
        stub.write_text(STUB)
        stub.chmod(0o755)

    return {"dir": work, "bin": binaries}


def launch(workspace, workers=2, pages=8, run_id=RUN_ID, total_pages="8",
           **extra):
    environment = dict(os.environ)
    environment.update({
        "PATH": f"{workspace['bin']}:{environment['PATH']}",
        "GENOAR_SKIP_MONITOR": "1",
        "GENOAR_WORKER_START_DELAY": "0",
        "GENOAR_RUN_ID": run_id,
    })
    if total_pages is None:
        environment.pop("GENOAR_TOTAL_PAGES", None)
    else:
        environment["GENOAR_TOTAL_PAGES"] = total_pages
    environment.update({k: str(v) for k, v in extra.items()})

    completed = subprocess.run(
        ["bash", SCRIPT, str(workers), str(pages)],
        cwd=str(workspace["dir"]), input="", text=True,
        capture_output=True, env=environment, timeout=180,
    )
    return completed.returncode, completed.stdout + completed.stderr


def run_dir(workspace, run_id=RUN_ID):
    return workspace["dir"] / "crawl_output" / "runs" / run_id


def assignments(workspace, run_id=RUN_ID):
    """{worker id: (start, end)} as recorded on disk."""
    workers = run_dir(workspace, run_id) / "workers"
    if not workers.is_dir():
        return {}
    found = {}
    for directory in sorted(workers.iterdir()):
        recorded = directory / "assignment.json"
        if recorded.exists():
            data = json.loads(recorded.read_text())
            found[data["worker_id"]] = (data["start_page"], data["end_page"])
    return found


def plant_checkpoints(workspace, run_id=RUN_ID):
    """What a half-finished run leaves behind: one checkpoint per worker.

    Their content does not matter here - what matters is that the launcher
    passes `--resume` for a worker that has one, so the range that worker is
    started on is the range its checkpoint is continued through.
    """
    for directory in (run_dir(workspace, run_id) / "workers").iterdir():
        (directory / "checkpoint.json").write_text(
            json.dumps({"last_page": 1, "worker_id": directory.name})
        )


class TestAResumeKeepsEveryWorkerOnItsOwnPages:
    def test_the_same_request_resumes_onto_the_same_slices(self, workspace):
        code, output = launch(workspace)
        assert code == 0, output
        original = assignments(workspace)
        assert original == {"1": (1, 4), "2": (5, 8)}
        plant_checkpoints(workspace)

        code, output = launch(workspace, GENOAR_RESUME="1")

        assert code == 0, output
        assert assignments(workspace) == original
        assert "Worker 1 resumes from" in output
        assert "Worker 2 resumes from" in output

    def test_more_workers_than_the_run_had_is_refused(self, workspace):
        """The reproduction. Two workers held pages 1-4 and 5-8; asked for
        four, the split becomes 1-2, 3-4, 5-6, 7-8, and worker 2's checkpoint -
        written for pages 5-8 - is continued through pages 3-4."""
        assert launch(workspace)[0] == 0
        plant_checkpoints(workspace)
        original = assignments(workspace)

        code, output = launch(workspace, workers=4, GENOAR_RESUME="1")

        assert code == 2, output
        assert "workers: the plan says 2, this resume asks for 4" in output
        assert assignments(workspace) == original, "nothing may be reassigned"
        assert "Starting worker" not in output

    def test_fewer_workers_than_the_run_had_is_refused(self, workspace):
        assert launch(workspace)[0] == 0
        plant_checkpoints(workspace)

        code, output = launch(workspace, workers=1, GENOAR_RESUME="1")

        assert code == 2, output
        assert "this resume asks for 1" in output

    def test_a_different_page_request_is_refused(self, workspace):
        """Pages 1-8 were requested and split; resuming with 4 would give
        worker 1 pages 1-2 and worker 2 pages 3-4, and pages 5-8 - which worker
        2 was part way through - would be nobody's."""
        assert launch(workspace)[0] == 0
        plant_checkpoints(workspace)

        code, output = launch(workspace, pages=4, total_pages="4",
                              GENOAR_RESUME="1")

        assert code == 2, output
        assert "pages requested: the plan says 1-8, this resume asks for 1-4" in output

    def test_the_refusal_says_how_to_resume_it_properly(self, workspace):
        assert launch(workspace)[0] == 0

        _, output = launch(workspace, workers=4, GENOAR_RESUME="1")

        assert "Resume it as it was:" in output
        assert "2 8" in output

    def test_a_bigger_corpus_does_not_move_the_boundaries(self, workspace):
        """The run was split against a corpus of 4; GEO now reports 8. That is
        not a new request and must not redistribute a run whose checkpoints were
        written against the old split - so the plan's own distributed end page
        stands and GEO is not asked again."""
        assert launch(workspace, workers=2, pages=8, total_pages="4")[0] == 0
        original = assignments(workspace)
        assert original == {"1": (1, 2), "2": (3, 4)}
        plant_checkpoints(workspace)

        code, output = launch(workspace, workers=2, pages=8, total_pages="8",
                              GENOAR_RESUME="1")

        assert code == 0, output
        assert assignments(workspace) == original
        assert "not re-probed" in output

    def test_the_corpus_is_not_probed_again_on_a_resume(self, workspace):
        """Not merely ignored: asking GEO costs a browser session, and an
        answer that cannot change anything is an answer nobody needs."""
        assert launch(workspace, total_pages="8")[0] == 0
        plant_checkpoints(workspace)

        code, output = launch(workspace, total_pages=None, GENOAR_RESUME="1")

        assert code == 0, output
        assert "Asking GEO how many pages" not in output


class TestTheRecordedAssignmentIsTheLastWord:
    """The reasoning above can be wrong, or a plan can be edited. What each
    worker was actually given is on disk beside its checkpoint."""

    def test_a_slice_that_does_not_match_what_was_recorded_is_refused(
        self, workspace
    ):
        assert launch(workspace)[0] == 0
        plant_checkpoints(workspace)
        # A worker that started on a different range than today's split makes -
        # however it got that way.
        recorded = run_dir(workspace) / "workers" / "worker-2" / "assignment.json"
        recorded.write_text(json.dumps(
            {"worker_id": "2", "start_page": 6, "end_page": 8}
        ))

        code, output = launch(workspace, GENOAR_RESUME="1")

        assert code == 2, output
        assert "pages they never started" in output
        assert "worker 2: started on pages 6-8" in output
        assert json.loads(recorded.read_text())["start_page"] == 6

    def test_a_worker_that_never_started_does_not_block_the_resume(self, workspace):
        """A run whose later workers were never reached has no assignment for
        them. There is nothing to contradict, and refusing would make a
        half-started run unresumable."""
        assert launch(workspace)[0] == 0
        plant_checkpoints(workspace)
        shutil.rmtree(run_dir(workspace) / "workers" / "worker-2")

        code, output = launch(workspace, GENOAR_RESUME="1")

        assert code == 0, output
        assert assignments(workspace) == {"1": (1, 4), "2": (5, 8)}


class TestAPlanThatCannotBeReadIsNotAPlanToResumeInto:
    def test_a_plan_missing_its_split_is_refused(self, workspace):
        """A plan without the numbers the split was made from cannot say what
        any checkpoint belongs to, so there is nothing to check the resume
        against - and an unverifiable resume must not read as a verified one."""
        assert launch(workspace)[0] == 0
        plan = run_dir(workspace) / "run.json"
        plan.write_text(json.dumps({"run_id": RUN_ID}))

        code, output = launch(workspace, GENOAR_RESUME="1")

        assert code == 2, output
        assert "does not" in output and "record how the run was split" in output
        assert "Starting worker" not in output

    def test_the_plan_is_never_rewritten_by_a_refused_resume(self, workspace):
        assert launch(workspace)[0] == 0
        before = (run_dir(workspace) / "run.json").read_text()

        launch(workspace, workers=4, GENOAR_RESUME="1")

        assert (run_dir(workspace) / "run.json").read_text() == before
        assert not (run_dir(workspace) / "resumes.jsonl").exists()

    def test_an_accepted_resume_still_leaves_the_plan_alone(self, workspace):
        assert launch(workspace)[0] == 0
        before = json.loads((run_dir(workspace) / "run.json").read_text())

        assert launch(workspace, GENOAR_RESUME="1")[0] == 0

        assert json.loads((run_dir(workspace) / "run.json").read_text()) == before
        assert before["requested_pages"] == {"start": 1, "end": 8}
