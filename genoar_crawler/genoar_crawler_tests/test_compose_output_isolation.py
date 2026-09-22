"""Two Compose projects on one host write to two output trees.

The defect this pins. The previous round made the container name and the
network per-project, and documented the result:

    make run COMPOSE_PROJECT_NAME=second   # a second run, alongside the first

That is true of the containers and false of everything they produce.
`docker-compose.yml` bind-mounts `./crawl_output`, `./logs`,
`./first_pass_output` and `./workflow_output` from the checkout, unconditionally
and identically for every project name, so the two runs share:

  * `crawl_output/` - the crawl's data area *and* `crawl_output/runs/`, where
    every run's plan, worker directories and merged manifest live;
  * `crawl_output/crawl_manifest.json`, the top-level pointer at "the newest
    completed run", which each run deletes when it starts;
  * `first_pass_output/`, whose analysis tables are named by content and not by
    run, so the second project's tables silently replace the first's;
  * `logs/`.

Neither of them is at fault and neither can tell. The instruction as documented
was therefore an instruction to corrupt a run.

The decision recorded here: each Compose project gets its own output root.
Refusing concurrent runs from one checkout was the other honest option, but it
retracts a capability the project has already documented, and the isolation is
cheap - four bind mounts under one variable. The default project keeps the
historic paths exactly, because the crawler, the Snakefile, `make fetch-sra` and
the README all name `crawl_output/` in the checkout root; only a second,
deliberately named project moves.

What is asserted here:
  * the default project's mounts are byte-for-byte where they always were;
  * a second project's are somewhere else, all of them;
  * the two share no output directory at all, and nothing under one is under
    the other;
  * a project name that is not a plain path segment is refused rather than
    resolved into one;
  * the Makefile targets that read those directories read the same ones the run
    wrote to.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# The four the pipeline writes. `all_query_results` and `workflow` are inputs -
# shared on purpose, because a second project has no copy of them and would
# find an empty directory where the UMLS tables should be.
OUTPUT_MOUNTS = [
    "/app/crawl_output",
    "/app/first_pass_output",
    "/app/logs",
    "/app/workflow_output",
]

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="`docker compose config` renders the file under test",
)


def compose_mounts(project, output_root=None):
    """{container path: host path} for one project, as Compose resolves it."""
    environment = dict(os.environ)
    environment.pop("GENOAR_OUTPUT_ROOT", None)
    if output_root is not None:
        environment["GENOAR_OUTPUT_ROOT"] = output_root
    rendered = subprocess.run(
        ["docker", "compose", "-p", project, "config", "--format", "json"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        env=environment, timeout=120,
    )
    assert rendered.returncode == 0, rendered.stderr
    service = json.loads(rendered.stdout)["services"]["genoar"]
    return {v["target"]: v["source"] for v in service["volumes"]}


def make_run_command(**variables):
    """What `make run` would actually execute, without executing it."""
    completed = subprocess.run(
        ["make", "-n", "run", *[f"{k}={v}" for k, v in variables.items()]],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


class TestTheDefaultProjectIsWhereItAlwaysWas:
    """Everything else in the repository names these paths - the Snakefile,
    `make fetch-sra`, `make status`, the crawler's own default output directory,
    both READMEs. Moving them to buy isolation nobody asked for would be a
    migration, not a fix."""

    def test_the_mounts_are_the_checkout_root(self):
        mounts = compose_mounts("genoar")

        for target in OUTPUT_MOUNTS:
            assert mounts[target] == str(REPO_ROOT / Path(target).name), target

    def test_an_unset_output_root_changes_nothing(self):
        assert compose_mounts("genoar") == compose_mounts("genoar",
                                                          output_root=".")


class TestASecondProjectWritesSomewhereElse:
    def test_every_output_mount_moves(self):
        first = compose_mounts("genoar")
        second = compose_mounts("second", output_root="./projects/second")

        for target in OUTPUT_MOUNTS:
            assert first[target] != second[target], target
            assert "projects/second" in second[target], second[target]

    def test_the_two_trees_are_disjoint(self):
        """Not merely different strings: neither may contain the other, or the
        second project's `crawl_output/runs/` lands inside the first's."""
        first = compose_mounts("genoar")
        second = compose_mounts("second", output_root="./projects/second")

        for a in (first[t] for t in OUTPUT_MOUNTS):
            for b in (second[t] for t in OUTPUT_MOUNTS):
                assert not Path(a) == Path(b)
                assert Path(b) not in Path(a).parents
                assert Path(a) not in Path(b).parents

    def test_the_inputs_are_still_shared(self):
        """A second project has no copy of the UMLS tables. Isolating an input
        would move the run to an empty directory and call it a clean start."""
        first = compose_mounts("genoar")
        second = compose_mounts("second", output_root="./projects/second")

        assert first["/app/all_query_results"] == second["/app/all_query_results"]
        assert first["/app/workflow"] == second["/app/workflow"]


class TestMakeGivesEachProjectItsOwnRoot:
    """The compose file can express the isolation; `make run` is what has to
    supply it, because the documented instruction is a make command and names
    only the project."""

    def test_the_default_run_passes_the_checkout_root(self):
        assert re.search(r"GENOAR_OUTPUT_ROOT=\.\s", make_run_command())

    def test_a_named_project_passes_a_root_of_its_own(self):
        recipe = make_run_command(COMPOSE_PROJECT_NAME="second")

        assert "GENOAR_OUTPUT_ROOT=./projects/second" in recipe
        assert "-p second" in recipe

    def test_the_documented_command_is_enough_on_its_own(self):
        """`make run COMPOSE_PROJECT_NAME=second` and nothing else. If the
        isolation needs a second variable the user has to remember, the
        documented instruction is still untrue."""
        recipe = make_run_command(COMPOSE_PROJECT_NAME="second")
        default = make_run_command()

        roots = re.findall(r"GENOAR_OUTPUT_ROOT=(\S+)", recipe)
        assert roots and all(root != "." for root in roots), roots
        assert set(roots).isdisjoint(re.findall(r"GENOAR_OUTPUT_ROOT=(\S+)",
                                                default))

    def test_the_run_announces_where_its_results_will_be(self):
        recipe = make_run_command(COMPOSE_PROJECT_NAME="second")
        assert "./projects/second" in recipe

    @pytest.mark.parametrize("name", ["../escape", "a/b", "with space", ""])
    def test_a_project_name_that_is_not_one_path_segment_is_refused(self, name):
        """The name becomes a directory. `COMPOSE_PROJECT_NAME=../..` would
        resolve the output root onto the checkout's parent."""
        completed = subprocess.run(
            ["make", "-n", "run", f"COMPOSE_PROJECT_NAME={name}"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
        )
        assert completed.returncode != 0, completed.stdout


class TestTheReadingTargetsFollowTheRunTheyDescribe:
    """`make status` after `make run COMPOSE_PROJECT_NAME=second` must count
    the second project's files. Counting the first project's and calling them
    this run's is the same class of false evidence as counting an earlier run's
    downloads."""

    def _recipe(self, target, **variables):
        completed = subprocess.run(
            ["make", "-n", target,
             *[f"{k}={v}" for k, v in variables.items()]],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
        )
        assert completed.returncode == 0, completed.stderr
        return completed.stdout

    @pytest.mark.parametrize("target", ["status", "clean", "setup"])
    def test_the_target_names_the_projects_own_tree(self, target):
        recipe = self._recipe(target, COMPOSE_PROJECT_NAME="second")

        assert "projects/second/crawl_output" in recipe, recipe

    @pytest.mark.parametrize("target", ["status", "clean", "setup"])
    def test_the_default_target_still_names_the_historic_tree(self, target):
        recipe = self._recipe(target)

        assert "crawl_output" in recipe
        assert "projects/" not in recipe
