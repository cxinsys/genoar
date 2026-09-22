"""How the three launcher scripts split a page range across workers.

The defect this pins, in run_crawl.sh: it computed only
`PAGES_PER_WORKER=MAX_PAGES/WORKERS`. With 1 page and 4 workers that is 0, so
the first three workers were handed `START_PAGE=1 END_PAGE=0` - and an end page
of 0 is the crawler's "crawl every page" sentinel. Three containers each began
a full crawl of GEO.

The other two scripts already validated their input and spread the remainder;
this file holds all three to the same rules, and adds the rule that came with
the page probe: the split is against the pages GEO actually holds.

Each script is run against stub `docker` / `python` executables, so nothing
here starts a browser or reaches GEO. Most tests stop after the launch phase;
the last class runs one launcher all the way through its monitor loop and its
aggregation, because what the run's completion evidence ends up claiming is the
other half of the same defect.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CRAWLER_DIR = Path(__file__).resolve().parent.parent

STUB_DOCKER = r"""#!/bin/sh
# Records `docker run` invocations; answers everything else so the script under
# test walks its normal path without a daemon.
case "$1" in
    images) echo "sha256:stub" ;;
    info)   exit 0 ;;
    run)
        shift
        printf 'run %s\n' "$*" >> "$STUB_LOG"
        case "$*" in
            *--report-pages*)
                if [ -n "${STUB_TOTAL_PAGES:-}" ]; then
                    printf 'GENOAR_PAGE_REPORT {"total_results": 1, "items_per_page": 500, "total_pages": %s}\n' "$STUB_TOTAL_PAGES"
                else
                    echo "probe failed"
                    exit 1
                fi
                ;;
            *) echo "stub-container-id" ;;
        esac
        ;;
    *) : ;;
esac
exit 0
"""

STUB_PYTHON = r"""#!/bin/sh
# Stands in for `python genoar_crawler.py ...`.
#
# The corpus probe and the crawl itself are answered here, so no browser starts
# and GEO is never touched. Aggregation is not stubbed: it is handed to the real
# crawler module, because the whole point of these tests is what the run's
# completion evidence ends up saying.
printf 'run %s\n' "$*" >> "$STUB_LOG"
case "$*" in
    *--report-pages*)
        if [ -n "${STUB_TOTAL_PAGES:-}" ]; then
            printf 'GENOAR_PAGE_REPORT {"total_results": 1, "items_per_page": 500, "total_pages": %s}\n' "$STUB_TOTAL_PAGES"
            exit 0
        fi
        echo "probe failed"
        exit 1
        ;;
    *--aggregate*)
        shift
        exec "$REAL_PYTHON" "$CRAWLER_DIR/genoar_crawler.py" "$@"
        ;;
esac

# A worker: "$1 $2 $3 true -o $6" - write the slice manifest a real one would,
# and a file in SMTX so the run does not look like it collected nothing. The
# manifest records both the file and the corpus size the worker saw, because
# that is what a real worker records and what the aggregator judges it on.
[ "${STUB_WORKER_EXIT:-0}" = "0" ] || exit "$STUB_WORKER_EXIT"
exec "$REAL_PYTHON" -c '
import os, sys
sys.path.insert(0, os.environ["CRAWLER_DIR"])
import genoar_crawler as gc
start, end, output = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
scope = gc.resolve_worker_scope(output)
scope.state_dir.mkdir(parents=True, exist_ok=True)
smtx = scope.output_dir / "SMTX"
smtx.mkdir(parents=True, exist_ok=True)
(smtx / f"GSE{start}_series_matrix.txt.gz").write_text("stub")
corpus = os.environ.get("STUB_TOTAL_PAGES")
gc.write_completion_manifest(
    scope.state_dir, requested_start=start, requested_end=end,
    completed_start=start, completed_end=int(os.environ.get("STUB_COMPLETED_END", end)),
    items_per_page=500, processed=1, failed=0,
    run_id=scope.run_id, worker_id=scope.worker_id,
    corpus_pages=int(corpus) if corpus else None,
    collected_files={"SMTX": 1, "SRR": 0, "META": 0},
)
' "$2" "$3" "$6"
"""


@pytest.fixture
def workspace(tmp_path):
    """A directory holding the scripts, stub executables and a log."""
    work = tmp_path / "work"
    work.mkdir()
    for name in ("run_crawl.sh", "run_parallel_crawl.sh", "run_docker_parallel.sh",
                 "Dockerfile.amd64", "Dockerfile.arm64"):
        shutil.copy(CRAWLER_DIR / name, work / name)

    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "docker").write_text(STUB_DOCKER)
    (binaries / "python").write_text(STUB_PYTHON)
    for stub in binaries.iterdir():
        stub.chmod(0o755)

    return {"dir": work, "bin": binaries, "log": tmp_path / "stub.log"}


def run_script(workspace, script, args=(), stdin="", total_pages=None, env=None):
    environment = dict(os.environ)
    environment.update({
        "PATH": f"{workspace['bin']}:{environment['PATH']}",
        "STUB_LOG": str(workspace["log"]),
        "GENOAR_SKIP_MONITOR": "1",
        "GENOAR_WORKER_START_DELAY": "0",
        "GENOAR_RUN_ID": "test-run",
        "REAL_PYTHON": sys.executable,
        "CRAWLER_DIR": str(CRAWLER_DIR),
    })
    if total_pages is not None:
        environment["STUB_TOTAL_PAGES"] = str(total_pages)
    environment.update(env or {})

    # A timeout, not patience: every one of these scripts ends in a monitoring
    # loop that only exits when its workers do, and GENOAR_SKIP_MONITOR is what
    # keeps this test out of it. If that hook ever stops working the test must
    # fail rather than hang.
    completed = subprocess.run(
        ["bash", script, *[str(a) for a in args]],
        cwd=str(workspace["dir"]), input=stdin, text=True,
        capture_output=True, env=environment, timeout=120,
    )
    log = workspace["log"]
    return completed, (log.read_text().splitlines() if log.exists() else [])


def worker_ranges(workspace, run_id="test-run"):
    """(start, end) for every worker the script actually started.

    Read from the assignment files rather than the stub log: those are written
    by the shell itself before each launch, so they are complete the moment the
    script exits, and they are the same record the aggregator later reads back.
    run_parallel_crawl.sh starts its workers in the background, so its stub log
    is still being appended to when the script returns.
    """
    workers = workspace["dir"] / "crawl_output" / "runs" / run_id / "workers"
    if not workers.is_dir():
        return []
    ranges = []
    for directory in sorted(workers.iterdir()):
        assignment = json.loads((directory / "assignment.json").read_text())
        ranges.append((assignment["start_page"], assignment["end_page"]))
    ranges.sort()
    return ranges


def launched_ranges(lines):
    """(start, end) as they reached a crawler's own command line."""
    ranges = []
    for line in lines:
        if "--report-pages" in line or "--aggregate" in line:
            continue
        match = re.search(r'(?:^|\s)(\d+) (\d+) true(?:\s|$)', line)
        if match:
            ranges.append((int(match.group(1)), int(match.group(2))))
    return ranges


# (script, arguments, stdin) for the same request under each launcher.
def one_page_four_workers(script):
    if script == "run_crawl.sh":
        return script, (), "4\n1\ny\n"
    return script, (4, 1), ""


def ten_pages_four_workers(script):
    if script == "run_crawl.sh":
        return script, (), "4\n10\ny\n"
    return script, (4, 10), ""


ALL_SCRIPTS = ["run_crawl.sh", "run_parallel_crawl.sh", "run_docker_parallel.sh"]


@pytest.mark.parametrize("script", ALL_SCRIPTS)
class TestOnePageFourWorkers:
    def test_exactly_one_worker_gets_page_one(self, workspace, script):
        run_script(workspace, *one_page_four_workers(script), total_pages=47)
        ranges = worker_ranges(workspace)

        assert ranges == [(1, 1)], f"expected one worker on page 1, got {ranges}"

    def test_no_worker_is_handed_end_page_zero(self, workspace, script):
        """End page 0 is the crawler's 'crawl every page' sentinel: handing it
        to three workers started three full crawls of GEO."""
        run_script(workspace, *one_page_four_workers(script), total_pages=47)

        assert all(end != 0 for _, end in worker_ranges(workspace))

    def test_the_workers_with_no_pages_are_reported_as_skipped(
        self, workspace, script
    ):
        completed, _ = run_script(workspace, *one_page_four_workers(script),
                                  total_pages=47)
        assert completed.stdout.count("no pages left to assign") == 3


@pytest.mark.parametrize("script", ["run_crawl.sh", "run_docker_parallel.sh"])
class TestWhatTheContainersReceive:
    """The two Docker launchers start their containers in the foreground, so
    what each crawler was really given can be read straight back."""

    def test_no_container_is_started_with_end_page_zero(self, workspace, script):
        _, lines = run_script(workspace, *one_page_four_workers(script),
                              total_pages=47)

        assert launched_ranges(lines) == [(1, 1)]


@pytest.mark.parametrize("script", ALL_SCRIPTS)
class TestRemainderIsSpread:
    def test_ten_pages_over_four_workers(self, workspace, script):
        """Not 2/2/2/4: the remainder goes to the first workers, one each."""
        run_script(workspace, *ten_pages_four_workers(script), total_pages=47)

        assert worker_ranges(workspace) == [(1, 3), (4, 6), (7, 8), (9, 10)]

    def test_the_slices_are_contiguous_and_disjoint(self, workspace, script):
        run_script(workspace, *ten_pages_four_workers(script), total_pages=47)

        pages = [p for start, end in worker_ranges(workspace)
                 for p in range(start, end + 1)]
        assert pages == list(range(1, 11))


@pytest.mark.parametrize("script", ALL_SCRIPTS)
class TestSplitAgainstTheRealPageCount:
    def test_a_short_corpus_shrinks_what_is_distributed(self, workspace, script):
        """100 pages requested, 3 on GEO: the later workers must not be
        started with slices that can only come back empty."""
        args = (script, (), "4\n100\ny\n") if script == "run_crawl.sh" \
            else (script, (4, 100), "")
        run_script(workspace, *args, total_pages=3)

        assert worker_ranges(workspace) == [(1, 1), (2, 2), (3, 3)]

    def test_a_failed_probe_falls_back_to_the_request(self, workspace, script):
        """Correctness never depended on the probe - each worker still caps
        itself - so an unreachable probe must not stop the run."""
        args = (script, (), "2\n4\ny\n") if script == "run_crawl.sh" \
            else (script, (2, 4), "")
        completed, _ = run_script(workspace, *args, total_pages=None)

        assert worker_ranges(workspace) == [(1, 2), (3, 4)]
        assert "Could not read the page count" in completed.stdout

    def test_the_probe_can_be_supplied_instead_of_run(self, workspace, script):
        args = (script, (), "4\n100\ny\n") if script == "run_crawl.sh" \
            else (script, (4, 100), "")
        _, lines = run_script(workspace, *args, env={"GENOAR_TOTAL_PAGES": "2"})

        assert worker_ranges(workspace) == [(1, 1), (2, 2)]
        assert not any("--report-pages" in line for line in lines)


@pytest.mark.parametrize("script", ALL_SCRIPTS)
class TestInvalidInput:
    @pytest.mark.parametrize("workers", ["0", "-1", "abc"])
    def test_a_bad_worker_count_exits_two(self, workspace, script, workers):
        args = (script, (), f"{workers}\n10\ny\n") if script == "run_crawl.sh" \
            else (script, (workers, 10), "")
        completed, _ = run_script(workspace, *args, total_pages=47)

        assert completed.returncode == 2

    @pytest.mark.parametrize("pages", ["0", "-3", "many"])
    def test_a_bad_page_count_exits_two(self, workspace, script, pages):
        args = (script, (), f"4\n{pages}\ny\n") if script == "run_crawl.sh" \
            else (script, (4, pages), "")
        completed, _ = run_script(workspace, *args, total_pages=47)

        assert completed.returncode == 2


@pytest.mark.parametrize("script", ["run_crawl.sh", "run_docker_parallel.sh"])
class TestContainerWorkersAreGivenAnIdentity:
    def test_every_container_is_given_its_own_worker_id(self, workspace, script):
        """Without this the crawler falls back to its process id, which is 1 in
        every container, and they all collide in /data."""
        args = (script, (), "3\n6\ny\n") if script == "run_crawl.sh" \
            else (script, (3, 6), "")
        _, lines = run_script(workspace, *args, total_pages=47)

        launches = [line for line in lines if "GENOAR_WORKER_ID" in line]
        assert len(launches) == 3
        ids = sorted(re.search(r"GENOAR_WORKER_ID=(\S+)", line).group(1)
                     for line in launches)
        assert ids == ["1", "2", "3"]
        assert all("GENOAR_RUN_ID=test-run" in line for line in launches)


class TestPythonWorkersAreGivenAnIdentity:
    def test_run_parallel_crawl_exports_the_identity(self, workspace):
        """It runs workers as background processes, so the identity travels in
        the environment rather than in `docker run -e`; the run directory it
        creates for each worker is the observable half."""
        run_script(workspace, "run_parallel_crawl.sh", (3, 6), total_pages=47,
                   env={"GENOAR_SKIP_MONITOR": "1"})

        workers = workspace["dir"] / "crawl_output" / "runs" / "test-run" / "workers"
        assert sorted(p.name for p in workers.iterdir()) == [
            "worker-1", "worker-2", "worker-3"
        ]
        for name, expected in (("worker-1", (1, 2)), ("worker-2", (3, 4)),
                               ("worker-3", (5, 6))):
            assignment = (workers / name / "assignment.json").read_text()
            assert f'"start_page": {expected[0]}' in assignment
            assert f'"end_page": {expected[1]}' in assignment


class TestRunsAreIsolatedFromEachOther:
    def test_a_second_run_does_not_touch_the_first_ones_directory(self, workspace):
        run_script(workspace, "run_parallel_crawl.sh", (2, 4), total_pages=47,
                   env={"GENOAR_RUN_ID": "first"})
        run_script(workspace, "run_parallel_crawl.sh", (2, 4), total_pages=47,
                   env={"GENOAR_RUN_ID": "second"})

        runs = workspace["dir"] / "crawl_output" / "runs"
        assert sorted(p.name for p in runs.iterdir()) == ["first", "second"]
        assert (runs / "first" / "run.json").exists()

    def test_a_run_id_that_is_a_path_is_refused(self, workspace):
        completed, _ = run_script(workspace, "run_parallel_crawl.sh", (2, 4),
                                  total_pages=47,
                                  env={"GENOAR_RUN_ID": "../escape"})
        assert completed.returncode == 2


class TestTheRunIsAggregatedOnce:
    """run_parallel_crawl.sh all the way through: launch, monitor, aggregate.

    The workers are stubbed - they write the slice manifest a real crawler
    would and nothing else - but the aggregation is the real one, so these say
    what the run's completion evidence actually ends up claiming.
    """

    def full_run(self, workspace, workers=2, pages=4, env=None):
        return run_script(
            workspace, "run_parallel_crawl.sh", (workers, pages),
            total_pages=47,
            env={"GENOAR_SKIP_MONITOR": "", "GENOAR_MONITOR_INTERVAL": "1",
                 **(env or {})},
        )

    def manifest(self, workspace, *parts):
        path = workspace["dir"].joinpath("crawl_output", *parts)
        return json.loads(path.read_text()) if path.exists() else None

    def test_a_completed_run_writes_one_whole_range_manifest(self, workspace):
        completed, _ = self.full_run(workspace)

        assert completed.returncode == 0, completed.stdout
        merged = self.manifest(workspace, "runs", "test-run", "crawl_manifest.json")
        assert merged["completed_pages"] == {"start": 1, "end": 4}
        assert merged["requested_pages"] == {"start": 1, "end": 4}
        assert merged["source"] == "aggregated"

    def test_the_top_level_manifest_points_at_that_run(self, workspace):
        self.full_run(workspace)

        pointer = self.manifest(workspace, "crawl_manifest.json")
        assert pointer["run_id"] == "test-run"
        assert pointer["completed_pages"] == {"start": 1, "end": 4}

    def test_no_worker_manifest_claims_the_whole_range(self, workspace):
        self.full_run(workspace)

        workers = (workspace["dir"] / "crawl_output" / "runs" / "test-run"
                   / "workers")
        for directory in sorted(workers.iterdir()):
            slice_manifest = json.loads(
                (directory / "crawl_manifest.json").read_text()
            )
            assert slice_manifest["source"] == "worker"
            assert slice_manifest["completed_pages"] != {"start": 1, "end": 4}

    def test_a_failed_worker_leaves_no_whole_range_manifest(self, workspace):
        """A partially completed run must not produce completion evidence."""
        completed, _ = self.full_run(workspace, env={"STUB_WORKER_EXIT": "1"})

        assert completed.returncode == 1
        assert self.manifest(workspace, "crawl_manifest.json") is None
        assert self.manifest(workspace, "runs", "test-run",
                             "crawl_manifest.json") is None

    def test_a_run_where_nothing_was_in_range_exits_three(self, workspace):
        completed, _ = self.full_run(workspace, env={"STUB_WORKER_EXIT": "3"})

        assert completed.returncode == 3
        assert self.manifest(workspace, "crawl_manifest.json") is None

    def test_a_failed_run_does_not_leave_the_previous_manifest_standing(
        self, workspace
    ):
        """Otherwise the newest failure inherits the last success's evidence."""
        self.full_run(workspace, env={"GENOAR_RUN_ID": "earlier"})
        assert self.manifest(workspace, "crawl_manifest.json") is not None

        completed, _ = self.full_run(
            workspace, env={"GENOAR_RUN_ID": "later", "STUB_WORKER_EXIT": "1"}
        )

        assert completed.returncode == 1
        assert self.manifest(workspace, "crawl_manifest.json") is None
        # The earlier run's own evidence is untouched.
        earlier = self.manifest(workspace, "runs", "earlier", "crawl_manifest.json")
        assert earlier["run_id"] == "earlier"

    def test_a_worker_that_stopped_short_is_not_smoothed_over(self, workspace):
        """The slice manifests are checked against the assignments, so a worker
        that covered less than it was given cannot be counted as complete."""
        completed, _ = self.full_run(
            workspace, workers=2, pages=4, env={"STUB_COMPLETED_END": "1"}
        )

        assert completed.returncode == 1
        assert self.manifest(workspace, "crawl_manifest.json") is None
