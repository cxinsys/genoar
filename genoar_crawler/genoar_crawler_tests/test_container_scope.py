"""Which containers a run is allowed to name, select, stop and remove.

The defect this pins is the one that actually killed a 90-minute rehearsal.
Previous rounds gave every worker its own run directory, checkpoint, downloads
and Chrome profile - but the containers those workers ran in stayed global:

  * `docker-compose.yml` pinned `container_name: genoar-pipeline` and a fixed
    network `name:`. A container name is host-global and is *not* namespaced by
    the Compose project, so a second `docker compose up` anywhere on the host
    recreated the first run's container. The crawl died and left `exit 137`.
  * `run_docker_parallel.sh` began every run with

        docker ps -aq --filter name=genoar-worker | docker stop / docker rm

    `--filter name=` is a substring match with no anchored form, so that swept
    up the live workers of anybody else's run.
  * `run_crawl.sh` did the same thing under a second, divergent prefix,
    `geo-worker`.
  * The hints the scripts printed ("Stop all workers", `docker ps --filter
    name=genoar-worker`) taught the same host-wide habit to every user.

So the rule asserted here is: a run may touch a container only when that
container carries *this run's* label. Names are run-scoped too, but only so two
concurrent runs do not collide on `--name`; ownership is decided by the label,
because a name filter cannot express ownership.

The scripts run against a stub `docker`, so nothing here reaches a daemon. The
stub deliberately answers a *name* filter with another run's containers: if a
script ever goes back to selecting by name, those ids turn up in its `stop`/`rm`
arguments and these tests fail.
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
REPO_ROOT = CRAWLER_DIR.parent

# A stub `docker` that records every invocation and answers `ps` from the
# environment. `ps -q` is the running set, `ps -aq` the whole set, and only a
# `label=` filter naming this run selects anything - except a `name=` filter,
# which returns the *foreign* containers, standing in for the blunt match that
# swept up other runs.
STUB_DOCKER = r"""#!/bin/sh
printf '%s\n' "$*" >> "$STUB_LOG"
cmd=$1
shift
case "$cmd" in
    info) exit 0 ;;
    images) echo "sha256:stub" ;;
    inspect) echo "false 0" ;;
    ps)
        all=0
        selector=""
        prev=""
        for arg in "$@"; do
            case "$arg" in
                -a|-aq|-qa|--all) all=1 ;;
            esac
            if [ "$prev" = "--filter" ]; then
                case "$arg" in
                    label=*) selector=${arg#label=} ;;
                    name=*)  selector="BY-NAME" ;;
                esac
            fi
            prev=$arg
        done
        if [ "$selector" = "BY-NAME" ]; then
            printf '%s\n' ${STUB_FOREIGN_CONTAINERS:-}
            exit 0
        fi
        [ -n "$selector" ] || exit 0
        [ "$selector" = "${STUB_THIS_RUN_LABEL:-}" ] || exit 0
        if [ "$all" = 1 ]; then
            printf '%s\n' ${STUB_ALL_CONTAINERS:-}
        else
            printf '%s\n' ${STUB_RUNNING_CONTAINERS:-}
        fi
        ;;
    run)
        case "$*" in
            *--report-pages*)
                printf 'GENOAR_PAGE_REPORT {"total_results": 1, "items_per_page": 500, "total_pages": %s}\n' "${STUB_TOTAL_PAGES:-47}"
                ;;
            *) echo "stub-container-id" ;;
        esac
        ;;
    *) : ;;
esac
exit 0
"""

DOCKER_LAUNCHERS = ["run_crawl.sh", "run_docker_parallel.sh"]


@pytest.fixture
def workspace(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    for name in ("run_crawl.sh", "run_parallel_crawl.sh", "run_docker_parallel.sh",
                 "Dockerfile.amd64", "Dockerfile.arm64"):
        shutil.copy(CRAWLER_DIR / name, work / name)

    binaries = tmp_path / "bin"
    binaries.mkdir()
    stub = binaries / "docker"
    stub.write_text(STUB_DOCKER)
    stub.chmod(0o755)

    return {"dir": work, "bin": binaries, "log": tmp_path / "stub.log"}


def launch(workspace, script, run_id="test-run", workers=2, pages=4, env=None):
    """Start one launcher through its worker-launch phase and stop there.

    GENOAR_SKIP_MONITOR is the same hook the other launcher tests use: every
    one of these scripts ends in a loop that only exits when its containers do.
    """
    environment = dict(os.environ)
    environment.update({
        "PATH": f"{workspace['bin']}:{environment['PATH']}",
        "STUB_LOG": str(workspace["log"]),
        "GENOAR_SKIP_MONITOR": "1",
        "GENOAR_WORKER_START_DELAY": "0",
        "GENOAR_RUN_ID": run_id,
        # Skip the corpus probe: it is a container of its own and says nothing
        # about scoping.
        "GENOAR_TOTAL_PAGES": "47",
    })
    environment.update(env or {})

    args = [] if script == "run_crawl.sh" else [str(workers), str(pages)]
    stdin = f"{workers}\n{pages}\ny\n" if script == "run_crawl.sh" else ""

    completed = subprocess.run(
        ["bash", script, *args],
        cwd=str(workspace["dir"]), input=stdin, text=True,
        capture_output=True, env=environment, timeout=120,
    )
    log = workspace["log"]
    return completed, (log.read_text().splitlines() if log.exists() else [])


def lines_starting(lines, verb):
    return [line for line in lines if line.split()[:1] == [verb]]


def container_names(lines):
    """Every value this run passed to `docker run --name`."""
    names = []
    for line in lines:
        match = re.search(r"--name (\S+)", line)
        if match:
            names.append(match.group(1))
    return names


def run_labels(lines):
    """Every genoar.run label value this run put on a container."""
    return {match.group(1)
            for line in lines
            for match in re.finditer(r"--label genoar\.run=(\S+)", line)}


class TestNoLaunchTouchesAnotherRun:
    """The reproduction, as a test: another run is live, this one starts."""

    FOREIGN = "live-worker-of-another-run"

    @pytest.mark.parametrize("script", DOCKER_LAUNCHERS)
    def test_a_live_foreign_worker_is_neither_stopped_nor_removed(
        self, workspace, script
    ):
        completed, lines = launch(
            workspace, script, run_id="mine",
            env={"STUB_FOREIGN_CONTAINERS": self.FOREIGN,
                 "STUB_THIS_RUN_LABEL": "genoar.run=nobody"},
        )

        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert lines_starting(lines, "stop") == []
        assert lines_starting(lines, "rm") == []
        assert self.FOREIGN not in "\n".join(lines)

    @pytest.mark.parametrize("script", DOCKER_LAUNCHERS)
    def test_no_container_is_ever_selected_by_name(self, workspace, script):
        """`--filter name=` is a substring match. There is no anchored form of
        it, so it can never be narrowed to one run - which is why the fix is a
        label and not a longer name."""
        _, lines = launch(workspace, script, run_id="mine")

        offenders = [line for line in lines if "--filter name=" in line]
        assert offenders == []

    @pytest.mark.parametrize("script", DOCKER_LAUNCHERS)
    def test_every_daemon_query_is_scoped_to_this_run(self, workspace, script):
        _, lines = launch(workspace, script, run_id="mine")

        for line in lines_starting(lines, "ps"):
            assert "--filter label=genoar.run=mine" in line, line


class TestNamesAndLabelsCarryTheRunId:
    @pytest.mark.parametrize("script", DOCKER_LAUNCHERS)
    def test_worker_containers_are_labelled_with_this_run(self, workspace, script):
        _, lines = launch(workspace, script, run_id="mine", workers=2, pages=4)

        assert run_labels(lines) == {"mine"}
        workers = [line for line in lines if "--label genoar.role=worker" in line]
        assert len(workers) == 2
        assert sorted(re.search(r"--label genoar\.worker=(\S+)", line).group(1)
                      for line in workers) == ["1", "2"]

    @pytest.mark.parametrize("script", DOCKER_LAUNCHERS)
    def test_two_runs_share_no_container_name(self, workspace, script):
        """Not a nicety: `docker run --name` fails outright on a name already
        taken, so without this the second concurrent run starts no workers."""
        _, first = launch(workspace, script, run_id="alpha", workers=2, pages=4)
        workspace["log"].unlink()
        _, second = launch(workspace, script, run_id="beta", workers=2, pages=4)

        alpha, beta = container_names(first), container_names(second)
        assert len(alpha) == 2 and len(beta) == 2
        assert set(alpha).isdisjoint(beta)
        assert all("alpha" in name for name in alpha)
        assert all("beta" in name for name in beta)

    @pytest.mark.parametrize("script", DOCKER_LAUNCHERS)
    def test_a_container_name_stays_one_path_safe_token(self, workspace, script):
        """Docker's own rule, and the harness in test_run_file_accounting.py
        keys a directory entry off this name."""
        _, lines = launch(workspace, script, run_id="a.run_id-1")

        for name in container_names(lines):
            assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name), name


class TestALeftoverRunIsNotSilentlyReused:
    """A crashed run of the same id left containers behind.

    Reusing them silently would fold that run's exit codes and worker
    directories into this run's completion evidence. Refusing is the whole
    decision; discarding them has to be asked for.
    """

    LEFTOVER = "crashed-worker-1"

    def refusing_env(self, run_id="mine", **extra):
        return {"STUB_THIS_RUN_LABEL": f"genoar.run={run_id}",
                "STUB_ALL_CONTAINERS": self.LEFTOVER, **extra}

    @pytest.mark.parametrize("script", DOCKER_LAUNCHERS)
    def test_a_stopped_container_of_this_run_refuses_the_start(
        self, workspace, script
    ):
        completed, lines = launch(workspace, script, run_id="mine",
                                  env=self.refusing_env())

        assert completed.returncode == 2, completed.stdout
        assert container_names(lines) == [], "no worker may start"
        assert lines_starting(lines, "rm") == [], "and nothing may be deleted"
        assert "GENOAR_RECLAIM_RUN=1" in completed.stdout

    @pytest.mark.parametrize("script", DOCKER_LAUNCHERS)
    def test_a_running_container_of_this_run_refuses_the_start(
        self, workspace, script
    ):
        completed, lines = launch(
            workspace, script, run_id="mine",
            env=self.refusing_env(STUB_RUNNING_CONTAINERS=self.LEFTOVER),
        )

        assert completed.returncode == 2, completed.stdout
        assert container_names(lines) == []
        assert lines_starting(lines, "stop") == []
        assert lines_starting(lines, "rm") == []

    @pytest.mark.parametrize("script", DOCKER_LAUNCHERS)
    def test_reclaiming_removes_exactly_those_containers_and_proceeds(
        self, workspace, script
    ):
        completed, lines = launch(
            workspace, script, run_id="mine",
            env=self.refusing_env(GENOAR_RECLAIM_RUN="1"),
        )

        assert completed.returncode == 0, completed.stdout + completed.stderr
        removed = lines_starting(lines, "rm")
        assert removed == [f"rm {self.LEFTOVER}"]
        assert len(container_names(lines)) == 2, "the run then starts normally"

    @pytest.mark.parametrize("script", DOCKER_LAUNCHERS)
    def test_reclaiming_still_refuses_a_run_that_is_live(self, workspace, script):
        """A crashed run's remains may be discarded. A running one's may not:
        that is the 90-minute crawl, killed by the next `docker compose up`."""
        completed, lines = launch(
            workspace, script, run_id="mine",
            env=self.refusing_env(STUB_RUNNING_CONTAINERS=self.LEFTOVER,
                                  GENOAR_RECLAIM_RUN="1"),
        )

        assert completed.returncode == 2, completed.stdout
        assert lines_starting(lines, "rm") == []
        assert lines_starting(lines, "stop") == []

    @pytest.mark.parametrize("script", DOCKER_LAUNCHERS)
    def test_the_refusal_leaves_the_previous_manifest_alone(self, workspace, script):
        """The refusal happens before anything on disk is written, so a run
        that never started cannot destroy the last run's completion evidence."""
        output = workspace["dir"] / "crawl_output"
        output.mkdir(parents=True)
        manifest = output / "crawl_manifest.json"
        manifest.write_text(json.dumps({"run_id": "earlier"}))

        completed, _ = launch(workspace, script, run_id="mine",
                              env=self.refusing_env())

        assert completed.returncode == 2
        assert json.loads(manifest.read_text())["run_id"] == "earlier"


class TestTheAdviceOnScreenIsScopedToo:
    """Hints are where the habit came from: `docker stop $(docker ps -q
    --filter name=genoar-worker)` is a host-wide command, printed by the tool
    itself, that a user would reasonably paste while another run was going."""

    @pytest.mark.parametrize("script", DOCKER_LAUNCHERS)
    def test_no_host_wide_command_is_printed(self, workspace, script):
        completed, _ = launch(workspace, script, run_id="mine")

        for forbidden in ("--filter name=genoar-worker",
                          "--filter name=geo-worker",
                          "docker logs genoar-pipeline",
                          "Stop all workers",
                          "Remove all workers"):
            assert forbidden not in completed.stdout

    @pytest.mark.parametrize("script", DOCKER_LAUNCHERS)
    def test_the_script_holds_no_name_filter_at_all(self, script):
        """Read from the source rather than from one run's output, because the
        worst offenders - "Stop all workers", "Remove all workers" - lived in
        the final summary, which only a completed run ever prints. A name
        filter anywhere in these files is either a sweep waiting to happen or
        advice that will become one in a user's shell history.

        Comment lines are exempt: both scripts describe the sweep they used to
        perform, and that description is the reason nobody puts it back."""
        text = (CRAWLER_DIR / script).read_text()
        offenders = [line for line in text.splitlines()
                     if "--filter name=" in line
                     and not line.lstrip().startswith("#")]
        assert offenders == []

    @pytest.mark.parametrize("script", DOCKER_LAUNCHERS)
    def test_the_label_and_a_real_container_name_are_printed(
        self, workspace, script
    ):
        completed, lines = launch(workspace, script, run_id="mine")

        assert "--filter label=genoar.run=mine" in completed.stdout
        # The names on screen must be the names that were actually used, not a
        # placeholder that no longer resolves.
        started = container_names(lines)
        assert any(name in completed.stdout for name in started), started


class TestComposeIsNotHostGlobal:
    """`container_name:` and a pinned network `name:` are the compose-side half
    of the same defect: both are host-global and neither is namespaced by the
    Compose project, so a second project recreated the first's container and
    would tear down the network underneath it."""

    @pytest.fixture
    def compose_text(self):
        return (REPO_ROOT / "docker-compose.yml").read_text()

    def test_no_service_pins_a_container_name(self, compose_text):
        keys = [line for line in compose_text.splitlines()
                if re.match(r"\s*container_name\s*:", line)]
        assert keys == []

    def test_the_network_is_not_pinned_to_a_fixed_name(self, compose_text):
        in_networks = False
        pinned = []
        for line in compose_text.splitlines():
            if re.match(r"^networks\s*:", line):
                in_networks = True
                continue
            if in_networks:
                if line and not line[0].isspace() and not line.lstrip().startswith("#"):
                    in_networks = False
                    continue
                if re.match(r"\s*name\s*:", line):
                    pinned.append(line)
        assert pinned == []

    def test_the_service_is_still_called_genoar(self, compose_text):
        """`--exit-code-from genoar` in the Makefile names the service, and the
        service name is what replaces the container name for `docker compose
        logs`. Removing container_name must not have moved it."""
        assert re.search(r"^  genoar\s*:", compose_text, re.MULTILINE)


class TestTheMakefileAddressesTheContainerByProject:
    @pytest.fixture
    def makefile(self):
        return (REPO_ROOT / "Makefile").read_text()

    def test_compose_is_always_invoked_with_a_project(self, makefile):
        """Without -p the project comes from the directory name, so two
        checkouts of different names silently share nothing and two of the same
        name silently share everything."""
        bare = [line for line in makefile.splitlines()
                if re.search(r"(?<!\$\()docker compose ", line)
                and "-p " not in line
                and not line.lstrip().startswith("@#")
                and not line.lstrip().startswith("#")
                and "@echo" not in line]
        assert bare == []

    def test_there_is_a_way_to_follow_the_log(self, makefile):
        """`docker logs genoar-pipeline` stops working when the fixed name
        goes, so the replacement has to be somewhere the user will find it."""
        assert re.search(r"^logs:", makefile, re.MULTILINE)
        assert "logs -f genoar" in makefile

    def test_a_second_run_can_be_named(self, makefile):
        assert "COMPOSE_PROJECT_NAME ?= genoar" in makefile
