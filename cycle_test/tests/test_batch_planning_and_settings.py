"""Deciding numbers the site is the only one who knows.

A corpus does not fit — not on the disk a shared facility hands out, and not
inside one job's wall clock. Splitting it needs figures nobody here has: the
quota, the queue's limit, how long a sample takes on that hardware. Guessing
any of them is discovered after the allocation is spent, so the rule is the one
the Cell Ranger path already follows — take it from the flag, the config, the
machine, or the person, and otherwise stop and say which key to write.
"""

import io
import json
import os

import yaml
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HPC = REPO / "srr_pipeline_package" / "hpc"
sys.path.insert(0, str(HPC))

import plan_batches as pb  # noqa: E402
import site_settings as st  # noqa: E402


def _answers(*given):
    """An `ask` that hands back the given answers, then refuses to be asked again."""
    it = iter(given)

    def ask(prompt):
        try:
            return next(it)
        except StopIteration:
            raise AssertionError("asked more questions than expected: %r" % prompt)
    return ask


A_NUMBER = st.Setting("batch_size", "How many samples per job",
                      "hpc_config.yaml", st.positive_int)


class TestWhereAValueComesFrom:
    """Flag, then config, then the machine, then the person, then a refusal."""

    def test_a_flag_wins_over_everything(self):
        setting = st.Setting("n", "How many", "cfg.yaml", st.positive_int,
                             detect=lambda: (9, "measured"), default=3)
        assert st.resolve(setting, "7", {"n": 5}) == (7, "command line")

    def test_the_config_wins_over_the_machine(self):
        setting = st.Setting("n", "How many", "cfg.yaml", st.positive_int,
                             detect=lambda: (9, "measured"))
        assert st.resolve(setting, None, {"n": 5}) == (5, "cfg.yaml")

    def test_the_machine_wins_over_a_default(self):
        """A number measured here beats one that was true somewhere else."""
        setting = st.Setting("n", "How many", "cfg.yaml", st.positive_int,
                             detect=lambda: (9, "measured"), default=3)
        assert st.resolve(setting, None, {}) == (9, "measured")

    def test_a_default_is_used_when_nothing_could_be_measured(self):
        setting = st.Setting("n", "How many", "cfg.yaml", st.positive_int,
                             detect=lambda: None, default=3)
        assert st.resolve(setting, None, {}) == (3, "default")

    def test_the_source_is_carried_so_a_plan_can_be_checked(self):
        _, source = st.resolve(A_NUMBER, None, {"batch_size": 5})
        assert source == "hpc_config.yaml"


class TestWhenNobodyKnows:

    def test_it_names_the_key_and_the_file(self):
        with pytest.raises(st.Unknown) as raised:
            st.resolve(A_NUMBER, None, {}, interactive=False)
        message = str(raised.value)
        assert "batch_size" in message
        assert "hpc_config.yaml" in message
        assert "--batch-size" in message

    def test_it_never_invents_a_number(self):
        """The failure mode this exists to prevent is a plausible guess."""
        with pytest.raises(st.Unknown):
            st.resolve(A_NUMBER, None, {}, interactive=False)

    def test_a_value_that_does_not_parse_is_refused_not_rounded(self):
        with pytest.raises(st.Unknown) as raised:
            st.resolve(A_NUMBER, None, {"batch_size": "lots"}, interactive=False)
        assert "batch_size" in str(raised.value)

    def test_zero_is_not_a_batch_size(self):
        with pytest.raises(st.Unknown):
            st.resolve(A_NUMBER, "0", {})


class TestAsking:

    def test_it_asks_when_it_has_to(self):
        value, source = st.resolve(A_NUMBER, None, {}, interactive=True,
                                   ask=_answers("12"), out=io.StringIO())
        assert (value, source) == (12, "asked")

    def test_a_bad_answer_is_asked_again_rather_than_accepted(self):
        out = io.StringIO()
        value, _ = st.resolve(A_NUMBER, None, {}, interactive=True,
                              ask=_answers("none", "-4", "8"), out=out)
        assert value == 8
        assert "Try again" in out.getvalue()

    def test_review_offers_what_was_found_and_takes_enter_for_it(self):
        setting = st.Setting("n", "How many", "cfg.yaml", st.positive_int,
                             detect=lambda: (9, "measured"))
        out = io.StringIO()
        value, source = st.resolve(setting, None, {}, interactive=True,
                                   review=True, ask=_answers(""), out=out)
        assert (value, source) == (9, "measured")
        assert "from measured" in out.getvalue()

    def test_review_lets_the_person_overrule_the_machine(self):
        setting = st.Setting("n", "How many", "cfg.yaml", st.positive_int,
                             detect=lambda: (9, "measured"))
        value, source = st.resolve(setting, None, {}, interactive=True,
                                   review=True, ask=_answers("40"),
                                   out=io.StringIO())
        assert (value, source) == (40, "asked")

    def test_nothing_is_asked_when_the_answer_is_already_known(self):
        """`_answers` fails the test if it is called at all."""
        st.resolve(A_NUMBER, None, {"batch_size": 5}, interactive=True,
                   ask=_answers(), out=io.StringIO())

    def test_an_input_stream_that_ends_is_not_a_zero(self):
        def ask(prompt):
            raise EOFError
        with pytest.raises(st.Unknown) as raised:
            st.resolve(A_NUMBER, None, {}, interactive=True, ask=ask,
                       out=io.StringIO())
        assert "hpc_config.yaml" in str(raised.value)


class TestWalltimeIsReadTheWayTheQueueWritesIt:

    def test_hours_minutes_seconds_become_minutes(self):
        assert st.walltime("120:00:00") == 7200
        assert st.walltime("01:30:00") == 90

    @pytest.mark.parametrize("bad", ["120h", "120:00", "", "-1:00:00", "a:b:c"])
    def test_anything_else_is_refused(self, bad):
        with pytest.raises(ValueError):
            st.walltime(bad)


GIB = float(1 << 30)


def _corpus(count, gb_each):
    return [("SRR%07d" % i, int(gb_each * GIB)) for i in range(1, count + 1)]


class TestReadingTheInputDirectory:

    def test_both_input_layouts_are_found(self, tmp_path):
        (tmp_path / "SRR0000001").mkdir()
        (tmp_path / "SRR0000001" / "SRR0000001.sra").write_bytes(b"x" * 300)
        (tmp_path / "SRR0000002.sra").write_bytes(b"x" * 100)
        found = dict(pb.find_samples(tmp_path))
        assert found == {"SRR0000001": 300, "SRR0000002": 100}

    def test_the_largest_come_first(self, tmp_path):
        for name, size in (("SRR1", 10), ("SRR2", 300), ("SRR3", 50)):
            (tmp_path / (name + ".sra")).write_bytes(b"x" * size)
        assert [n for n, _ in pb.find_samples(tmp_path)] == ["SRR2", "SRR3", "SRR1"]

    def test_a_missing_directory_is_empty_not_a_crash(self, tmp_path):
        assert pb.find_samples(tmp_path / "nope") == []

    def test_what_a_sample_keeps_is_measured_when_a_run_is_there_to_measure(self, tmp_path):
        success = tmp_path / "success"
        outs = success / "SRR1" / "cellranger_output" / "outs"
        outs.mkdir(parents=True)
        (outs / "filtered_feature_bc_matrix.h5").write_bytes(b"x" * 200)
        ratio, how = pb.measure_residue_ratio(success, [("SRR1", 1000)])
        assert ratio == 0.2
        assert "measured" in how

    def test_a_measurement_never_goes_below_what_the_policy_keeps(self, tmp_path):
        """Nothing here can tell which policy produced the tree it measures.

        Measuring a `summary` run and then planning an `all` run underestimates
        what accumulates by fifty times and overfills the disk. Measuring high
        and planning low only costs a smaller batch.
        """
        success = tmp_path / "success"
        outs = success / "SRR1" / "cellranger_output" / "outs"
        outs.mkdir(parents=True)
        (outs / "metrics_summary.csv").write_bytes(b"x" * 10)
        ratio, how = pb.measure_residue_ratio(success, [("SRR1", 1000)], floor=1.5)
        assert ratio == 1.5
        assert "held at" in how

    def test_an_empty_output_tree_is_not_a_measurement_of_zero(self, tmp_path):
        """A zero ratio stopped the whole plan on `must be greater than zero`."""
        success = tmp_path / "success"
        (success / "SRR1" / "cellranger_output" / "outs").mkdir(parents=True)
        assert pb.measure_residue_ratio(success, [("SRR1", 1000)]) is None

    def test_nothing_to_measure_leaves_the_estimate_alone(self, tmp_path):
        assert pb.measure_residue_ratio(tmp_path, [("SRR1", 10)]) is None


class TestTheWholeToolEndToEnd:

    def _corpus_dir(self, tmp_path, count=25):
        sra = tmp_path / "sra"
        for i in range(1, count + 1):
            d = sra / ("SRR%07d" % i)
            d.mkdir(parents=True)
            (d / ("SRR%07d.sra" % i)).write_bytes(b"x" * 1_000_000)
        return sra

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(HPC / "plan_batches.py")] + [str(a) for a in args],
            capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def test_it_writes_a_plan_and_a_list_per_batch(self, tmp_path):
        sra = self._corpus_dir(tmp_path)
        out = tmp_path / "plan"
        done = self._run("--input-dir", sra, "--out", out, "--non-interactive",
                         "--walltime", "02:00:00", "--minutes-per-gb", "2.9",
                         "--disk-gb", "500")
        assert done.returncode == 0, done.stderr
        payload = json.loads((out / "plan.json").read_text())
        assert payload["samples"] == 25
        assert payload["plan_id"].startswith("plan-")
        # Packed by time, so the count follows the sizes rather than a fixed
        # samples-per-job number.
        assert payload["plan"]["batches"]
        assert sum(len(b["samples"]) for b in payload["plan"]["batches"]) == 25
        assert len(list(out.glob("batch_*.txt"))) == len(payload["plan"]["batches"])
        assert (out / "batch_001.txt").read_text().split() == \
            payload["plan"]["batches"][0]["samples"]

    def test_it_refuses_a_mixed_corpus_when_any_sample_exceeds_walltime(
            self, tmp_path):
        """Runnable neighbours must not hide samples no job can finish."""
        sra = tmp_path / "sra"
        sra.mkdir()
        (sra / "SRR0000001.sra").write_bytes(b"small")
        for name, size_gib in (("SRR0000002", 0.9), ("SRR0000003", 1.1)):
            with (sra / (name + ".sra")).open("wb") as fh:
                fh.truncate(int(size_gib * pb.GIB))

        out = tmp_path / "plan"
        done = self._run(
            "--input-dir", sra, "--out", out, "--non-interactive",
            "--walltime", "00:01:00", "--minutes-per-gb", "1",
            "--disk-gb", "100")

        assert done.returncode == 4, done.stdout + done.stderr
        message = done.stdout + done.stderr
        assert "SRR0000002" in message
        assert "SRR0000003" in message
        payload = json.loads((out / "plan.json").read_text())
        assert payload["plan"]["fits"] is False
        assert set(payload["plan"]["over_clock"]) == {
            "SRR0000002", "SRR0000003"}

    def test_it_says_where_every_number_came_from(self, tmp_path):
        sra = self._corpus_dir(tmp_path)
        done = self._run("--input-dir", sra, "--out", tmp_path / "plan",
                         "--non-interactive", "--walltime", "02:00:00",
                         "--minutes-per-gb", "2.9", "--disk-gb", "500")
        assert done.returncode == 0, done.stdout + done.stderr
        assert "(command line)" in done.stdout
        assert "(default)" in done.stdout
        assert "peak, against a 500 GB quota" in done.stdout

    def test_a_missing_number_stops_it_with_the_key_to_write(self, tmp_path):
        sra = self._corpus_dir(tmp_path)
        done = self._run("--input-dir", sra, "--out", tmp_path / "plan",
                         "--non-interactive")
        assert done.returncode == 2
        assert "disk_gb" in done.stderr
        assert "hpc_config.yaml" in done.stderr
        assert "Traceback" not in done.stderr, "an operator is not a stack trace"

    def test_non_interactive_refuses_to_guess_the_site_runtime_rate(self,
                                                                    tmp_path):
        sra = self._corpus_dir(tmp_path)
        out = tmp_path / "plan"

        done = self._run(
            "--input-dir", sra, "--out", out, "--non-interactive",
            "--walltime", "02:00:00", "--disk-gb", "500")

        assert done.returncode == 2
        assert "minutes_per_gb" in done.stderr
        assert "--minutes-per-gb" in done.stderr
        assert "measure" in done.stderr.lower()
        assert not out.exists()

    @pytest.mark.parametrize("via", ["command line", "hpc_config.yaml"])
    def test_an_explicit_site_runtime_rate_is_accepted(self, tmp_path, via):
        sra = self._corpus_dir(tmp_path)
        out = tmp_path / "plan"
        args = ["--input-dir", sra, "--out", out, "--non-interactive",
                "--walltime", "02:00:00", "--disk-gb", "500"]
        if via == "command line":
            args.extend(["--minutes-per-gb", "2.9"])
        else:
            config = tmp_path / "hpc_config.yaml"
            config.write_text("minutes_per_gb: 2.9\n")
            args.extend(["--config", config])

        done = self._run(*args)

        assert done.returncode == 0, done.stdout + done.stderr
        payload = json.loads((out / "plan.json").read_text())
        assert payload["settings"]["minutes_per_gb"] == 2.9
        assert payload["sources"]["minutes_per_gb"] == via

    def test_the_config_is_read_when_one_is_given(self, tmp_path):
        sra = self._corpus_dir(tmp_path)
        cfg = tmp_path / "hpc_config.yaml"
        cfg.write_text('walltime: "02:00:00"\nminutes_per_gb: 2.9\ndisk_gb: 500\n')
        done = self._run("--input-dir", sra, "--out", tmp_path / "plan",
                         "--config", cfg, "--non-interactive")
        assert done.returncode == 0, done.stderr
        assert "hpc_config.yaml)" in done.stdout

    def test_an_empty_input_directory_is_reported_not_planned(self, tmp_path):
        (tmp_path / "sra").mkdir()
        done = self._run("--input-dir", tmp_path / "sra", "--out",
                         tmp_path / "plan", "--non-interactive")
        assert done.returncode == 3
        assert "No inputs found" in done.stderr


import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "prepare_hpc_handoff", HPC / "prepare_hpc_handoff.py")
handoff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handoff)
import schedulers as sch  # noqa: E402


def _cfg(**over):
    base = dict(host="h", user="u", port=22, remote_base="/scratch/x",
                remote_sif="/scratch/x/i.sif", remote_ref="/scratch/x/ref",
                remote_cellranger="/scratch/x/cellranger",
                transfer_tool="rsync", queue="normal", ncpus=16, mem_gb=120,
                walltime="24:00:00", cellranger_threads=16, cellranger_mem=100)
    base.update(over)
    return base


class TestThePlannerAndHandoffShareOnePipelineContract:
    """Exercise the two public tools in sequence, not hand-written plan JSON."""

    def _plan(self, tmp_path, config_text=""):
        sra = tmp_path / "sra" / "SRR1"
        sra.mkdir(parents=True)
        (sra / "SRR1.sra").write_bytes(b"x" * 1_000_000)
        config = tmp_path / "hpc_config.yaml"
        config.write_text(
            'walltime: "24:00:00"\nminutes_per_gb: 2.9\ndisk_gb: 500\n'
            + config_text)
        plan = tmp_path / "plan"
        done = subprocess.run(
            [sys.executable, str(HPC / "plan_batches.py"),
             "--input-dir", str(sra.parent), "--out", str(plan),
             "--config", str(config), "--non-interactive"],
            capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr
        return sra.parent, plan

    def test_the_default_current_choice_survives_planning_and_handoff(self,
                                                                      tmp_path):
        sra, plan = self._plan(tmp_path)
        planned = json.loads((plan / "plan.json").read_text())
        assert planned["settings"]["legacy_pipeline"] is False

        bundle = handoff.generate_bundle(
            _cfg(), sra, tmp_path / "bundle", run_id="run-1",
            batches_dir=plan)
        assert yaml.safe_load(
            (bundle / "config.yaml").read_text())["legacy_pipeline"] is False

    @pytest.mark.parametrize("choice", [
        "legacy_pipeline: false\n",
        "pipeline_mode: corrected\n",
        "safe_mode: false\n",
    ])
    def test_an_explicit_current_choice_survives_planning_and_handoff(
            self, tmp_path, choice):
        sra, plan = self._plan(tmp_path, choice)
        planned = json.loads((plan / "plan.json").read_text())
        assert planned["settings"]["legacy_pipeline"] is False
        assert planned["sources"]["legacy_pipeline"] == "hpc_config.yaml"

        bundle = handoff.generate_bundle(
            _cfg(legacy_pipeline=False), sra, tmp_path / "bundle",
            run_id="run-1", batches_dir=plan)
        assert yaml.safe_load(
            (bundle / "config.yaml").read_text())["legacy_pipeline"] is False

    def test_a_legacy_corpus_plan_is_refused_at_handoff(self, tmp_path):
        sra, plan = self._plan(tmp_path, "legacy_pipeline: true\n")
        planned = json.loads((plan / "plan.json").read_text())
        assert planned["settings"]["legacy_pipeline"] is True

        with pytest.raises(SystemExit) as raised:
            handoff.generate_bundle(
                _cfg(legacy_pipeline=True), sra, tmp_path / "bundle",
                run_id="run-1", batches_dir=plan)
        assert "needs the current pipeline" in str(raised.value)

    def test_handoff_refuses_to_silently_replace_an_explicit_choice(self,
                                                                    tmp_path):
        sra, plan = self._plan(tmp_path)
        with pytest.raises(SystemExit) as raised:
            handoff.generate_bundle(
                _cfg(legacy_pipeline=True), sra, tmp_path / "bundle",
                run_id="run-1", batches_dir=plan)
        message = str(raised.value)
        assert "plan.json" in message
        assert "legacy_pipeline" in message

    def test_a_pre_contract_plan_is_refused_instead_of_guessed(self, tmp_path):
        sra, plan = self._plan(tmp_path)
        path = plan / "plan.json"
        payload = json.loads(path.read_text())
        del payload["settings"]["legacy_pipeline"]
        payload["sources"].pop("legacy_pipeline", None)
        path.write_text(json.dumps(payload))

        with pytest.raises(SystemExit) as raised:
            handoff.generate_bundle(
                _cfg(), sra, tmp_path / "bundle", run_id="run-1",
                batches_dir=plan)
        assert "does not record" in str(raised.value)
        assert "plan_batches.py" in str(raised.value)

    def test_an_input_added_after_planning_invalidates_the_contract(self,
                                                                    tmp_path):
        sra, plan = self._plan(tmp_path)
        added = sra / "SRR2"
        added.mkdir()
        (added / "SRR2.sra").write_bytes(b"new input")

        with pytest.raises(SystemExit) as raised:
            handoff.generate_bundle(
                _cfg(), sra, tmp_path / "bundle", run_id="run-1",
                batches_dir=plan)

        message = str(raised.value)
        assert "unplanned sample(s) were added: SRR2" in message
        assert "Run plan_batches.py again" in message

    def test_a_changed_execution_walltime_invalidates_the_plan(self, tmp_path):
        """A batch sized for 24 hours must not be submitted with one hour."""
        sra, plan = self._plan(tmp_path)
        out = tmp_path / "bundle"

        with pytest.raises(SystemExit) as raised:
            handoff.generate_bundle(
                _cfg(walltime="01:00:00"), sra, out, run_id="run-1",
                batches_dir=plan)

        message = str(raised.value)
        assert "walltime" in message
        assert "24:00:00" in message
        assert "01:00:00" in message
        assert not out.exists(), "an invalid plan must not leave a partial bundle"

    def test_an_unreadable_plan_choice_is_refused_instead_of_defaulted(self,
                                                                       tmp_path):
        sra, plan = self._plan(tmp_path)
        path = plan / "plan.json"
        payload = json.loads(path.read_text())
        payload["settings"]["legacy_pipeline"] = "perhaps"
        path.write_text(json.dumps(payload))

        with pytest.raises(SystemExit) as raised:
            handoff.generate_bundle(
                _cfg(), sra, tmp_path / "bundle", run_id="run-1",
                batches_dir=plan)
        assert "invalid pipeline choice" in str(raised.value)


def test_the_shipped_hpc_template_names_only_real_planner_controls(tmp_path):
    """An operator should never tune a key the planner silently ignores."""
    template = yaml.safe_load(
        (HPC / "hpc_config.example.yaml").read_text())
    definitions = pb.settings_for(
        tmp_path / "sra", tmp_path / "plan", None, [], "all")

    for key in ("minutes_per_gb", "fastq_expansion", "clock_margin",
                "max_concurrent_jobs"):
        assert key in template
        assert key in definitions
    for obsolete in ("minutes_per_sample", "working_multiple",
                     "samples_at_once"):
        assert obsolete not in template


def test_the_shipped_hpc_template_has_no_cross_site_runtime_default():
    template = yaml.safe_load(
        (HPC / "hpc_config.example.yaml").read_text())

    assert template["minutes_per_gb"] is None


class TestHandoffResourceValidation:
    @pytest.mark.parametrize(
        "over, expected",
        [
            ({"ncpus": 1, "cellranger_threads": 16}, "ncpus"),
            ({"mem_gb": 100, "cellranger_mem": 100}, "mem_gb"),
            ({"ncpus": 0}, "ncpus"),
            ({"cellranger_threads": "many"}, "cellranger_threads"),
        ],
    )
    def test_an_impossible_resource_request_is_refused_before_writing(
            self, tmp_path, over, expected):
        sra = tmp_path / "sra"
        sra.mkdir()
        out = tmp_path / "bundle"

        with pytest.raises(SystemExit) as raised:
            handoff.generate_bundle(
                _cfg(**over), sra, out, run_id="run-1")

        assert expected in str(raised.value)
        assert not out.exists()

    def test_the_documented_resource_relationship_is_accepted(self):
        sch.validate(_cfg(ncpus=16, cellranger_threads=16,
                          mem_gb=120, cellranger_mem=100))


def _bash_ok(text):
    import os
    import tempfile
    fh = tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False)
    fh.write(text)
    fh.close()
    done = subprocess.run(["bash", "-n", fh.name], capture_output=True, text=True)
    os.unlink(fh.name)
    return done.returncode == 0, done.stderr


class TestOneJobBecomesAnArray:
    """Seventy batches is seventy submissions unless the scheduler is told."""

    def test_a_bundle_with_no_batches_is_what_it_always_was(self):
        script = sch.render_job_script(_cfg(), "run-A")
        assert "--array" not in script
        assert "GENOAR_BATCH" not in script
        assert 'SRA_DIR="./data/sra"' in script

    def test_slurm_gets_an_array_sized_from_the_lists(self):
        script = sch.render_job_script(_cfg(array_count=73), "run-A")
        assert "#SBATCH --array=1-73%1" in script

    def test_concurrency_defaults_to_one_because_the_quota_is_shared(self):
        """Two tasks at once need twice the disk the batch size assumed."""
        assert sch.max_concurrent(_cfg(array_count=73)) == 1
        assert sch.max_concurrent(_cfg(array_count=73, max_concurrent_jobs=4)) == 4

    def test_array_logs_are_named_per_task(self):
        script = sch.render_job_script(_cfg(array_count=5), "run-A")
        assert "slurm-%A_%a.out" in script
        assert "slurm-%j.out" not in script

    def test_pbspro_gets_its_own_array_directive(self):
        script = sch.render_job_script(_cfg(scheduler="pbspro", array_count=9),
                                       "run-A")
        assert "#PBS -J 1-9" in script

    def test_torque_uses_its_own_array_directive_and_index(self):
        script = sch.render_job_script(_cfg(scheduler="torque", array_count=9),
                                       "run-A")
        assert "#PBS -J" not in script
        assert "#PBS -t 1-9" in script
        assert "PBS_ARRAYID" in script

    @pytest.mark.parametrize("cfg", [_cfg(), _cfg(array_count=73),
                                     _cfg(scheduler="pbspro", array_count=9)])
    def test_every_job_script_is_valid_shell(self, cfg):
        ok, why = _bash_ok(sch.render_job_script(cfg, "run-A"))
        assert ok, why


class TestEachTaskGetsItsOwnEverything:
    """Shared state across seventy concurrent tasks is the failure to avoid.

    What each of these asserts is checked by running the block, in
    TestTheArrayBlockRunAsShell. These pin the rendered text only where the
    text is the contract: a variable name the scheduler sets, or an id the
    pipeline will refuse a duplicate of.
    """

    def _body(self):
        return sch.render_job_script(_cfg(array_count=73), "run-A")

    def test_the_run_id_carries_the_batch_and_the_attempt(self):
        """A re-submitted task is a new run; a repeated id is refused."""
        assert 'RUN_ID="run-A-b$GENOAR_BATCH-$GENOAR_ATTEMPT"' in self._body()

    def test_both_schedulers_index_variables_are_read(self):
        body = self._body()
        assert "SLURM_ARRAY_TASK_ID" in body and "PBS_ARRAY_INDEX" in body

    def test_inputs_are_linked_not_copied(self):
        """A batch is hundreds of GB; copying it is the disk the plan saved."""
        assert "ln -sfn" in self._body()
        assert "cp -a" not in self._body()


class TestTheBundleCarriesTheBatches:

    def _plan_dir(self, tmp_path, count):
        d = tmp_path / "plan"
        d.mkdir()
        for n in range(1, count + 1):
            (d / ("batch_%03d.txt" % n)).write_text("SRR%07d\n" % n)
        return d

    def test_the_lists_travel_and_size_the_array(self, tmp_path):
        sra = tmp_path / "sra"
        (sra / "SRR0000001").mkdir(parents=True)
        (sra / "SRR0000001" / "a.sra").write_bytes(b"x")
        plan = self._plan_dir(tmp_path, 4)
        (plan / "plan.json").write_text(json.dumps(
            {"plan_id": "p", "sample_names": ["SRR0000001"],
             "settings": {"retain": "all", "release_input": False,
                          "pipeline_mode": "corrected", "walltime": 1440},
             "plan": {"fits": True}}))
        out = handoff.generate_bundle(_cfg(), sra, tmp_path / "bundle",
                                      run_id="run-1", batches_dir=plan)
        assert sorted(p.name for p in (out / "batches").glob("batch_*.txt")) == [
            "batch_001.txt", "batch_002.txt", "batch_003.txt", "batch_004.txt"]
        # plan.json sits beside them as well as at the bundle root, because
        # `--plan bundle/batches` is the natural thing to type and without it
        # there the cross-check against the plan's own sample list is lost.
        assert (out / "batches" / "plan.json").is_file()
        assert (out / "corpus_report.py").is_file(), (
            "the only thing that can see a batch nobody submitted")
        assert "#SBATCH --array=1-4%1" in (out / "run_stage3.slurm").read_text()

    def test_a_gap_in_the_numbering_is_refused(self, tmp_path):
        """An array task whose list is missing finds out after it is queued."""
        d = tmp_path / "plan"
        d.mkdir()
        (d / "batch_001.txt").write_text("SRR1\n")
        (d / "batch_003.txt").write_text("SRR3\n")
        with pytest.raises(SystemExit) as raised:
            handoff.copy_batches(d, tmp_path / "bundle")
        assert "without gaps" in str(raised.value)

    def test_an_empty_plan_directory_is_refused(self, tmp_path):
        (tmp_path / "plan").mkdir()
        with pytest.raises(SystemExit):
            handoff.copy_batches(tmp_path / "plan", tmp_path / "bundle")

    def test_no_batches_leaves_the_bundle_alone(self, tmp_path):
        assert handoff.copy_batches(None, tmp_path / "bundle") == 0


class TestBringingBackOnlyWhatIsWanted:

    def test_the_default_is_everything(self):
        script = handoff.render_retrieve(_cfg(), "stage3_results")
        assert 'MODE="all"' in script

    def test_a_site_can_set_the_default(self):
        script = handoff.render_retrieve(_cfg(retrieve="summary"), "stage3_results")
        assert 'MODE="summary"' in script

    def test_an_unknown_default_is_refused_when_the_bundle_is_built(self):
        with pytest.raises(SystemExit):
            handoff.render_retrieve(_cfg(retrieve="everything"), "stage3_results")

    def test_analysis_excludes_the_bam_and_nothing_else(self):
        assert handoff.RETRIEVE_FILTERS["analysis"] == [
            "--exclude=possorted_genome_bam.bam",
            "--exclude=possorted_genome_bam.bam.bai"]

    def test_summary_is_a_whitelist_and_the_excludes_come_last(self):
        """rsync applies filters in order; a catch-all first drops everything."""
        filters = handoff.RETRIEVE_FILTERS["summary"]
        assert filters[-1] == "--exclude=*"
        assert all(f.startswith("--include=") for f in filters[:-1])

    def test_the_run_records_come_back_in_every_mode(self):
        """Results without outcome.json are numbers nobody can check."""
        assert "--include=runs/***" in handoff.RETRIEVE_FILTERS["summary"]
        assert not handoff.RETRIEVE_FILTERS["all"]
        assert all(not f.startswith("--exclude=runs")
                   for f in handoff.RETRIEVE_FILTERS["analysis"])

    def test_scp_says_it_cannot_filter_rather_than_filtering_wrongly(self):
        script = handoff.render_retrieve(_cfg(transfer_tool="scp"), "stage3_results")
        assert "scp cannot filter" in script

    @pytest.mark.parametrize("tool", ["rsync", "scp"])
    def test_the_script_is_valid_shell(self, tool):
        ok, why = _bash_ok(handoff.render_retrieve(_cfg(transfer_tool=tool),
                                                   "stage3_results"))
        assert ok, why


class TestWhatDecidesTheRun:
    """The clock decides the batch. The disk decides whether it runs at all."""

    def _corpus(self, count=500, gb=15.2):
        return [("S%04d" % i, int(gb * GIB)) for i in range(count)]

    def test_the_batch_is_packed_by_time_not_by_count(self):
        """Cell Ranger's work is proportional to the reads it is given.

        A flat per-sample estimate taken from a 2 GB pilot said 163 samples
        fit in five days; at this corpus's 15 GB mean the true figure is a
        fraction of that, and the first batches -- packed largest-first --
        overran worst.
        """
        result = pb.plan(self._corpus(), 100000, 7200, 2.9, 0.2, 4.0)
        assert result["bound_by"] == "clock"
        for batch in result["batches"]:
            assert batch["estimated_minutes"] <= 7200 * 0.8 + 1, batch

    def test_a_bigger_sample_costs_more_of_the_batch(self):
        small = [("S%d" % i, int(2 * GIB)) for i in range(200)]
        large = [("L%d" % i, int(30 * GIB)) for i in range(200)]
        assert (pb.plan(small, 10**6, 7200, 2.9, 0.2, 4.0)["batch_size"]
                > pb.plan(large, 10**6, 7200, 2.9, 0.2, 4.0)["batch_size"])

    def test_a_sample_too_long_for_one_job_is_named(self):
        """A wall clock no single sample can finish inside is not a batch."""
        corpus = [("HUGE", int(9000 * GIB)), ("ok", int(4 * GIB))]
        result = pb.plan(corpus, 10**9, 7200, 2.9, 0.2, 4.0)
        assert result["over_clock"] == ["HUGE"]
        placed = [s for b in result["batches"] for s in b["samples"]]
        assert "HUGE" not in placed

    def test_the_peak_has_all_three_terms(self):
        """Inputs, accumulated output, and the copy step 5 makes of the reads.

        The third was missing: step 5 copies each sample's whole directory into
        results/success before Cell Ranger runs, and retention only takes it
        back afterwards, so a batch's reads are on the disk twice while it
        runs.
        """
        result = pb.plan(self._corpus(), 2000, 7200, 2.9, 0.2, 4.0)
        assert result["fits"] is False
        assert result["peak_gb"] > 7600 + 1520 + 45.6
        assert result["transient_gb"] > 0

    def test_the_accumulated_part_does_not_shrink_with_the_batch(self):
        long_clock = pb.plan(self._corpus(), 100000, 7200, 2.9, 0.2, 4.0)
        short_clock = pb.plan(self._corpus(), 100000, 600, 2.9, 0.2, 4.0)
        assert (short_clock["residue_total_gb"]
                == pytest.approx(long_clock["residue_total_gb"]))

    def test_a_smaller_batch_does_reduce_the_copied_reads(self):
        """Which is why the report may not claim batching cannot help."""
        long_clock = pb.plan(self._corpus(), 100000, 7200, 2.9, 0.2, 4.0)
        short_clock = pb.plan(self._corpus(), 100000, 600, 2.9, 0.2, 4.0)
        assert len(short_clock["batches"]) > len(long_clock["batches"])
        assert short_clock["peak_gb"] < long_clock["peak_gb"]

    def test_tasks_running_together_cost_scratch_and_copies(self):
        """The array shares one filesystem; the plan priced one task."""
        alone = pb.plan(self._corpus(), 100000, 7200, 2.9, 0.2, 4.0)
        several = pb.plan(self._corpus(), 100000, 7200, 2.9, 0.2, 4.0,
                          concurrent_jobs=4)
        assert several["peak_gb"] > alone["peak_gb"]

    def test_a_stricter_retention_does_change_it(self):
        keeping = pb.plan(self._corpus(), 100000, 7200, 2.9, 1.5, 4.0)
        releasing = pb.plan(self._corpus(), 100000, 7200, 2.9, 0.03, 4.0)
        assert releasing["peak_gb"] < keeping["peak_gb"]

    def test_releasing_the_inputs_changes_it_too(self):
        held = pb.plan(self._corpus(), 100000, 7200, 2.9, 0.2, 4.0)
        freed = pb.plan(self._corpus(), 100000, 7200, 2.9, 0.2, 4.0,
                        release_input=True)
        assert freed["peak_gb"] < held["peak_gb"]

    def test_every_sample_lands_in_exactly_one_batch(self):
        corpus = self._corpus(250)
        result = pb.plan(corpus, 100000, 7200, 2.9, 0.2, 4.0)
        placed = [s for b in result["batches"] for s in b["samples"]]
        assert sorted(placed) == sorted(n for n, _ in corpus)
        assert len(placed) == len(set(placed))

    def test_working_on_several_at_once_costs_scratch(self):
        alone = pb.plan(self._corpus(), 100000, 7200, 2.9, 0.2, 4.0)
        together = pb.plan(self._corpus(), 100000, 7200, 2.9, 0.2, 3.0, 4)
        assert together["peak_gb"] > alone["peak_gb"]

    def test_an_empty_corpus_is_not_an_error(self):
        assert pb.plan([], 2000, 7200, 2.9, 0.2, 4.0)["batches"] == []

    def test_a_wall_clock_too_short_for_one_sample_plans_nothing(self):
        result = pb.plan(self._corpus(), 100000, 10, 44, 0.2, 4.0)
        assert result["batches"] == []
        assert result["bound_by"] == "clock"


class TestSayingItDoesNotFit:

    def _payload(self, quota):
        corpus = [("S%04d" % i, int(15.2 * GIB)) for i in range(500)]
        return {"samples": 500, "total_input_gb": 7600.0,
                "settings": {"disk_gb": quota, "residue_ratio": 0.2},
                "sources": {},
                "plan": pb.plan(corpus, quota, 7200, 2.9, 0.2, 4.0)}

    def test_the_shortfall_is_stated_in_gigabytes(self):
        out = io.StringIO()
        pb.report(self._payload(2000), out)
        assert "IT DOES NOT FIT" in out.getvalue()
        assert "Short by" in out.getvalue()

    def test_it_names_the_three_things_filling_the_disk(self):
        out = io.StringIO()
        pb.report(self._payload(2000), out)
        text = out.getvalue()
        assert "output kept, accumulated over every batch" in text
        assert "a second copy of a batch's reads" in text
        assert "expanded to FASTQ" in text
        assert "Two of those four are per batch" in text

    def test_it_says_how_much_of_the_corpus_would_fit(self, tmp_path):
        """Found by asking the same arithmetic about prefixes of the corpus."""
        sra = tmp_path / "sra"
        for i in range(1, 21):
            d = sra / ("SRR%07d" % i)
            d.mkdir(parents=True)
            (d / "x.sra").write_bytes(b"x" * 20_000_000)
        done = subprocess.run(
            [sys.executable, str(HPC / "plan_batches.py"), "--input-dir",
             str(sra), "--out", str(tmp_path / "plan"), "--non-interactive",
             "--walltime", "24:00:00", "--minutes-per-gb", "2.9",
             "--disk-gb", "1"], capture_output=True, text=True)
        assert done.returncode == 4
        assert "of the 20 samples would fit" in done.stdout

    def test_a_plan_that_fits_says_what_is_spare(self):
        out = io.StringIO()
        pb.report(self._payload(20000), out)
        assert "It fits, with" in out.getvalue()

    def test_a_corpus_that_does_not_fit_is_not_exit_zero(self, tmp_path):
        sra = tmp_path / "sra"
        for i in range(4):
            d = sra / ("SRR%07d" % i)
            d.mkdir(parents=True)
            (d / "x.sra").write_bytes(b"x" * 2_000_000)
        done = subprocess.run(
            [sys.executable, str(HPC / "plan_batches.py"), "--input-dir",
             str(sra), "--out", str(tmp_path / "plan"), "--non-interactive",
             "--walltime", "24:00:00", "--minutes-per-gb", "2.9",
             "--disk-gb", "0.001"], capture_output=True, text=True)
        assert done.returncode == 4
        assert "IT DOES NOT FIT" in done.stdout


class TestThePlanIsTheContract:

    def _plan_dir(self, tmp_path, fits=True, retain="summary"):
        d = tmp_path / "plan"
        d.mkdir(exist_ok=True)
        (d / "batch_001.txt").write_text("SRR1\n")
        (d / "plan.json").write_text(json.dumps({
            "kind": "genoar.batch.plan", "plan_id": "plan-abc123",
            "samples": 1, "sample_names": ["SRR1"],
            "settings": {"retain": retain, "release_input": True,
                         "pipeline_mode": "corrected", "disk_gb": 1000,
                         "walltime": 1440},
            "plan": {"fits": fits, "peak_gb": 12345.0}}))
        return d

    def _sra(self, tmp_path):
        sra = tmp_path / "sra" / "SRR1"
        sra.mkdir(parents=True)
        (sra / "SRR1.sra").write_bytes(b"x")
        return tmp_path / "sra"

    def test_the_bundle_takes_retention_from_the_plan(self, tmp_path):
        """The disk the plan checked depends on it, so the config cannot differ."""
        out = handoff.generate_bundle(
            _cfg(retain="all"), self._sra(tmp_path), tmp_path / "bundle",
            run_id="run-1", batches_dir=self._plan_dir(tmp_path))
        config = yaml.safe_load((out / "config.yaml").read_text())
        assert config["retain"] == "summary"
        assert config["release_input"] is True

    def test_the_plan_travels_with_the_bundle(self, tmp_path):
        out = handoff.generate_bundle(
            _cfg(), self._sra(tmp_path), tmp_path / "bundle", run_id="run-1",
            batches_dir=self._plan_dir(tmp_path))
        assert json.loads((out / "plan.json").read_text())["plan_id"] == "plan-abc123"

    def test_a_plan_that_does_not_fit_is_refused(self, tmp_path):
        with pytest.raises(SystemExit) as raised:
            handoff.generate_bundle(
                _cfg(), self._sra(tmp_path), tmp_path / "bundle",
                run_id="run-1",
                batches_dir=self._plan_dir(tmp_path, fits=False))
        assert "does not fit" in str(raised.value)

    def test_batch_lists_with_no_plan_are_refused(self, tmp_path):
        d = tmp_path / "plan"
        d.mkdir()
        (d / "batch_001.txt").write_text("SRR1\n")
        with pytest.raises(SystemExit) as raised:
            handoff.generate_bundle(_cfg(), self._sra(tmp_path),
                                    tmp_path / "bundle", run_id="run-1",
                                    batches_dir=d)
        assert "no plan.json" in str(raised.value)


class TestFindingSamplesOnADisk:

    def test_a_fastq_is_not_read_as_a_sample(self, tmp_path):
        """`Path.stem` on `x_R1_001.fastq.gz` names a sample nothing can find."""
        (tmp_path / "SRR9_S1_L001_R1_001.fastq.gz").write_bytes(b"x")
        (tmp_path / "SRR9_S1_L001_R2_001.fastq.gz").write_bytes(b"x")
        assert pb.find_samples(tmp_path) == []

    def test_a_directory_and_a_leftover_archive_are_one_sample(self, tmp_path):
        both = tmp_path / "SRR1"
        both.mkdir()
        (both / "SRR1.sra").write_bytes(b"x" * 300)
        (tmp_path / "SRR1.sra").write_bytes(b"x" * 300)
        assert pb.find_samples(tmp_path) == [("SRR1", 300)]

    def test_a_link_and_its_target_are_one_sample(self, tmp_path):
        (tmp_path / "PLAIN.sra").write_bytes(b"x" * 400)
        (tmp_path / "LINK.sra").symlink_to(tmp_path / "PLAIN.sra")
        found = pb.find_samples(tmp_path)
        assert len(found) == 1 and found[0][1] == 400

    def test_an_empty_sample_directory_is_kept_not_dropped(self, tmp_path):
        """Broken input that vanishes from the plan is never chased down."""
        (tmp_path / "SRR_EMPTY").mkdir()
        (tmp_path / "SRR_OK.sra").write_bytes(b"x" * 10)
        assert dict(pb.find_samples(tmp_path)) == {"SRR_EMPTY": 0, "SRR_OK": 10}


class TestRetrievalRunAsShell:

    def test_only_with_no_mode_is_a_usage_error_not_a_shell_error(self, tmp_path):
        script = tmp_path / "retrieve.sh"
        script.write_text(handoff.render_retrieve(_cfg(), "out"))
        done = subprocess.run(["bash", str(script), "--only"],
                              capture_output=True, text=True)
        assert done.returncode == 2
        assert "needs a mode" in done.stderr
        assert "unbound variable" not in done.stderr

    def test_an_unknown_mode_is_refused(self, tmp_path):
        script = tmp_path / "retrieve.sh"
        script.write_text(handoff.render_retrieve(_cfg(), "out"))
        done = subprocess.run(["bash", str(script), "--only", "everything"],
                              capture_output=True, text=True)
        assert done.returncode == 2

    def test_summary_brings_the_receipt_so_the_tree_can_be_reverified(self):
        """Without it a released sample reads as output that went missing."""
        assert "--include=.genoar_cellranger.json" in \
            handoff.RETRIEVE_FILTERS["summary"]


class TestTheToolNeverShowsAnOperatorATraceback:

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(HPC / "plan_batches.py")] + [str(a) for a in args],
            capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def test_a_config_path_that_does_not_exist(self, tmp_path):
        sra = tmp_path / "sra" / "SRR1"
        sra.mkdir(parents=True)
        (sra / "SRR1.sra").write_bytes(b"x")
        done = self._run("--input-dir", tmp_path / "sra", "--out",
                         tmp_path / "plan", "--config", tmp_path / "nope.yaml",
                         "--non-interactive")
        assert done.returncode == 2
        assert "Traceback" not in done.stderr
        assert "hpc_config.example.yaml" in done.stderr

    def test_a_config_that_is_not_yaml(self, tmp_path):
        sra = tmp_path / "sra" / "SRR1"
        sra.mkdir(parents=True)
        (sra / "SRR1.sra").write_bytes(b"x")
        bad = tmp_path / "bad.yaml"
        bad.write_text("walltime: [unclosed\n")
        done = self._run("--input-dir", tmp_path / "sra", "--out",
                         tmp_path / "plan", "--config", bad, "--non-interactive")
        assert done.returncode == 2
        assert "Traceback" not in done.stderr


class TestThePlanIsAboutParticularFiles:
    """A list of names is not the corpus the disk figure was worked out from."""

    def _sra(self, tmp_path, size=1000):
        sra = tmp_path / "sra" / "SRR1"
        sra.mkdir(parents=True)
        (sra / "SRR1.sra").write_bytes(b"x" * size)
        return tmp_path / "sra"

    def _plan_dir(self, tmp_path, planned_size=1000):
        d = tmp_path / "plan"
        d.mkdir(exist_ok=True)
        (d / "batch_001.txt").write_text("SRR1\n")
        (d / "plan.json").write_text(json.dumps({
            "kind": "genoar.batch.plan", "plan_id": "plan-abc123",
            "samples": 1, "sample_names": ["SRR1"],
            "sample_bytes": {"SRR1": planned_size},
            "settings": {"retain": "all", "release_input": False,
                         "pipeline_mode": "corrected", "disk_gb": 1000,
                         "walltime": 1440},
            "plan": {"fits": True, "peak_gb": 1.0}}))
        return d

    def test_inputs_that_match_the_plan_are_accepted(self, tmp_path):
        out = handoff.generate_bundle(
            _cfg(), self._sra(tmp_path), tmp_path / "bundle", run_id="run-1",
            batches_dir=self._plan_dir(tmp_path))
        assert (out / "batches" / "batch_001.txt").exists()

    def test_inputs_that_grew_since_the_plan_are_refused(self, tmp_path):
        """The same names re-downloaded larger kept the plan valid."""
        with pytest.raises(SystemExit) as raised:
            handoff.generate_bundle(
                _cfg(), self._sra(tmp_path, size=10_000),
                tmp_path / "bundle", run_id="run-1",
                batches_dir=self._plan_dir(tmp_path, planned_size=1000))
        assert "not the ones" in str(raised.value)

    def test_an_input_that_is_gone_is_refused(self, tmp_path):
        sra = self._sra(tmp_path)
        plan = self._plan_dir(tmp_path)
        (plan / "plan.json").write_text(json.dumps({
            "plan_id": "p", "sample_names": ["SRR1", "SRR_GONE"],
            "sample_bytes": {"SRR1": 1000, "SRR_GONE": 500},
            "settings": {"retain": "all", "release_input": False},
            "plan": {"fits": True}}))
        with pytest.raises(SystemExit) as raised:
            handoff.generate_bundle(_cfg(), sra, tmp_path / "bundle",
                                    run_id="run-1", batches_dir=plan)
        assert "SRR_GONE" in str(raised.value)

    def test_the_plan_id_changes_when_the_inputs_do(self, tmp_path):
        import subprocess as sp
        ids = []
        for size in (1_000_000, 9_000_000):
            sra = tmp_path / ("sra%d" % size) / "SRR1"
            sra.mkdir(parents=True)
            (sra / "SRR1.sra").write_bytes(b"x" * size)
            out = tmp_path / ("plan%d" % size)
            sp.run([sys.executable, str(HPC / "plan_batches.py"),
                    "--input-dir", str(sra.parent), "--out", str(out),
                    "--non-interactive", "--walltime", "24:00:00",
                    "--minutes-per-gb", "2.9", "--disk-gb", "500"],
                   capture_output=True, text=True, check=True)
            ids.append(json.loads((out / "plan.json").read_text())["plan_id"])
        assert ids[0] != ids[1]
