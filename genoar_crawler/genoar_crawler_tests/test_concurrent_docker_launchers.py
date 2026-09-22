"""Two real Docker launchers, at the same time, on one checkout.

Every other launcher test in this directory runs against a stub `docker` and
stops at `GENOAR_SKIP_MONITOR`. That proves what the scripts *say* to the
daemon, which is most of what matters and none of what a boundary is. The
isolation test in test_container_scope.py, in particular, runs alpha and then
beta - sequentially, against a stub, with the monitors skipped - so it cannot
show that a live run survives another run starting, that a monitor reports its
own containers and not the host's, or that two runs' results stay apart while
both are being written.

This runs both launchers against the real daemon, started at one instant, with
their monitoring loops active, long enough that all four containers are up
together, and then asks:

  * did the two overlap at all (else the rest proves nothing);
  * did every container of both runs exit 0 - a run swept by the other shows
    137 or is simply gone;
  * did each monitor report only its own containers;
  * are the two runs' directories, plans, worker directories and manifests
    entirely separate;
  * did each worker get its own private state inside the container.

It is skipped unless GENOAR_DOCKER_E2E=1, because it builds an image and takes
about a minute:

    GENOAR_DOCKER_E2E=1 python3 -m pytest genoar_crawler/genoar_crawler_tests/\
test_concurrent_docker_launchers.py -q

The image it builds is a stand-in for the crawler: the same command line, the
same identity variables, the same per-worker layout under /data, and a sleep
where the crawl would be. The crawler's real image needs Chrome and a live GEO,
and neither says anything about whether two launchers can coexist - which is
what is being tested. Everything above the image is the product: the launcher,
its container naming and labelling, its monitor, its aggregation step and its
exit code.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

CRAWLER_DIR = Path(__file__).resolve().parent.parent

LAUNCHER = "run_docker_parallel.sh"

# The launcher builds `genoar:<arch>` from `Dockerfile.<arch>` when the tag is
# absent, so the stand-in is a Dockerfile in the workspace and the product
# builds it. Nothing here reaches into the launcher to redirect it.
BASE_IMAGE = "alpine:latest"

DOCKERFILE = f"""FROM {BASE_IMAGE}
COPY crawler.sh /usr/local/bin/crawler.sh
ENTRYPOINT ["/bin/sh", "/usr/local/bin/crawler.sh"]
"""

# A stand-in for genoar_crawler.py: the three command lines the launcher uses,
# the identity it hands over, and the per-worker layout under /data.
CRAWLER = r"""#!/bin/sh
set -e
OUT=/data

case "$1" in
    --report-pages)
        echo 'GENOAR_PAGE_REPORT {"total_results": 2000, "items_per_page": 500, "total_pages": 4}'
        exit 0
        ;;
    --aggregate)
        RUN=""
        while [ $# -gt 0 ]; do
            case "$1" in
                --run-id) RUN=$2; shift 2 ;;
                -o) OUT=$2; shift 2 ;;
                *) shift ;;
            esac
        done
        RUN_DIR="$OUT/runs/$RUN"
        WORKERS=$(find "$RUN_DIR/workers" -name crawl_manifest.json -type f | wc -l | tr -d ' ')
        FILES=$(find "$RUN_DIR/workers" -name 'collected-*' -type f | wc -l | tr -d ' ')
        cat > "$RUN_DIR/crawl_manifest.json" <<EOF
{"run_id": "$RUN", "status": "completed", "source": "aggregated", "workers": $WORKERS}
EOF
        cp "$RUN_DIR/crawl_manifest.json" "$OUT/crawl_manifest.json"
        printf 'GENOAR_RUN_REPORT {"collected_smtx": %s, "collected_srr": 0, "collected_meta": 0, "collected_total": %s}\n' "$FILES" "$FILES"
        exit 0
        ;;
esac

START=$1
END=$2
: "${GENOAR_RUN_ID:?a worker needs a run}"
: "${GENOAR_WORKER_ID:?a worker needs an id}"

WORK="$OUT/runs/$GENOAR_RUN_ID/workers/worker-$GENOAR_WORKER_ID"
mkdir -p "$WORK/chrome-profile" "$WORK/downloads"
echo "run=$GENOAR_RUN_ID worker=$GENOAR_WORKER_ID pid=$$ pages=$START-$END" > "$WORK/identity"
date -u +%s > "$WORK/started_at"
: > "$WORK/collected-$GENOAR_RUN_ID-$GENOAR_WORKER_ID"

sleep "${GENOAR_STUB_SECONDS:-20}"

cat > "$WORK/crawl_manifest.json" <<EOF
{"status": "completed", "source": "worker", "run_id": "$GENOAR_RUN_ID",
 "worker_id": "$GENOAR_WORKER_ID",
 "requested_pages": {"start": $START, "end": $END},
 "completed_pages": {"start": $START, "end": $END}}
EOF
date -u +%s > "$WORK/finished_at"
"""

RACER = r"""#!/bin/bash
while [ ! -e "$RACE_BARRIER" ]; do :; done
exec bash "$RACE_SCRIPT" "$@"
"""

RUNS = ["alpha", "beta"]
WORKERS = 2
PAGES = 4


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          timeout=180, **kwargs)


def host_arch():
    machine = os.uname().machine
    return "arm64" if machine in ("arm64", "aarch64") else "amd64"


IMAGE = f"genoar:{host_arch()}"

pytestmark = [
    pytest.mark.skipif(os.environ.get("GENOAR_DOCKER_E2E") != "1",
                       reason="set GENOAR_DOCKER_E2E=1 to run against a daemon"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="no docker"),
]


@pytest.fixture(scope="module")
def daemon():
    if docker("info").returncode != 0:
        pytest.skip("the Docker daemon is not running")
    if docker("image", "inspect", IMAGE).returncode == 0:
        pytest.skip(f"{IMAGE} already exists here; refusing to replace it")
    if docker("image", "inspect", BASE_IMAGE).returncode != 0:
        pytest.skip(f"{BASE_IMAGE} is not cached and this test does not pull")
    yield
    for run in RUNS:
        stale = docker("ps", "-aq", "--filter", f"label=genoar.run={run}").stdout.split()
        if stale:
            docker("rm", "-f", *stale)
    docker("image", "rm", IMAGE)


@pytest.fixture(scope="module")
def arena(tmp_path_factory, daemon):
    root = tmp_path_factory.mktemp("concurrent")
    work = root / "work"
    work.mkdir()
    shutil.copy(CRAWLER_DIR / LAUNCHER, work / LAUNCHER)
    (work / "crawler.sh").write_text(CRAWLER)
    for arch in ("amd64", "arm64"):
        (work / f"Dockerfile.{arch}").write_text(DOCKERFILE)
    racer = root / "racer.sh"
    racer.write_text(RACER)
    racer.chmod(0o755)
    return {"root": root, "dir": work, "racer": racer, "barrier": root / "GO"}


@pytest.fixture(scope="module")
def both_runs(arena):
    """Start both launchers at one instant and watch until both have finished.

    Returns each run's exit code and output, plus the samples taken of the
    daemon while they ran.
    """
    # Build once here rather than letting the two racing launchers both try:
    # the point of this test is the run boundary, not Docker's build lock.
    built = docker("build", "--platform", f"linux/{host_arch()}",
                   "-t", IMAGE, "-f", f"Dockerfile.{host_arch()}", ".",
                   cwd=str(arena["dir"]))
    assert built.returncode == 0, built.stderr

    environment = dict(os.environ)
    environment.update({
        "RACE_BARRIER": str(arena["barrier"]),
        "RACE_SCRIPT": str(arena["dir"] / LAUNCHER),
        "GENOAR_TOTAL_PAGES": str(PAGES),
        "GENOAR_WORKER_START_DELAY": "1",
        "GENOAR_MONITOR_INTERVAL": "3",
        "GENOAR_STUB_SECONDS": "20",
    })

    processes = {}
    for run in RUNS:
        processes[run] = subprocess.Popen(
            ["bash", str(arena["racer"]), str(WORKERS), str(PAGES)],
            cwd=str(arena["dir"]), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={**environment, "GENOAR_RUN_ID": run},
        )
    time.sleep(0.4)
    arena["barrier"].write_text("go\n")

    # What the daemon looked like while they were both alive. This is the only
    # evidence that they overlapped at all, and without it nothing below means
    # anything.
    samples = []
    deadline = time.time() + 240
    while time.time() < deadline:
        running = {run: docker("ps", "-q", "--filter",
                               f"label=genoar.run={run}").stdout.split()
                   for run in RUNS}
        samples.append(running)
        if all(process.poll() is not None for process in processes.values()):
            break
        time.sleep(0.5)

    outcomes = {}
    for run, process in processes.items():
        output, _ = process.communicate(timeout=120)
        outcomes[run] = (process.returncode, output)

    return {"outcomes": outcomes, "samples": samples,
            "output_dir": arena["dir"] / "crawl_output"}


class TestTheRunsReallyOverlapped:
    def test_all_four_workers_were_up_at_the_same_time(self, both_runs):
        """Not alpha-then-beta. If this fails, the isolation the rest of this
        file asserts was never actually put under any pressure."""
        together = [s for s in both_runs["samples"]
                    if all(len(s[run]) > 0 for run in RUNS)]
        assert together, "the two runs never had containers up at the same time"

        widest = max(sum(len(s[run]) for run in RUNS) for s in together)
        assert widest == len(RUNS) * WORKERS, (
            f"at most {widest} containers were up together, expected "
            f"{len(RUNS) * WORKERS}"
        )


class TestNeitherRunTouchedTheOther:
    def test_both_launchers_succeeded(self, both_runs):
        for run, (code, output) in both_runs["outcomes"].items():
            assert code == 0, f"{run} exited {code}\n{output}"

    def test_every_container_exited_zero(self, both_runs):
        """A container another run stopped comes back 137 or 143, and one
        another run removed comes back missing. Both were what the host-wide
        `docker ps --filter name=genoar-worker | xargs docker stop` did."""
        for run in RUNS:
            ids = docker("ps", "-aq", "--filter",
                         f"label=genoar.run={run}").stdout.split()
            assert len(ids) == WORKERS, f"{run} has {len(ids)} containers"
            for container in ids:
                state = docker("inspect", "-f",
                               "{{.State.Status}} {{.State.ExitCode}}",
                               container).stdout.strip()
                assert state == "exited 0", f"{run}/{container}: {state}"

    def test_neither_monitor_ever_named_the_other_run(self, both_runs):
        """Two monitors sharing a view of the host is how a run comes to report
        somebody else's worker as its own - and then to wait for it."""
        for run in RUNS:
            other = [r for r in RUNS if r != run][0]
            _, output = both_runs["outcomes"][run]
            assert f"genoar-worker-{other}-" not in output
            assert f"genoar.run={other}" not in output

    def test_each_monitor_counted_only_its_own_workers(self, both_runs):
        for run in RUNS:
            _, output = both_runs["outcomes"][run]
            counts = re.findall(r"Containers running: (\d+)/(\d+)", output)
            assert counts, output
            for running, total in counts:
                assert int(total) == WORKERS
                assert int(running) <= WORKERS


class TestTheTwoRunsWroteToDifferentPlaces:
    def test_each_run_has_its_own_plan_and_manifest(self, both_runs):
        for run in RUNS:
            run_dir = both_runs["output_dir"] / "runs" / run
            plan = json.loads((run_dir / "run.json").read_text())
            assert plan["run_id"] == run
            assert plan["workers"] == WORKERS
            merged = json.loads((run_dir / "crawl_manifest.json").read_text())
            assert merged["run_id"] == run
            assert merged["workers"] == WORKERS

    def test_no_worker_directory_is_shared(self, both_runs):
        seen = {}
        for run in RUNS:
            workers = both_runs["output_dir"] / "runs" / run / "workers"
            names = sorted(p.name for p in workers.iterdir())
            assert names == [f"worker-{n}" for n in range(1, WORKERS + 1)]
            for directory in workers.iterdir():
                identity = (directory / "identity").read_text()
                assert f"run={run}" in identity
                seen.setdefault(str(directory.resolve()), []).append(run)
        assert all(len(runs) == 1 for runs in seen.values()), seen

    def test_each_worker_had_its_own_state_inside_the_container(self, both_runs):
        """The defect this layout was built for: every container is PID 1, so
        an identity derived from the process id gave all four the same
        /data/.chrome/worker-1 and the same checkpoint."""
        profiles = set()
        for run in RUNS:
            workers = both_runs["output_dir"] / "runs" / run / "workers"
            for directory in workers.iterdir():
                assert (directory / "chrome-profile").is_dir()
                assert (directory / "downloads").is_dir()
                profiles.add(str((directory / "chrome-profile").resolve()))
        assert len(profiles) == len(RUNS) * WORKERS

    def test_the_workers_of_both_runs_were_alive_together(self, both_runs):
        """From the containers' own clocks, not from the harness's polling."""
        windows = []
        for run in RUNS:
            workers = both_runs["output_dir"] / "runs" / run / "workers"
            starts = [int((d / "started_at").read_text())
                      for d in workers.iterdir()]
            ends = [int((d / "finished_at").read_text())
                    for d in workers.iterdir()]
            windows.append((min(starts), max(ends)))

        (start_a, end_a), (start_b, end_b) = windows
        assert min(end_a, end_b) >= max(start_a, start_b), windows

    def test_the_top_level_pointer_belongs_to_one_of_them_and_says_which(
        self, both_runs
    ):
        """crawl_output/crawl_manifest.json is one file for the whole output
        directory - the pointer at the newest completed run. Two concurrent
        runs both write it, and the last one wins; what must never happen is
        that it names a run it does not describe."""
        pointer = json.loads(
            (both_runs["output_dir"] / "crawl_manifest.json").read_text()
        )
        assert pointer["run_id"] in RUNS
        owner = both_runs["output_dir"] / "runs" / pointer["run_id"]
        assert pointer == json.loads((owner / "crawl_manifest.json").read_text())
