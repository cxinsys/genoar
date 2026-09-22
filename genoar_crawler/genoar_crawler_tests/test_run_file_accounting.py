"""What the parallel launchers report and exit on: this run, not the directory.

The defect these pin: both launchers ended with

    SMTX_COUNT=$(count_files "$OUTPUT_DIR/SMTX" '*.gz')
    ...
    if [ "$TOTAL_FILES" -eq 0 ]; then exit 4

against an output directory that is deliberately persistent - SMTX/SRR/META
hold the results of every run ever made into it, because earlier results must
survive a new run. So a single file left over from any earlier run made a run
that downloaded nothing report "3 files collected" and exit 0, and exit 4
("workers finished cleanly but this run collected nothing") could not fire at
all once the directory was non-empty.

What this run collected is what its workers recorded writing, merged by the
aggregator: `collected_files` in the run manifest. The lifetime contents of the
directory are still shown - they are what an operator wants to see - but
labelled as the directory's, and nothing branches on them.

Both launchers are driven end to end here: launch, monitor loop, aggregation,
summary and exit status. Only the browser and the container runtime are stubbed
- the aggregation and the entire shell summary are the shipped ones.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from selenium.common.exceptions import NoSuchElementException

CRAWLER_DIR = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(CRAWLER_DIR))
import genoar_crawler as gc  # noqa: E402

# One stubbed worker, shared by both launchers: it writes the slice manifest a
# real worker writes, and STUB_FILES_PER_WORKER files into the shared output
# directory - the two halves that have to agree for the run's own count to mean
# anything. The optional manifest fields are passed only when the crawler
# accepts them, so this file reproduces the defect on unfixed code instead of
# dying of a TypeError.
STUB_WORKER = r'''
import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["CRAWLER_DIR"])
import genoar_crawler as gc


def run_worker(output_dir, start, end, env):
    exit_code = int(env.get("STUB_WORKER_EXIT", "0"))
    if exit_code:
        return exit_code

    scope = gc.resolve_worker_scope(output_dir, env=env)
    scope.state_dir.mkdir(parents=True, exist_ok=True)

    # Which buckets this worker downloads into. The default is SMTX, the only
    # one a bare output directory scan looks at.
    names = {"SMTX": "GSE{gse}_series_matrix.txt.gz", "SRR": "GSE{gse}.txt",
             "META": "GSE{gse}_meta.txt"}
    collected = {"SMTX": 0, "SRR": 0, "META": 0}
    for index in range(int(env.get("STUB_FILES_PER_WORKER", "1"))):
        for bucket in env.get("STUB_BUCKETS", "SMTX").split(","):
            folder = Path(output_dir) / bucket
            folder.mkdir(parents=True, exist_ok=True)
            (folder / names[bucket].format(gse=f"{start}{index}")).write_text("stub")
            collected[bucket] += 1

    completed_end = int(env.get("STUB_COMPLETED_END", end))
    fields = dict(
        requested_start=start, requested_end=end, completed_start=start,
        completed_end=completed_end, items_per_page=500,
        processed=sum(collected.values()), failed=0,
        run_id=scope.run_id, worker_id=scope.worker_id,
    )
    optional = {
        "corpus_pages": int(env["STUB_CORPUS_PAGES"])
        if env.get("STUB_CORPUS_PAGES") else None,
        "collected_files": collected,
    }
    accepted = inspect.signature(gc.write_completion_manifest).parameters
    fields.update({k: v for k, v in optional.items() if k in accepted})

    gc.write_completion_manifest(scope.state_dir, **fields)
    return 0


def page_report(env):
    total = env.get("STUB_TOTAL_PAGES")
    if not total:
        print("probe failed")
        return 1
    print(gc.PAGE_REPORT_PREFIX
          + '{"total_results": 1, "items_per_page": 500, '
          + f'"total_pages": {total}}}')
    return 0


def aggregate(argv):
    """The real aggregator, reached exactly as the launcher reaches it."""
    sys.argv = ["genoar_crawler.py"] + list(argv)
    try:
        gc.main()
    except SystemExit as exit_call:
        return exit_call.code or 0
    return 0
'''

# Stands in for `python genoar_crawler.py ...`.
STUB_PYTHON = r'''
import os
import sys

sys.path.insert(0, os.environ["STUB_DIR"])
from stub_worker import aggregate, page_report, run_worker

argv = sys.argv[1:]           # genoar_crawler.py <args...>
with open(os.environ["STUB_LOG"], "a") as log:
    log.write("run " + " ".join(argv) + "\n")
args = argv[1:]

if "--report-pages" in args:
    sys.exit(page_report(os.environ))
if "--aggregate" in args:
    sys.exit(aggregate(args))

# A worker: "<start> <end> true -o <output>"
sys.exit(run_worker(args[4], int(args[0]), int(args[1]), os.environ))
'''

# Stands in for the container runtime. `docker run -d` runs the stubbed worker
# then and there and remembers its exit code, so `docker inspect` answers the
# monitor loop the way a real daemon would.
STUB_DOCKER = r'''
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["STUB_DIR"])
from stub_worker import aggregate, page_report, run_worker

argv = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(" ".join(argv) + "\n")

command, rest = (argv[0], argv[1:]) if argv else ("", [])
states = Path(os.environ["STUB_DIR"]) / "containers"
states.mkdir(exist_ok=True)


def flag_values(args, name):
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == name]


if command == "images":
    print("sha256:stub")
elif command == "inspect":
    state = states / rest[-1]
    if not state.exists():
        sys.exit(1)
    print(f"false {state.read_text().strip()}")
elif command == "run":
    # -v <host>:/data is how the container reaches the output directory.
    mounts = flag_values(rest, "-v")
    output_dir = mounts[0].split(":")[0] if mounts else "crawl_output"
    tail = rest[rest.index([a for a in rest if a.startswith("genoar:")][0]) + 1:]

    if "--report-pages" in tail:
        sys.exit(page_report(os.environ))
    if "--aggregate" in tail:
        arguments = [output_dir if a == "/data" else a for a in tail]
        sys.exit(aggregate(arguments))

    environment = dict(os.environ)
    for assignment in flag_values(rest, "-e"):
        key, _, value = assignment.partition("=")
        environment[key] = value
    code = run_worker(output_dir, int(tail[0]), int(tail[1]), environment)
    (states / flag_values(rest, "--name")[0]).write_text(str(code))
    print("stub-container-id")
'''

SHELL_WRAPPER = '#!/bin/sh\nexec "$REAL_PYTHON" "$STUB_DIR/{}" "$@"\n'


@pytest.fixture
def workspace(tmp_path):
    """The launchers, the stubs they will find on PATH, and an empty output."""
    work = tmp_path / "work"
    work.mkdir()
    for name in ("run_parallel_crawl.sh", "run_docker_parallel.sh",
                 "run_crawl.sh", "Dockerfile.amd64", "Dockerfile.arm64"):
        shutil.copy(CRAWLER_DIR / name, work / name)

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "stub_worker.py").write_text(STUB_WORKER)
    (stubs / "python_stub.py").write_text(STUB_PYTHON)
    (stubs / "docker_stub.py").write_text(STUB_DOCKER)

    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name, implementation in (("python", "python_stub.py"),
                                 ("docker", "docker_stub.py")):
        (binaries / name).write_text(SHELL_WRAPPER.format(implementation))
        (binaries / name).chmod(0o755)

    return {"dir": work, "bin": binaries, "stubs": stubs,
            "log": tmp_path / "stub.log",
            "output": work / "crawl_output"}


def earlier_results(workspace, count=1):
    """Files an earlier run left in the shared output directory."""
    written = []
    for index in range(count):
        for folder, name in (("SMTX", f"GSE900{index}_series_matrix.txt.gz"),
                             ("SRR", f"GSE900{index}.txt"),
                             ("META", f"GSE900{index}_meta.txt")):
            path = workspace["output"] / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("from an earlier run")
            written.append(path)
    return written


def run_launcher(workspace, script, workers=1, pages=2, total_pages=4, env=None):
    """One launcher, all the way through: launch, monitor, aggregate, summary.

    run_crawl.sh asks for the same two numbers on stdin instead of taking them
    as arguments, and confirms before it starts; the rest of the run is the
    same, which is the point of holding all three to these tests.
    """
    environment = dict(os.environ)
    environment.update({
        "PATH": f"{workspace['bin']}:{environment['PATH']}",
        "STUB_LOG": str(workspace["log"]),
        "STUB_DIR": str(workspace["stubs"]),
        "REAL_PYTHON": sys.executable,
        "CRAWLER_DIR": str(CRAWLER_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
        "GENOAR_RUN_ID": "test-run",
        "GENOAR_WORKER_START_DELAY": "0",
        "GENOAR_MONITOR_INTERVAL": "1",
        "GENOAR_SKIP_MONITOR": "",
    })
    if total_pages is not None:
        environment["STUB_TOTAL_PAGES"] = str(total_pages)
        environment["STUB_CORPUS_PAGES"] = str(total_pages)
    environment.update(env or {})

    arguments = [str(workers), str(pages)]
    stdin = ""
    if script == "run_docker_parallel.sh":
        arguments.append("amd64")
    elif script == "run_crawl.sh":
        arguments, stdin = [], f"{workers}\n{pages}\ny\n"

    # A timeout, not patience: every launcher ends in a loop that exits only
    # when its workers do. If the stubs ever stop answering, this must fail
    # rather than hang.
    return subprocess.run(
        ["bash", script, *arguments], cwd=str(workspace["dir"]), text=True,
        input=stdin, capture_output=True, env=environment, timeout=180,
    )


ALL_LAUNCHERS = ["run_parallel_crawl.sh", "run_docker_parallel.sh",
                 "run_crawl.sh"]


@pytest.mark.parametrize("script", ALL_LAUNCHERS)
class TestExitFourDescribesThisRun:
    def test_a_run_that_collected_nothing_exits_four(self, workspace, script):
        """No leftovers at all: the straightforward case."""
        completed = run_launcher(workspace, script,
                                 env={"STUB_FILES_PER_WORKER": "0"})

        assert completed.returncode == 4, completed.stdout

    def test_earlier_results_do_not_rescue_it(self, workspace, script):
        """The defect: one file from any earlier run turned "this run collected
        nothing" into a success."""
        earlier_results(workspace)

        completed = run_launcher(workspace, script,
                                 env={"STUB_FILES_PER_WORKER": "0"})

        assert completed.returncode == 4, completed.stdout
        assert "collected 0 files" in completed.stdout

    def test_a_run_that_collected_files_still_succeeds(self, workspace, script):
        completed = run_launcher(workspace, script,
                                 env={"STUB_FILES_PER_WORKER": "2"})

        assert completed.returncode == 0, completed.stdout

    def test_a_run_that_collected_only_srr_and_meta_is_not_empty(
        self, workspace, script
    ):
        """A GSE can yield an accession list and a metadata table without a
        series matrix. run_crawl.sh counted `*.gz` alone, so such a run - real
        files, real work - reported that it had collected nothing."""
        completed = run_launcher(workspace, script,
                                 env={"STUB_BUCKETS": "SRR,META"})

        assert completed.returncode == 0, completed.stdout
        assert "This run collected 2 file(s)" in completed.stdout

    def test_the_earlier_results_are_left_where_they_were(self, workspace, script):
        """Counting this run's output must never mean clearing the directory
        first: those are somebody's earlier results."""
        earlier = earlier_results(workspace, count=2)

        run_launcher(workspace, script, env={"STUB_FILES_PER_WORKER": "1"})

        assert [path for path in earlier if not path.exists()] == []
        assert all(path.read_text() == "from an earlier run" for path in earlier)


@pytest.mark.parametrize("script", ALL_LAUNCHERS)
class TestTheSummaryKeepsTheTwoCountsApart:
    def test_this_runs_files_are_counted_from_the_run_manifest(
        self, workspace, script
    ):
        earlier_results(workspace, count=2)   # 6 files that are not this run's

        completed = run_launcher(workspace, script, workers=2, pages=2,
                                 env={"STUB_FILES_PER_WORKER": "1"})

        assert completed.returncode == 0, completed.stdout
        merged = json.loads(
            (workspace["output"] / "runs" / "test-run" / "crawl_manifest.json")
            .read_text()
        )
        assert merged["collected_files"]["total"] == 2
        assert "This run collected 2 file(s)" in completed.stdout

    def test_the_directory_total_is_shown_and_labelled(self, workspace, script):
        """Both numbers are useful; only one of them is about this run, so the
        summary may not leave a reader to guess which."""
        earlier_results(workspace, count=2)

        completed = run_launcher(workspace, script, workers=2, pages=2,
                                 env={"STUB_FILES_PER_WORKER": "1"})

        assert "Files collected by this run" in completed.stdout
        assert "all runs" in completed.stdout
        # 6 earlier files + this run's 2.
        assert "Total: 8" in completed.stdout


@pytest.mark.parametrize("script", ALL_LAUNCHERS)
class TestACapAndATruncatedRunEndDifferently:
    """The other half of the same defect, through the real launchers.

    Both runs cover fewer pages than were requested. One reached the end of
    GEO's corpus and is a success; the other stopped short of pages that exist
    and must leave no whole-range evidence at all.
    """

    def manifests(self, workspace):
        run = workspace["output"] / "runs" / "test-run" / "crawl_manifest.json"
        pointer = workspace["output"] / "crawl_manifest.json"
        return (json.loads(run.read_text()) if run.exists() else None,
                json.loads(pointer.read_text()) if pointer.exists() else None)

    def test_a_run_capped_by_a_short_corpus_succeeds(self, workspace, script):
        """4 pages asked for, GEO holds 2, both crawled: all the work there is."""
        completed = run_launcher(workspace, script, workers=2, pages=4,
                                 total_pages=2)

        assert completed.returncode == 0, completed.stdout
        merged, pointer = self.manifests(workspace)
        assert merged["requested_pages"] == {"start": 1, "end": 4}
        assert merged["completed_pages"] == {"start": 1, "end": 2}
        assert merged["resolved_end_page"] == 2
        assert merged["coverage"] == "capped"
        assert merged["corpus_pages"] == 2
        assert pointer["run_id"] == "test-run"

    def test_a_run_that_stopped_short_of_the_corpus_leaves_no_manifest(
        self, workspace, script
    ):
        """The reported case: pages 1-4 planned, GEO holds 4, covered 1-2."""
        completed = run_launcher(workspace, script, workers=1, pages=4,
                                 total_pages=4,
                                 env={"STUB_COMPLETED_END": "2"})

        assert completed.returncode == 1, completed.stdout
        assert self.manifests(workspace) == (None, None)
        assert "truncated run" in completed.stderr


class _Link:
    """One <a> on GEO's matrix listing."""

    def __init__(self, text):
        self.text = text
        self.clicked = False

    def click(self):
        self.clicked = True


class _ListingDriver:
    """The FTP matrix listing page, and nothing else: what download_smtx drives."""

    def __init__(self, link_names):
        self.links = [_Link(name) for name in link_names]
        self.url = None

    def get(self, url):
        self.url = url

    def find_element(self, by, value):
        if self.links:
            return self.links[0]
        raise NoSuchElementException("empty listing")

    def find_elements(self, by, value):
        return list(self.links)

    # -- what setup_driver does to a fresh browser --------------------
    def execute_cdp_cmd(self, cmd, params):
        return None

    def maximize_window(self):
        return None

    def set_page_load_timeout(self, seconds):
        return None

    def quit(self):
        return None


class _RunSelectorDriver(_ListingDriver):
    """The SRA Run Selector page: the link that leads there and its two
    download buttons, which is all download_sra_data touches."""

    BUTTONS = ("//button[@id='t-acclist-all']", "//button[@id='t-rit-all']")

    def __init__(self):
        super().__init__([])
        self.url = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE1"
        self.buttons = {xpath: _Button() for xpath in self.BUTTONS}

    @property
    def current_url(self):
        return self.url

    def find_element(self, by, value):
        if value in self.buttons:
            return self.buttons[value]
        raise NoSuchElementException(value)

    def find_elements(self, by, value):
        return [_Link("SRA Run Selector")] if value == "SRA Run Selector" else []

    def execute_script(self, script, *args):
        return None


class _Button(_Link):
    def __init__(self):
        super().__init__("download")

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True


def make_crawler(tmp_path, monkeypatch, driver):
    """A real SimpleCrawler with a stand-in browser.

    Only `webdriver.Chrome` is replaced: a Service is built from a path that
    does not exist, which selenium is happy to hold until something asks it to
    launch, and nothing here ever does.
    """
    monkeypatch.setenv("CHROME_BINARY_PATH", "/stand-in/google-chrome-stable")
    monkeypatch.setenv("CHROME_DRIVER_PATH", "/stand-in/chromedriver")
    monkeypatch.setattr(gc, "CHROME_PROFILE_ROOT", tmp_path / "chrome-profiles")
    monkeypatch.setattr(gc.webdriver, "Chrome", lambda **kwargs: driver)
    monkeypatch.setattr(gc.time, "sleep", lambda seconds: None)
    return gc.SimpleCrawler(output_dir=str(tmp_path / "out"))


class TestTheCrawlerCountsWhatItPublishes:
    """The producer half: a worker can only report files it recorded writing.

    These drive the real download paths - the browser is the only stand-in -
    because a count assembled anywhere else would be a count of the directory
    again, and the directory belongs to every run that ever wrote there.
    """

    def test_a_downloaded_matrix_is_counted_once_it_is_a_result(
        self, tmp_path, monkeypatch
    ):
        driver = _ListingDriver(["GSE1_series_matrix.txt.gz"])
        crawler = make_crawler(tmp_path, monkeypatch, driver)

        def fake_wait(pattern, timeout=20):
            """The browser, having finished writing into the download area."""
            path = crawler.download_dir / "GSE1_series_matrix.txt.gz"
            path.write_text("matrix")
            return path

        monkeypatch.setattr(crawler, "wait_for_download", fake_wait)

        assert crawler.download_smtx("GSE1") is True
        assert crawler.collected_files == {"SMTX": 1, "SRR": 0, "META": 0}
        assert (crawler.output_dir / "SMTX" / "GSE1_series_matrix.txt.gz").exists()

    def test_a_download_that_never_arrived_is_counted_as_nothing(
        self, tmp_path, monkeypatch
    ):
        driver = _ListingDriver(["GSE1_series_matrix.txt.gz"])
        crawler = make_crawler(tmp_path, monkeypatch, driver)
        monkeypatch.setattr(crawler, "wait_for_download",
                            lambda pattern, timeout=20: None)

        assert crawler.download_smtx("GSE1") is False
        assert crawler.collected_files["SMTX"] == 0

    def test_earlier_results_in_the_directory_are_not_this_crawls(
        self, tmp_path, monkeypatch
    ):
        driver = _ListingDriver(["GSE2_series_matrix.txt.gz"])
        crawler = make_crawler(tmp_path, monkeypatch, driver)
        earlier = crawler.output_dir / "SMTX" / "GSE1_series_matrix.txt.gz"
        earlier.write_text("from an earlier run")

        monkeypatch.setattr(crawler, "wait_for_download",
                            lambda pattern, timeout=20: None)
        crawler.download_smtx("GSE2")

        assert crawler.collected_files["SMTX"] == 0
        assert earlier.read_text() == "from an earlier run"

    def test_the_accession_list_and_the_metadata_land_in_their_own_buckets(
        self, tmp_path, monkeypatch
    ):
        driver = _RunSelectorDriver()
        crawler = make_crawler(tmp_path, monkeypatch, driver)

        def fake_wait(pattern, timeout=20):
            name = "SRR_Acc_List.txt" if "SRR_Acc_List" in pattern \
                else "SraRunTable.txt"
            path = crawler.download_dir / name
            path.write_text("downloaded")
            return path

        monkeypatch.setattr(crawler, "wait_for_download", fake_wait)

        crawler.download_sra_data("GSE1")

        assert crawler.collected_files == {"SMTX": 0, "SRR": 1, "META": 1}
        assert (crawler.output_dir / "SRR" / "GSE1.txt").exists()
        assert (crawler.output_dir / "META" / "GSE1_meta.txt").exists()

    def test_the_manifest_carries_the_tally_with_a_total(self, tmp_path):
        gc.write_completion_manifest(
            tmp_path, requested_start=1, requested_end=2, completed_start=1,
            completed_end=2, items_per_page=500, processed=1, failed=0,
            collected_files={"SMTX": 2, "SRR": 1, "META": 1},
        )

        manifest = json.loads(
            (tmp_path / gc.COMPLETION_MANIFEST_FILE).read_text()
        )
        assert manifest["collected_files"] == {
            "SMTX": 2, "SRR": 1, "META": 1, "total": 4
        }

    def test_no_tally_at_all_is_not_a_tally_of_zero(self, tmp_path):
        """Kept apart deliberately: "nothing recorded this" and "this recorded
        nothing" are different claims, even though both fail to prove a file."""
        gc.write_completion_manifest(
            tmp_path, requested_start=1, requested_end=2, completed_start=1,
            completed_end=2, items_per_page=500, processed=1, failed=0,
        )

        manifest = json.loads(
            (tmp_path / gc.COMPLETION_MANIFEST_FILE).read_text()
        )
        assert manifest["collected_files"] is None
