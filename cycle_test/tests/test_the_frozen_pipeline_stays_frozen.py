"""`pipeline/` is the one Stage 3 that has run on a real cluster.

One sample went through K-BDS on 2026-08-14 and came back complete: 12,630
cells, 44 minutes, `expected 1, completed 1, fresh 1`. What ran was the tree at
commit 45e7939, tagged `stage3-pilot-2026-08-14`, plus two fixes made by hand
on the cluster to get it
there — the `--create-bam` version branch (Cell Ranger 7.0.1 refuses the flag)
and finding the launcher under `bin/`. That combination is what `pipeline/`
holds, and it does not change again.

Everything found since lives in `pipeline_next/`, and there is no shortage of
it: checks that reported nothing, silent failures, retention, the corpus path.
All of it is better reasoned than what is frozen here. None of it has run on a
cluster. "This produced 12,630 cells" and "this is more correct" are different
kinds of claim, and only one of them is a measurement.

So this file exists to make changing the frozen tree a deliberate act rather
than a side effect. If a hash below fails, nothing is wrong with the test:
either revert the change or move it to `pipeline_next/`, which is what it is
for. Updating the hash means saying that the thing K-BDS verified is now
something else.

The hashes below have been recomputed once, for the public release. The only
edit was to comments: the note on each hand fix named a contributor no reader
outside the project can resolve, so it names the role instead. Every line of
code, and the reason each fix exists, is exactly what ran. The pilot commit is
still 45e7939 and the diff against it is still those two fixes and nothing
else, which the first test here checks. A further recomputation needs a reason
of the same kind.

The public repository starts from a fresh first commit and does not carry
45e7939 or the tag, so there the diff test skips and the hashes alone stand
guard. The internal repository, which has the full history, runs both.
"""

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FROZEN = REPO / "srr_pipeline_package" / "pipeline"
NEXT = REPO / "srr_pipeline_package" / "pipeline_next"

# The commit the K-BDS pilot ran from, before the two hand fixes were written
# into the product. Tagged `stage3-pilot-2026-08-14` so a published result can
# cite it; the short hash below is what this test compares against.
PILOT_COMMIT = "45e7939"

# sha256 of every file in the frozen tree.
EXPECTED = {
    "Snakefile_hs.smk":
        "2369eacc171569da84e4cd0a700f5cc2a582a13b159e51fc1519feeb35e69171",
    "check_counts.sh":
        "8cdc7ffbe5a8889793b681f19e4cd67a35a83571bbdc59cb4e9ad1dd712f9253",
    "fastq_dump_parallel.sh":
        "9dc33ca2f660c55f522defddb4542d930da373451e08a35af42e7654b92ca1a1",
    "fastq_gzip_parallel.sh":
        "f3151953f3a560ee87c0bbdaf053e07279c8478ec1ffef60814b0e4b3d48aa6e",
    "lib.sh":
        "8979492c864a3b9bc9543e6613673b760e78b394738f59f163d88d62b9139ac5",
    "make_directories.sh":
        "de00efdf160a0117362444f126865861a5af1af705bf14030f38467ee2f9e3d1",
    "move_success_dirs.sh":
        "4b346f8502889adbe519c5707dcf6c47a16a7b5574fa7f9f2a582e82254086e4",
    "parse_fastq_logs.py":
        "05ab3688cfb3fe7305313d1cd2fdaff8ee771546f7968f2d5ee129a6cff1bf93",
    "rename_fastq.sh":
        "f9307bbe723ab6bba71843e8a0003e1978dd1a3769e8992b4094e21bfa784fae",
    "rename_move_failed_fastqs.py":
        "ba5b81110af3c6b63e395e2208a804cc4df0abee1d77dc18d503c785302ed9fe",
    "run_docker_pipeline.sh":
        "f2f17e5f1b7edcedbc082f574eaf86b1bb5a30a5cd5b74dcb94524a01cce576e",
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args):
    return subprocess.run(("git",) + args, cwd=REPO, capture_output=True,
                          text=True)


class TestTheFrozenTreeIsTheOneThatRan:

    def test_it_differs_from_the_pilot_commit_only_by_the_two_hand_fixes(self):
        """Every changed line traces to issue 2 or issue 3 of the pilot.

        The pilot found five problems. Three were the environment or the pilot
        bundle; two were this pipeline, and those two are the whole of the
        difference. Anything else that has crept in belongs in pipeline_next.
        """
        done = _git("diff", "--numstat", PILOT_COMMIT, "--",
                    "srr_pipeline_package/pipeline/")
        if done.returncode != 0:
            pytest.skip(f"pilot commit {PILOT_COMMIT} not in this history "
                        "(a public clone) or not a git checkout")
        changed = {line.split("\t")[2] for line in done.stdout.splitlines()
                   if line.strip()}
        assert changed <= {
            "srr_pipeline_package/pipeline/Snakefile_hs.smk",
            "srr_pipeline_package/pipeline/run_docker_pipeline.sh",
        }, "a file the pilot never needed changed has been changed"

    def test_the_create_bam_branch_is_there(self):
        """Issue 2. Without it, Cell Ranger 7.0.1 refuses to start."""
        source = (FROZEN / "Snakefile_hs.smk").read_text()
        assert "def cellranger_bam_flag()" in source
        assert "{params.bam_flag}" in source
        assert '"--create-bam=true"\n' not in source.replace(
            'return " --create-bam=true"', "")

    def test_the_launcher_is_looked_for_under_bin(self):
        """Issue 3. Without it, the mount fails on `.env.json`."""
        source = (FROZEN / "run_docker_pipeline.sh").read_text()
        assert 'if [[ ! -x "$CELLRANGER_BIN" ]]; then' in source
        assert 'CELLRANGER_ROOT/bin/$(basename "$CELLRANGER_BIN")' in source

    def test_nothing_written_after_the_pilot_has_leaked_in(self):
        """The frozen tree carries no work from the rounds that followed."""
        source = (FROZEN / "run_docker_pipeline.sh").read_text()
        for stranger in ("RETAIN=", "release_input", "RELEASE_KEY",
                         "environment.json", "CELLRANGER_VERSION_EXPECTED",
                         "--keep-going", "cr_series"):
            assert stranger not in source, stranger


class TestTheHashesHold:
    """The hard guarantee. A change here has to be a decision."""

    @pytest.mark.parametrize("name", sorted(EXPECTED))
    def test_the_file_is_byte_for_byte_what_ran(self, name):
        assert _digest(FROZEN / name) == EXPECTED[name], (
            "%s is not the file K-BDS ran. Revert it, or move the change to "
            "pipeline_next/." % name)

    def test_every_frozen_file_is_pinned(self):
        on_disk = {p.name for p in FROZEN.iterdir() if p.is_file()}
        assert on_disk == set(EXPECTED), (
            "a file was added to or removed from the frozen tree")


class TestTheTwoTreesAreSeparate:

    def test_the_working_copy_exists_and_is_not_the_frozen_one(self):
        assert (NEXT / "run_docker_pipeline.sh").is_file()
        assert _digest(NEXT / "run_docker_pipeline.sh") != _digest(
            FROZEN / "run_docker_pipeline.sh")

    def test_neither_tree_reaches_into_the_other(self):
        """Each calls its own steps by absolute path, and only its own."""
        for name, tree, mine, theirs in (
                ("frozen", FROZEN, "/pipeline/", "/pipeline_next/"),
                ("working", NEXT, "/pipeline_next/", "/pipeline/")):
            for path in sorted(tree.iterdir()):
                if not path.is_file():
                    continue
                source = path.read_text(errors="ignore")
                assert theirs not in source, (
                    "%s tree's %s reaches into the other tree" % (name, path.name))

    def test_the_dispatcher_defaults_to_the_verified_one(self):
        entry = (REPO / "srr_pipeline_package" / "pipeline_entry.sh").read_text()
        assert 'TREE="/pipeline"' in entry
        assert 'TREE="/pipeline_next"' in entry

    def test_the_image_carries_both(self):
        dockerfile = (REPO / "srr_pipeline_package" / "docker"
                      / "Dockerfile").read_text()
        assert "COPY srr_pipeline_package/pipeline_next/ /pipeline_next/" in dockerfile
        assert 'ENTRYPOINT ["/pipeline_entry.sh"]' in dockerfile


class TestTheChoiceTravelsWithTheRun:
    """The choice has to reach the container, or the switch is a local one."""

    def _config(self, **over):
        import importlib.util
        import yaml
        hpc = REPO / "srr_pipeline_package" / "hpc"
        spec = importlib.util.spec_from_file_location(
            "prepare_hpc_handoff", hpc / "prepare_hpc_handoff.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cfg = {"cellranger_threads": 16, "cellranger_mem": 100}
        cfg.update(over)
        return yaml.safe_load(module.render_config_yaml(cfg))

    def test_the_generated_config_defaults_to_the_current_tree(self):
        assert self._config()["legacy_pipeline"] is False

    def test_a_site_can_ask_for_the_earlier_one(self):
        assert self._config(legacy_pipeline=True)["legacy_pipeline"] is True

    def test_the_older_spellings_still_work(self):
        """A config already written should not stop working."""
        assert self._config(safe_mode=True)["legacy_pipeline"] is True
        assert self._config(legacy_pipeline=False)["legacy_pipeline"] is False
        assert self._config(pipeline_mode="verified")["legacy_pipeline"] is True

    def test_the_word_false_means_false(self):
        """`bool("false")` is True, and this decides which code runs."""
        assert self._config(legacy_pipeline="false")["legacy_pipeline"] is False
        assert self._config(legacy_pipeline="true")["legacy_pipeline"] is True


class TestTheSwitchIsActuallyReachable:
    """A switch nothing goes through is not a switch.

    Both trees and the dispatcher went into a Docker stage the standard build
    does not build, and the cluster runner called the frozen pipeline by its
    own path -- so the setting did nothing on either path that matters, and
    every run got the verified tree whatever the config said.
    """

    def _dockerfile(self):
        return (REPO / "srr_pipeline_package" / "docker" / "Dockerfile").read_text()

    def test_the_target_the_makefile_builds_carries_both_trees(self):
        makefile = (REPO / "Makefile").read_text()
        target = re.search(r"--target (\w+) -t \$\(SRR_DOCKER_IMAGE\)", makefile)
        assert target, "the standard build target has moved"
        built = target.group(1)
        dockerfile = self._dockerfile()
        # Everything from that stage's FROM to the next one.
        start = dockerfile.index("AS %s\n" % built)
        nxt = dockerfile.find("\nFROM ", start)
        stage = dockerfile[start:nxt if nxt != -1 else len(dockerfile)]
        assert "COPY srr_pipeline_package/pipeline_next/ /pipeline_next/" in stage
        assert 'ENTRYPOINT ["/pipeline_entry.sh"]' in stage

    def test_the_cluster_runner_goes_through_the_dispatcher(self):
        runner = (REPO / "srr_pipeline_package" / "singularity"
                  / "run_singularity_pipeline.sh").read_text()
        assert "/pipeline_entry.sh" in runner
        assert 'CMD+=("$SIF" /pipeline/run_docker_pipeline.sh)' not in runner

    def test_an_older_image_still_runs(self):
        """A .sif built before both trees existed has no dispatcher."""
        runner = (REPO / "srr_pipeline_package" / "singularity"
                  / "run_singularity_pipeline.sh").read_text()
        assert "if [ -x /pipeline_entry.sh ]" in runner
        assert "exec /pipeline/run_docker_pipeline.sh" in runner


class TestTheModeSaysWhichTreeInTheWord:

    def _entry(self):
        return (REPO / "srr_pipeline_package" / "pipeline_entry.sh").read_text()

    def test_every_spelling_is_accepted(self):
        entry = self._entry()
        for key in ("legacy_pipeline", "pipeline_mode", "safe_mode"):
            assert 'data.get("%s")' % key in entry

    def test_the_default_is_the_current_tree(self):
        """It was the other way round while a cluster run had to be matched.

        Nothing runs on that cluster now, and what is left is a pipeline handed
        to people who will run it on hardware nobody here can see. Shipping the
        one whose silent failures are still in it as the default would be an
        odd thing to do.
        """
        entry = self._entry()
        assert 'echo current' in entry
        assert 'chosen = "current"' in entry

    def test_the_generated_config_carries_the_mode(self):
        import importlib.util
        import yaml
        hpc = REPO / "srr_pipeline_package" / "hpc"
        spec = importlib.util.spec_from_file_location(
            "prepare_hpc_handoff", hpc / "prepare_hpc_handoff.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        base = {"cellranger_threads": 16, "cellranger_mem": 100}
        assert yaml.safe_load(module.render_config_yaml(base))["legacy_pipeline"] \
            is False
        assert yaml.safe_load(module.render_config_yaml(
            dict(base, legacy_pipeline=True)))["legacy_pipeline"] is True
        assert yaml.safe_load(module.render_config_yaml(
            dict(base, safe_mode=True)))["legacy_pipeline"] is True


class TestAPlanThatNeedsTheCorrectedTreeGetsIt:
    """Retention and the array are not in the frozen tree."""

    def _bundle(self, tmp_path, mode):
        import importlib.util
        hpc = REPO / "srr_pipeline_package" / "hpc"
        spec = importlib.util.spec_from_file_location(
            "prepare_hpc_handoff", hpc / "prepare_hpc_handoff.py")
        handoff = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(handoff)
        sra = tmp_path / "sra" / "SRR1"
        sra.mkdir(parents=True)
        (sra / "SRR1.sra").write_bytes(b"x")
        plan = tmp_path / "plan"
        plan.mkdir()
        (plan / "batch_001.txt").write_text("SRR1\n")
        (plan / "plan.json").write_text(json.dumps({
            "plan_id": "p", "sample_names": ["SRR1"],
            "sample_bytes": {"SRR1": 1},
            "settings": {"retain": "analysis", "release_input": False,
                         "pipeline_mode": mode, "walltime": 1440},
            "plan": {"fits": True}}))
        cfg = dict(host="h", user="u", port=22,
                   remote_base="/s/x", remote_sif="/s/i.sif",
                   remote_ref="/s/ref", remote_cellranger="/s/cr",
                   transfer_tool="rsync", queue="q", ncpus=16, mem_gb=120,
                   walltime="24:00:00", cellranger_threads=16,
                   cellranger_mem=100)
        return handoff.generate_bundle(cfg, tmp_path / "sra",
                                       tmp_path / "bundle", run_id="run-1",
                                       batches_dir=plan)

    def test_a_corrected_plan_builds(self, tmp_path):
        out = self._bundle(tmp_path, "corrected")
        assert (out / "batches" / "batch_001.txt").is_file()

    def test_a_verified_plan_is_refused(self, tmp_path):
        """It would ignore retention and use a different amount of disk."""
        with pytest.raises(SystemExit) as raised:
            self._bundle(tmp_path, "verified")
        assert "needs the current pipeline" in str(raised.value)


# One table, two implementations. `legacy_pipeline` is the spelling to write;
# the earlier ones still answer, because a config already written should keep
# working. Absent or unreadable is the CURRENT tree -- the default changed when
# the reason for the old one went away.
TREE_TABLE = [
    ({}, "current"),
    ({"legacy_pipeline": True}, "legacy"),
    ({"legacy_pipeline": False}, "current"),
    ({"legacy_pipeline": "true"}, "legacy"),
    ({"legacy_pipeline": "  YES "}, "legacy"),
    ({"legacy_pipeline": ""}, "current"),
    ({"legacy_pipeline": "nonsense"}, "current"),
    ({"legacy_pipeline": True, "safe_mode": False}, "legacy"),
    ({"legacy_pipeline": False}, "current"),
    ({"pipeline_mode": "verified"}, "legacy"),
    ({"pipeline_mode": ""}, "current"),
    ({"pipeline_mode": "nonsense"}, "current"),
    ({"legacy_pipeline": False}, "current"),
    ({"safe_mode": True}, "legacy"),
    ({"safe_mode": "false"}, "current"),
    ({"safe_mode": "off"}, "current"),
    ({"safe_mode": ""}, "current"),
    ({"safe_mode": None}, "current"),
    ({"safe_mode": "maybe"}, "current"),
]



class TestOneRuleForWhichTreeRuns:
    """Three readers, two spellings, and they disagreed.

    The dispatcher took an empty `safe_mode:` as false and chose the tree that
    has never run on a cluster; the bundle generator read only `pipeline_mode`
    and refused a plan that had asked with the other spelling. This value
    decides which code analyses the data.
    """

    def _settings(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "site_settings",
            REPO / "srr_pipeline_package" / "hpc" / "site_settings.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _dispatcher(self, config, tmp_path):
        """The rule as the container runs it, on a real config file."""
        import yaml
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(config) if config else "{}\n")
        entry = (REPO / "srr_pipeline_package" / "pipeline_entry.sh").read_text()
        block = entry[entry.index('TREE_CHOICE="$(python3 - "$CONFIG" <<\'PY\''):]
        block = block[block.index("\n") + 1:block.index("\nPY\n")]
        done = subprocess.run(["python3", "-c", block, str(path)],
                              capture_output=True, text=True)
        return done.stdout.strip()

    @pytest.mark.parametrize("config,expected", TREE_TABLE)
    def test_the_bundle_side_reads_it_this_way(self, config, expected):
        assert self._settings().pipeline_tree_of(config) == expected

    @pytest.mark.parametrize("config,expected", TREE_TABLE)
    def test_the_container_side_agrees(self, config, expected, tmp_path):
        assert self._dispatcher(config, tmp_path) == expected

    def test_a_plan_written_with_the_older_spelling_still_builds(self, tmp_path):
        """`safe_mode: false` asks for the current tree, and the bundle
        generator refused it because it looked only at the other key."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "prepare_hpc_handoff",
            REPO / "srr_pipeline_package" / "hpc" / "prepare_hpc_handoff.py")
        handoff = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(handoff)
        sra = tmp_path / "sra" / "SRR1"
        sra.mkdir(parents=True)
        (sra / "SRR1.sra").write_bytes(b"x")
        plan = tmp_path / "plan"
        plan.mkdir()
        (plan / "batch_001.txt").write_text("SRR1\n")
        (plan / "plan.json").write_text(json.dumps({
            "plan_id": "p", "sample_names": ["SRR1"],
            "sample_bytes": {"SRR1": 1},
            # Written with the older spelling on purpose: this is the test
            # that it still answers.
            "settings": {"retain": "analysis", "release_input": False,
                         "safe_mode": False, "walltime": 1440},
            "plan": {"fits": True}}))
        out = handoff.generate_bundle(
            dict(host="h", user="u", port=22, remote_base="/s/x",
                 remote_sif="/s/i.sif", remote_ref="/s/ref",
                 remote_cellranger="/s/cr", transfer_tool="rsync", queue="q",
                 ncpus=16, mem_gb=120, walltime="24:00:00",
                 cellranger_threads=16, cellranger_mem=100),
            tmp_path / "sra", tmp_path / "bundle", run_id="run-1",
            batches_dir=plan)
        import yaml
        assert yaml.safe_load(
            (out / "config.yaml").read_text())["legacy_pipeline"] is False


class TestAnOldImageWillNotQuietlyRunTheOtherTree:
    """Falling back is right for a run that wanted the verified tree.

    It is wrong for one that asked for the corrected tree: the run goes ahead
    under code the config did not ask for, and nothing in the output says so.
    """

    def _fallback(self, tmp_path, config_text, with_dispatcher=False):
        runner = (REPO / "srr_pipeline_package" / "singularity"
                  / "run_singularity_pipeline.sh").read_text()
        block = runner[runner.index("'if [ -x /pipeline_entry.sh ]") + 1:
                       runner.index("' --)")]
        image = tmp_path / "image"
        (image / "pipeline").mkdir(parents=True)
        (image / "pipeline" / "run_docker_pipeline.sh").write_text(
            "#!/bin/sh\necho RAN-VERIFIED\n")
        (image / "pipeline" / "run_docker_pipeline.sh").chmod(0o755)
        if with_dispatcher:
            (image / "pipeline_entry.sh").write_text(
                "#!/bin/sh\necho RAN-DISPATCHER\n")
            (image / "pipeline_entry.sh").chmod(0o755)
        config = tmp_path / "config.yaml"
        config.write_text(config_text)
        block = (block.replace("/pipeline_entry.sh", str(image / "pipeline_entry.sh"))
                      .replace("/pipeline/run_docker", str(image / "pipeline" / "run_docker"))
                      .replace("/work/config.yaml", str(config)))
        return subprocess.run(["sh", "-c", block, "--"],
                              capture_output=True, text=True)

    def test_asking_for_the_corrected_tree_on_an_old_image_fails(self, tmp_path):
        done = self._fallback(tmp_path, "pipeline_mode: corrected\n")
        assert done.returncode == 2
        assert "Rebuild the image" in done.stderr
        assert "RAN-VERIFIED" not in done.stdout

    def test_the_older_spelling_fails_too(self, tmp_path):
        done = self._fallback(tmp_path, "safe_mode: false\n")
        assert done.returncode == 2
        assert "RAN-VERIFIED" not in done.stdout

    def test_a_quoted_value_is_read(self, tmp_path):
        done = self._fallback(tmp_path, 'pipeline_mode: "corrected"\n')
        assert done.returncode == 2

    @pytest.mark.parametrize("text", ["legacy_pipeline: true\n",
                                      "pipeline_mode: verified\n",
                                      "safe_mode: true\n"])
    def test_a_run_that_asked_for_the_earlier_tree_still_runs(self, tmp_path, text):
        done = self._fallback(tmp_path, text)
        assert done.returncode == 0, done.stderr
        assert "RAN-VERIFIED" in done.stdout

    def test_a_config_that_says_nothing_now_refuses_on_an_old_image(self, tmp_path):
        """The flipped default reaches here, and should.

        An image built before the split carries only the earlier tree. A config
        that names nothing is asking for the current one, so running anyway
        would hand back the earlier tree's results under the current tree's
        name -- which is the whole failure this fallback exists to prevent.
        The cost is that an old image has to be rebuilt, and the message says
        so.
        """
        done = self._fallback(tmp_path, "{}\n")
        assert done.returncode != 0
        assert "Rebuild the image" in done.stderr

    def test_a_current_image_never_reaches_the_fallback(self, tmp_path):
        done = self._fallback(tmp_path, "legacy_pipeline: false\n",
                              with_dispatcher=True)
        assert "RAN-DISPATCHER" in done.stdout
