"""The shell launchers' gate on GENOAR_RUN_ID, held to the crawler's own rule.

The defect this pins: the three launchers validated the run id with

    case "$RUN_ID" in *[!A-Za-z0-9._-]*|'') ... reject ;; esac

which checks the character set and nothing else - no anchor on the first
character, no length bound, and no rejection of a value made entirely of legal
characters. `genoar_crawler.py` is stricter: IDENTITY_PATTERN is
`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`.

Order is what made the gap exploitable rather than merely untidy. The shell
builds `RUN_DIR="$OUTPUT_DIR/runs/$RUN_ID"` and `mkdir -p`s it before any Python
ever sees the id, so the laxer gate ran first and won:

    GENOAR_RUN_ID=..  ->  runs/.. == the output directory itself
                          crawl_output/run.json, crawl_output/workers/worker-1
    GENOAR_RUN_ID=.   ->  runs/.  == the run index itself
                          crawl_output/runs/run.json, crawl_output/runs/workers/

Both were reproduced against all three launchers before the fix.

So the rule asserted here is that the shell gate and the Python gate agree, on
a shared table of inputs, and that a refusal happens before anything is created.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CRAWLER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CRAWLER_DIR))
import genoar_crawler as gc  # noqa: E402

ALL_LAUNCHERS = ["run_crawl.sh", "run_parallel_crawl.sh", "run_docker_parallel.sh"]

# Answers `docker` and `python` well enough for a launcher to reach its worker
# launch. Nothing here needs a daemon, a browser or GEO: every case is decided
# before that, and the point of the assertions is what did *not* happen.
STUB = r"""#!/bin/sh
case "$1" in
    images) echo "sha256:stub" ;;
    info)   exit 0 ;;
    inspect) echo "false 0" ;;
    run)
        case "$*" in
            *--report-pages*) echo 'GENOAR_PAGE_REPORT {"total_results": 1, "items_per_page": 500, "total_pages": 47}' ;;
            *) echo "stub-container-id" ;;
        esac
        ;;
    *) : ;;
esac
exit 0
"""

# One table, applied to all three launchers and to the crawler module, because
# "the two gates agree" is the property - not "each is independently plausible".
#
# Every rejected value here is one `genoar_crawler.py` refuses. The first five
# are the ones the old shell gate let through.
REJECTED = [
    ("..", "the parent directory - resolves runs/.. onto the output directory"),
    (".", "the current directory - resolves runs/. onto the run index"),
    ("-rf", "leads with a dash, so it reads as an option wherever it is expanded"),
    (".hidden", "leads with a dot"),
    ("x" * 80, "80 characters, over the 64 the crawler allows"),
    ("has space", "inner whitespace"),
    ("a\tb", "an inner tab"),
    ("a\nb", "an inner newline - only the ends are stripped, so this stays"),
    ("../escape", "a path"),
    ("a/b", "a path separator"),
    ("../escape\n", "a path with a trailing newline; stripping reveals a path"),
    (".\n", "the current directory with a trailing newline"),
    ("_leading", "leads with an underscore"),
    ("é", "not ASCII, which the crawler's [A-Za-z0-9] does not accept"),
]

# Accepted, and what the launcher should end up calling the run.
ACCEPTED = [
    ("good-id-1", "good-id-1"),
    ("  good-id-1  ", "good-id-1"),          # .strip(), as the crawler does
    ("good-id-1\n", "good-id-1"),            # a trailing newline is whitespace
    ("a", "a"),                              # one character is a legal id
    ("x" * 64, "x" * 64),                    # exactly the limit
    ("A.b_c-1", "A.b_c-1"),
    ("latest", "latest"),                    # only the exact literal is reserved
]


@pytest.fixture
def workspace(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    for name in (*ALL_LAUNCHERS, "Dockerfile.amd64", "Dockerfile.arm64"):
        shutil.copy(CRAWLER_DIR / name, work / name)

    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("docker", "python"):
        stub = binaries / name
        stub.write_text(STUB)
        stub.chmod(0o755)

    return {"dir": work, "bin": binaries}


def launch(workspace, script, run_id):
    """Run one launcher with this GENOAR_RUN_ID, up to the worker launch."""
    environment = dict(os.environ)
    environment.update({
        "PATH": f"{workspace['bin']}:{environment['PATH']}",
        "GENOAR_SKIP_MONITOR": "1",
        "GENOAR_WORKER_START_DELAY": "0",
        "GENOAR_TOTAL_PAGES": "4",
        "GENOAR_RUN_ID": run_id,
    })
    args = [] if script == "run_crawl.sh" else ["2", "4"]
    stdin = "2\n4\ny\n" if script == "run_crawl.sh" else ""

    return subprocess.run(
        ["bash", script, *args],
        cwd=str(workspace["dir"]), input=stdin, text=True,
        capture_output=True, env=environment, timeout=120,
    )


def run_dirs(workspace):
    """The run directories a launcher created, by name."""
    runs = workspace["dir"] / "crawl_output" / "runs"
    return sorted(p.name for p in runs.iterdir()) if runs.is_dir() else []


def python_accepts(value):
    """What genoar_crawler.py makes of the same value.

    It strips, treats an empty result as "no id given", and otherwise holds the
    value to IDENTITY_PATTERN.
    """
    stripped = (value or "").strip()
    if not stripped:
        return True  # not provided; the crawler runs unassigned
    return bool(gc.IDENTITY_PATTERN.match(stripped))


@pytest.mark.parametrize("script", ALL_LAUNCHERS)
@pytest.mark.parametrize("value,why", REJECTED, ids=[repr(v) for v, _ in REJECTED])
class TestAnUnusableIdIsRefused:
    def test_it_exits_two(self, workspace, script, value, why):
        """Exit 2 - a bad argument, which is what these scripts and Stage 3
        both already use for a run id that cannot be a directory name."""
        completed = launch(workspace, script, value)
        assert completed.returncode == 2, (
            f"{script} accepted {value!r} ({why})\n{completed.stdout}"
        )

    def test_it_creates_nothing(self, workspace, script, value, why):
        """The refusal has to land before `mkdir -p`. That is the whole bug:
        the run directory was built and created before Python validated it, so
        a laxer shell gate had already touched the filesystem."""
        launch(workspace, script, value)
        assert not (workspace["dir"] / "crawl_output").exists(), (
            f"{script} created an output tree for {value!r} ({why})"
        )


@pytest.mark.parametrize("value,why", REJECTED, ids=[repr(v) for v, _ in REJECTED])
def test_the_crawler_refuses_every_rejected_value_too(value, why):
    """The invariant: the shell may not be laxer than the module whose
    directories it is creating. If this fails, the table is wrong, not the
    shell."""
    assert not python_accepts(value), (
        f"{value!r} ({why}) is accepted by genoar_crawler.py"
    )


@pytest.mark.parametrize("value,expected", ACCEPTED,
                         ids=[repr(v) for v, _ in ACCEPTED])
def test_the_crawler_accepts_every_accepted_value_too(value, expected):
    """And not stricter, either: a legitimate id refused by the shell is a
    launcher nobody can drive."""
    assert python_accepts(value)


@pytest.mark.parametrize("script", ALL_LAUNCHERS)
class TestTheReservedIdIsRefused:
    """Stage 3 keeps <results>/runs/LATEST as its newest-run pointer file and
    refuses that id. Nothing on this side writes such a pointer today, but both
    halves read the same GENOAR_RUN_ID, so a rule a user meets in one half has
    to hold in the other - otherwise GENOAR_RUN_ID=LATEST crawls happily here
    and then fails Stage 3 as a configuration error, half a pipeline in."""

    def test_latest_is_refused(self, workspace, script):
        completed = launch(workspace, script, "LATEST")
        assert completed.returncode == 2
        assert "reserved" in completed.stdout
        assert not (workspace["dir"] / "crawl_output").exists()

    def test_the_reservation_is_the_exact_literal(self, workspace, script):
        """Stage 3 compares `!= "LATEST"`, case-sensitively. Matching that
        exactly is the point; inventing a looser rule here would be a new
        disagreement rather than the end of one."""
        completed = launch(workspace, script, "latest")
        assert completed.returncode == 0, completed.stdout
        assert run_dirs(workspace) == ["latest"]


@pytest.mark.parametrize("script", ALL_LAUNCHERS)
@pytest.mark.parametrize("value,expected", ACCEPTED,
                         ids=[repr(v) for v, _ in ACCEPTED])
class TestAUsableIdStillWorks:
    def test_it_runs_and_names_the_run_directory(self, workspace, script,
                                                 value, expected):
        completed = launch(workspace, script, value)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert run_dirs(workspace) == [expected]


@pytest.mark.parametrize("script", ALL_LAUNCHERS)
class TestNoIdAtAllMintsOne:
    """`.strip() or None` in the crawler reads unset, empty and all-whitespace
    alike as "no id was given". The shell must not disagree in either
    direction: `${VAR:-default}` on its own lets empty through and mints an id,
    and a bare non-empty check refuses an all-whitespace value outright."""

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"],
                             ids=["empty", "spaces", "tab-newline"])
    def test_a_blank_id_is_replaced_by_a_generated_one(self, workspace, script,
                                                       value):
        completed = launch(workspace, script, value)

        assert completed.returncode == 0, completed.stdout + completed.stderr
        created = run_dirs(workspace)
        assert len(created) == 1, created
        assert created[0] not in ("", ".", "..")

    def test_the_generated_id_passes_the_gate_it_was_generated_for(
        self, workspace, script
    ):
        """A gate the tool's own default cannot pass is a gate nobody keeps."""
        launch(workspace, script, "")
        generated = run_dirs(workspace)[0]

        assert gc.IDENTITY_PATTERN.match(generated), generated
        assert python_accepts(generated)


@pytest.mark.parametrize("script", ALL_LAUNCHERS)
class TestANewlineSmugglesNothingPastTheGate:
    """A newline is the one whitespace character that can also be a shell line.

    The shell gate strips leading and trailing whitespace before it validates,
    exactly as `genoar_crawler.py` does, and that is deliberate: the two gates
    have to agree, and Python's `.strip()` removes a trailing newline. So a
    trailing newline on an otherwise-legal id is not itself a refusal - it is
    removed, and the id underneath is judged on its own. What must hold is that
    it cannot carry anything through the gate with it, and that no path this
    script builds ever contains one.

    Everything else about a newline is refused: an inner one is not stripped and
    is not in the character class, and a stripped value that turns out to be
    reserved or path-shaped is refused as reserved or path-shaped.
    """

    def test_a_trailing_newline_does_not_reach_a_directory_name(self, workspace,
                                                                script):
        completed = launch(workspace, script, "good-id-1\n")

        assert completed.returncode == 0, completed.stdout
        created = run_dirs(workspace)
        assert created == ["good-id-1"]
        assert all("\n" not in name for name in created)

    def test_a_trailing_newline_does_not_unreserve_LATEST(self, workspace,
                                                          script):
        """The reserved check has to come after the strip, or `LATEST\\n` walks
        past a comparison against the exact literal."""
        completed = launch(workspace, script, "LATEST\n")

        assert completed.returncode == 2, completed.stdout
        assert "reserved" in completed.stdout
        assert not (workspace["dir"] / "crawl_output").exists()

    def test_a_trailing_newline_does_not_hide_a_path(self, workspace, script):
        completed = launch(workspace, script, "../escape\n")

        assert completed.returncode == 2, completed.stdout
        assert not (workspace["dir"] / "crawl_output").exists()

    def test_an_inner_newline_is_refused(self, workspace, script):
        """Not stripped, not in [A-Za-z0-9._-], and it would be a second line
        anywhere the value were echoed."""
        completed = launch(workspace, script, "run\nrm -rf /")

        assert completed.returncode == 2, completed.stdout
        assert not (workspace["dir"] / "crawl_output").exists()

    def test_a_newline_only_id_mints_one_instead(self, workspace, script):
        """All-whitespace reads as "no id given" on both sides."""
        completed = launch(workspace, script, "\n")

        assert completed.returncode == 0, completed.stdout
        created = run_dirs(workspace)
        assert len(created) == 1 and created[0] not in ("", ".", "..")


class TestTheShellAndThePythonAgreeEverywhere:
    """One table, both gates. Written as its own test so a future change to
    either side that breaks the agreement fails here by name."""

    @pytest.mark.parametrize("script", ALL_LAUNCHERS)
    def test_the_launcher_matches_the_crawler_on_every_case(self, workspace,
                                                            script):
        disagreements = []
        for value, _ in REJECTED:
            if launch(workspace, script, value).returncode != 2:
                disagreements.append((value, "shell accepted, crawler refuses"))
            shutil.rmtree(workspace["dir"] / "crawl_output", ignore_errors=True)
        for value, _ in ACCEPTED:
            if launch(workspace, script, value).returncode != 0:
                disagreements.append((value, "shell refused, crawler accepts"))
            shutil.rmtree(workspace["dir"] / "crawl_output", ignore_errors=True)

        assert disagreements == []

    def test_the_pattern_this_file_asserts_is_the_crawlers_own(self):
        """If IDENTITY_PATTERN is ever relaxed, the shell must follow, and this
        says so out loud rather than leaving a copy to rot."""
        assert gc.IDENTITY_PATTERN.pattern == r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'

    @pytest.mark.parametrize("script", ALL_LAUNCHERS)
    def test_the_launcher_carries_that_pattern_verbatim(self, script):
        text = (CRAWLER_DIR / script).read_text()
        assert r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$' in text
