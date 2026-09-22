"""Cell Ranger differs between releases, and installations differ in shape.

Both cost a K-BDS pilot run: the pipeline handed `--create-bam` to a 7.0.1
that has no such argument, and mounted a `bin/` directory whose parent held
the `.env.json` Cell Ranger reads at startup. These pin the behaviour that
came out of that.
"""

import importlib.util
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cycle_test.utils.stage3_runner import (  # noqa: E402
    VALIDATED_CELLRANGER_VERSION,
    VALIDATED_REFERENCE_VERSION,
    _cellranger_bin_in_container,
    _cellranger_version_key,
    _reference_release_key,
    cellranger_binary,
    normalize_cellranger_root,
    validate_cellranger,
)

SNAKEFILE = REPO / "srr_pipeline_package" / "pipeline_next" / "Snakefile_hs.smk"
RUN_SH = REPO / "srr_pipeline_package" / "pipeline_next" / "run_docker_pipeline.sh"
HANDOFF = REPO / "srr_pipeline_package" / "hpc" / "prepare_hpc_handoff.py"


def _load_handoff_module():
    """Import prepare_hpc_handoff.py, which lives outside any package."""
    spec = importlib.util.spec_from_file_location("prepare_hpc_handoff", HANDOFF)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_flag_helpers(cellranger_bin):
    """Exec the version helpers out of the Snakefile, which is not importable."""
    source = SNAKEFILE.read_text()
    block = re.search(r"CELLRANGER_VERSION_RE = .*?(?=\nrule )", source, re.S)
    assert block, "the version helpers are gone from the Snakefile"
    namespace = {"cellranger_bin": str(cellranger_bin), "re": re,
                 "subprocess": subprocess}
    exec(block.group(0), namespace)  # noqa: S102 - reading our own source
    # The block is cut at the first `rule `, so a rule inserted between the two
    # helpers would silently take one of them out of the test rather than fail
    # it. Both have to be here.
    for name in ("cellranger_version", "cellranger_bam_flag"):
        assert name in namespace, (
            f"{name} was not in the extracted block — something now sits "
            f"between the helpers and this test stopped covering it")
    return namespace


def _fake_cellranger(directory: Path, version: str, body: str = None) -> Path:
    """A launcher that answers --version. `body` overrides how it answers.

    The default is 8.x's shape. Real installations differ — 7.x builds print
    `cellranger 7.0.1`, module wrappers announce themselves first, and some put
    the whole line on stderr — so the cases that matter pass their own body.
    """
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "cellranger"
    if body is None:
        body = f'echo "cellranger cellranger-{version}"'
    binary.write_text(f"#!/bin/sh\n{body}\n")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return binary


class TestTheBamFlagFollowsTheInstalledVersion:
    """8.0 introduced --create-bam; 7.x refuses to start when handed it."""

    def test_seven_gets_no_flag(self, tmp_path):
        binary = _fake_cellranger(tmp_path / "cr7", "7.0.1")
        helpers = _load_flag_helpers(binary)
        assert helpers["cellranger_version"]() == "7.0.1"
        assert helpers["cellranger_bam_flag"]() == ""

    def test_eight_gets_the_flag(self, tmp_path):
        binary = _fake_cellranger(tmp_path / "cr8", "8.0.1")
        helpers = _load_flag_helpers(binary)
        assert helpers["cellranger_version"]() == "8.0.1"
        assert helpers["cellranger_bam_flag"]() == " --create-bam=true"

    def test_an_unreadable_version_stops_rather_than_guessing(self, tmp_path):
        """Both guesses cost the whole allocation, so neither is made.

        `--create-bam` on a 7.x dies at startup; omitting it on an 8.x runs the
        full count and then fails on a BAM that was never written. The old
        behaviour picked the first of those and called it the safe assumption.
        """
        helpers = _load_flag_helpers(tmp_path / "does-not-exist")
        assert helpers["cellranger_version"]() == ""
        with pytest.raises(RuntimeError) as raised:
            helpers["cellranger_bam_flag"]()
        assert "does-not-exist" in str(raised.value), (
            "the error has to name the binary it could not read")

    def test_a_seven_that_prints_the_bare_form_is_read(self, tmp_path):
        """Some 7.x builds print `cellranger 7.0.1`, not `cellranger-7.0.1`.

        Held rather than newly won: the old pattern read this too. It is here so
        that tightening the pattern around the word "cellranger" cannot quietly
        drop the form half the installations use.
        """
        binary = _fake_cellranger(tmp_path / "bare", "7.0.1",
                                  body='echo "cellranger 7.0.1"')
        helpers = _load_flag_helpers(binary)
        assert helpers["cellranger_version"]() == "7.0.1"
        assert helpers["cellranger_bam_flag"]() == ""

    def test_a_version_printed_on_stderr_is_read(self, tmp_path):
        """Reading stdout alone made a working install look like no install."""
        binary = _fake_cellranger(tmp_path / "onerr", "7.0.1",
                                  body='echo "cellranger cellranger-7.0.1" >&2')
        helpers = _load_flag_helpers(binary)
        assert helpers["cellranger_version"]() == "7.0.1"
        assert helpers["cellranger_bam_flag"]() == ""

    def test_a_wrapper_announcing_itself_does_not_become_the_version(self, tmp_path):
        """`Loading singularity/3.8.7` is not Cell Ranger 3.x.

        Taking the first three-part number anywhere in the output read that as
        the version, decided the install was pre-8, and omitted --create-bam on
        an 8.0.1 — which then writes no BAM and fails on a missing output.
        """
        binary = _fake_cellranger(
            tmp_path / "wrapped", "8.0.1",
            body='echo "Loading singularity/3.8.7"\necho "cellranger cellranger-8.0.1"')
        helpers = _load_flag_helpers(binary)
        assert helpers["cellranger_version"]() == "8.0.1"
        assert helpers["cellranger_bam_flag"]() == " --create-bam=true"

    def test_the_version_is_read_once_however_many_samples_there_are(self, tmp_path):
        """`bam_flag` is evaluated per job; --version is not run per job.

        Without this, an unreadable launcher on a cold filesystem spent the
        60s+180s retry ladder once per sample — hours of an allocation gone
        before the first count, and the retry was added to help.
        """
        counter = tmp_path / "calls"
        binary = _fake_cellranger(
            tmp_path / "counted", "8.0.1",
            body=f'echo x >> {counter}\necho "cellranger cellranger-8.0.1"')
        helpers = _load_flag_helpers(binary)
        for _ in range(5):
            assert helpers["cellranger_bam_flag"]() == " --create-bam=true"
        assert counter.read_text().count("x") == 1

    def test_the_flag_is_never_hardcoded_into_the_command(self):
        """The rule must ask for the flag, not spell it out."""
        source = SNAKEFILE.read_text()
        rule = source[source.index("rule cellranger_count:"):]
        assert "--create-bam" not in rule, (
            "the rule hardcodes --create-bam again; 7.x cannot run that"
        )
        assert "params.bam_flag" in rule


class TestAnInstallationIsItsWholeTree:
    """Cell Ranger reads .env.json beside the launcher, so the root is mounted."""

    def _install(self, root: Path, launcher_in_bin: bool) -> Path:
        target = root / "bin" if launcher_in_bin else root
        _fake_cellranger(target, "7.0.1")
        (root / ".env.json").write_text("{}")
        for name in ("external", "lib", "mro"):
            (root / name).mkdir(exist_ok=True)
        return root

    def test_pointing_at_bin_resolves_to_the_root(self, tmp_path):
        root = self._install(tmp_path / "cellranger-7.0.1", launcher_in_bin=True)
        assert normalize_cellranger_root(root / "bin") == root

    def test_pointing_at_the_root_stays_there(self, tmp_path):
        root = self._install(tmp_path / "cellranger-7.0.1", launcher_in_bin=True)
        assert normalize_cellranger_root(root) == root

    def test_the_launcher_is_found_in_either_layout(self, tmp_path):
        in_bin = self._install(tmp_path / "a", launcher_in_bin=True)
        at_root = self._install(tmp_path / "b", launcher_in_bin=False)
        assert cellranger_binary(in_bin) == in_bin / "bin" / "cellranger"
        assert cellranger_binary(at_root) == at_root / "cellranger"

    def test_the_container_path_follows_the_layout(self, tmp_path):
        in_bin = self._install(tmp_path / "a", launcher_in_bin=True)
        at_root = self._install(tmp_path / "b", launcher_in_bin=False)
        assert _cellranger_bin_in_container(in_bin / "bin") == "/opt/cellranger/bin/cellranger"
        assert _cellranger_bin_in_container(at_root) == "/opt/cellranger/cellranger"

    def test_validation_accepts_a_bin_directory(self, tmp_path):
        root = self._install(tmp_path / "cellranger-7.0.1", launcher_in_bin=True)
        assert validate_cellranger(root / "bin") == []

    def test_validation_rejects_a_directory_with_no_launcher(self, tmp_path):
        empty = tmp_path / "nothing"
        empty.mkdir()
        problems = validate_cellranger(empty)
        assert problems and "not found" in problems[0]

    def test_validation_rejects_a_launcher_with_no_installation_around_it(self, tmp_path):
        """A bin/ whose parent is not an installation either.

        normalize_cellranger_root hands back what it was given when it finds no
        .env.json anywhere, so validation cannot stop at "there is a launcher
        here": a module tree or symlink farm exposing only the binary would be
        declared valid, mounted, and then die at Cell Ranger startup on the
        .env.json nobody looked for.
        """
        loose = tmp_path / "modules" / "cellranger" / "bin"
        _fake_cellranger(loose, "8.0.1")
        problems = validate_cellranger(loose)
        assert problems, "a launcher with no installation around it is not valid"
        assert ".env.json" in problems[0]


def _newest(names, key):
    return sorted((Path(n) for n in names), key=key, reverse=True)[0].name


class TestTheNewestInstallWins:
    """Directory order sorts 7 before 8, and 10 before 7. Numbers must decide."""

    def test_versions_order_numerically(self):
        names = ["cellranger-7.0.1", "cellranger-8.0.1", "cellranger-10.0.0"]
        ordered = sorted((Path(n) for n in names),
                         key=_cellranger_version_key, reverse=True)
        assert [p.name for p in ordered] == [
            "cellranger-10.0.0", "cellranger-8.0.1", "cellranger-7.0.1"]

    def test_alphabetical_order_would_have_been_wrong(self):
        """The bug this replaces: plain sorting picks 7.0.1 over 8.0.1."""
        names = ["cellranger-7.0.1", "cellranger-8.0.1"]
        assert sorted(names)[0] == "cellranger-7.0.1"
        assert _newest(names, _cellranger_version_key) == "cellranger-8.0.1"

    def test_only_the_release_digits_count(self):
        """Numbers before `cellranger-` must not outrank the release.

        Reading every number in the directory name compared the leading one
        first, so a directory filed under the year it was fetched beat the
        newer release sitting beside it.
        """
        names = ["2024-cellranger-7.0.1", "cellranger-8.0.1"]
        assert _newest(names, _cellranger_version_key) == "cellranger-8.0.1"


class TestTheReferenceIsHumanFirst:
    """A reference for another species runs to completion and means nothing."""

    def test_releases_order_by_year(self):
        names = ["refdata-gex-GRCh38-2020-A", "refdata-gex-GRCh38-2024-A"]
        assert _newest(names, _reference_release_key) == "refdata-gex-GRCh38-2024-A", (
            "2020-A and 2024-A carry different gene annotations; the newer one "
            "is what the guide installs"
        )

    def test_a_mouse_reference_never_wins(self):
        """GRCm39 vs GRCh38: reading every number in the name, 39 beat 38.

        This is the human pipeline. Picking the mouse transcriptome does not
        fail — it produces a full result set that means nothing, with nothing
        in the log to say so, which is the worst shape a defect can take here.
        """
        names = ["refdata-gex-GRCh38-2024-A", "refdata-gex-GRCm39-2024-A"]
        assert _newest(names, _reference_release_key) == "refdata-gex-GRCh38-2024-A"

    def test_a_mouse_reference_never_wins_even_when_newer(self):
        names = ["refdata-gex-GRCh38-2020-A", "refdata-gex-GRCm39-2024-A"]
        assert _newest(names, _reference_release_key) == "refdata-gex-GRCh38-2020-A"

    def test_a_combined_reference_loses_to_a_human_only_one(self):
        """`refdata-gex-GRCh38-and-GRCm39-2024-A` names GRCh38 and is not it.

        10x ships it for barnyard experiments. Testing for GRCh38 alone called
        it human, so it beat a human-only reference of an earlier release —
        and counting against it puts about half the features behind a
        `GRCm39_` prefix, on a run that completes and reports numbers.
        """
        names = ["refdata-gex-GRCh38-2020-A", "refdata-gex-GRCh38-and-GRCm39-2024-A"]
        assert _newest(names, _reference_release_key) == "refdata-gex-GRCh38-2020-A"

    def test_a_combined_reference_still_beats_a_mouse_only_one(self):
        names = ["refdata-gex-GRCm39-2024-A", "refdata-gex-GRCh38-and-GRCm39-2024-A"]
        assert _newest(names, _reference_release_key) == "refdata-gex-GRCh38-and-GRCm39-2024-A"

    def test_the_release_letter_breaks_a_tie_within_one_year(self):
        """Same year, different rebuild: taking whichever glob returned first."""
        names = ["refdata-gex-GRCh38-2020-A", "refdata-gex-GRCh38-2020-B"]
        assert _newest(names, _reference_release_key) == "refdata-gex-GRCh38-2020-B"


class TestEveryGeneratedConfigStatesWhatItRanAgainst:
    """Step 0's comparison is only as real as the config that reaches it.

    `configs/example.yaml` carries both keys, and nothing copies that file. Every
    actual run gets its config from one of the two generators below, and while
    neither wrote the keys, step 0 read empty strings, skipped both comparisons,
    and printed nothing — on a 7.0.1 with 2020-A exactly as on the validated
    pair. The check existed and did not run, which is worse than not having it.
    """

    def test_the_runner_writes_both_keys(self, tmp_path):
        from cycle_test.utils.stage3_runner import Stage3PipelineRunner

        runner = Stage3PipelineRunner(
            sra_dir=tmp_path / "sra",
            results_dir=tmp_path / "run" / "results",
            logs_dir=tmp_path / "run" / "logs",
            cellranger_path=tmp_path / "cellranger",
            ref_genome_path=tmp_path / "ref",
        )
        config = yaml.safe_load(runner.generate_config().read_text())
        assert config["cellranger_version"] == VALIDATED_CELLRANGER_VERSION
        assert config["reference_version"] == VALIDATED_REFERENCE_VERSION

    def test_the_handoff_bundle_writes_both_keys(self):
        handoff = _load_handoff_module()
        cfg = {"cellranger_threads": 16, "cellranger_mem": 100}
        config = yaml.safe_load(handoff.render_config_yaml(cfg))
        assert config["cellranger_version"] == VALIDATED_CELLRANGER_VERSION
        assert config["reference_version"] == VALIDATED_REFERENCE_VERSION

    def test_a_site_can_state_its_own_pair(self):
        handoff = _load_handoff_module()
        cfg = {"cellranger_threads": 16, "cellranger_mem": 100,
               "cellranger_version": "7.0.1",
               "reference_version": "refdata-gex-GRCh38-2020-A"}
        config = yaml.safe_load(handoff.render_config_yaml(cfg))
        assert config["cellranger_version"] == "7.0.1"
        assert config["reference_version"] == "refdata-gex-GRCh38-2020-A"

    def test_the_two_definitions_of_the_validated_pair_agree(self):
        """The handoff script copies the pair rather than importing it.

        It is handed to sites on its own and has to run without the rest of the
        tree, so the copy is deliberate. This is what keeps the copy honest.
        """
        handoff = _load_handoff_module()
        assert handoff.DEFAULT_CELLRANGER_VERSION == VALIDATED_CELLRANGER_VERSION
        assert handoff.DEFAULT_REFERENCE_VERSION == VALIDATED_REFERENCE_VERSION


class TestStepZeroReadsTheReferenceItWasGiven:
    """The shell half of the same check, run as shell.

    Its reference lookup matched nothing on every input — the first grep's match
    ends in a quote and the second demanded a last character that was not one —
    so the genome was always empty, the "does it match" test was therefore
    always true, and the note fired for sites running exactly the reference it
    was checking for. A note that never stays quiet carries no information, and
    nothing in the suite could see it because nothing ran the shell.
    """

    def _step0_reference_check(self, tmp_path, reference_json: str, expected: str):
        """Run step 0's whole reference block, as shell, and read what it said.

        The block is taken entire — the lookup, the comparison and the echo —
        rather than the two assignment lines with the condition rewritten here.
        A test that re-implements the condition it is checking passes whatever
        the script does: the always-firing form this exists to catch stayed
        green under exactly that shape.
        """
        source = RUN_SH.read_text()
        start = source.index("  # Read the reference's own reference.json")
        end = source.index('  echo "[step0] OK: cellranger=')
        block = source[start:end]
        assert "REF_GENOME_MISMATCH" in block and "NOTE:" in block, (
            "step 0's reference block has moved or been rewritten")
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir(exist_ok=True)
        (ref_dir / "reference.json").write_text(reference_json)
        script = "\n".join([
            "set -euo pipefail",
            f"REF_PATH={shlex.quote(str(ref_dir))}",
            f"REFERENCE_VERSION_EXPECTED={shlex.quote(expected)}",
            block,
            'printf "GENOME=%s RELEASE=%s\\n" "${REF_GENOME_FOUND:-}" "${REF_RELEASE_FOUND:-}"',
        ])
        done = subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, check=True)
        note = "NOTE:" in done.stdout
        fields = dict(part.split("=", 1) for part in
                      done.stdout.strip().splitlines()[-1].split(" "))
        return fields["GENOME"], fields["RELEASE"], "fired" if note else "quiet"

    def test_the_genome_is_read_out_of_reference_json(self, tmp_path):
        genome, release, _ = self._step0_reference_check(
            tmp_path, '{\n  "genomes": ["GRCh38"],\n  "version": "2024-A"\n}\n',
            "refdata-gex-GRCh38-2024-A")
        assert (genome, release) == ("GRCh38", "2024-A")

    def test_the_validated_reference_draws_no_note(self, tmp_path):
        """The case the broken lookup could never produce."""
        _, _, note = self._step0_reference_check(
            tmp_path, '{"genomes": ["GRCh38"], "version": "2024-A"}',
            "refdata-gex-GRCh38-2024-A")
        assert note == "quiet"

    def test_a_different_release_draws_the_note(self, tmp_path):
        _, _, note = self._step0_reference_check(
            tmp_path, '{"genomes": ["GRCh38"], "version": "2020-A"}',
            "refdata-gex-GRCh38-2024-A")
        assert note == "fired"

    def test_a_different_species_draws_the_note(self, tmp_path):
        _, _, note = self._step0_reference_check(
            tmp_path, '{"genomes": ["GRCm39"], "version": "2024-A"}',
            "refdata-gex-GRCh38-2024-A")
        assert note == "fired"

    def test_a_combined_reference_draws_the_note(self, tmp_path):
        """Reading only the first entry of "genomes" saw GRCh38 and matched.

        The run then counts human reads against a human+mouse transcriptome
        and says nothing, which is the whole failure this check exists for.
        """
        genome, _, note = self._step0_reference_check(
            tmp_path, '{"genomes": ["GRCh38", "GRCm39"], "version": "2024-A"}',
            "refdata-gex-GRCh38-2024-A")
        assert genome == "GRCh38,GRCm39"
        assert note == "fired"

    def test_a_tab_indented_reference_json_still_reads(self, tmp_path):
        """Stripping only spaces and newlines left the tab inside the name."""
        _, _, note = self._step0_reference_check(
            tmp_path, '{\n\t"genomes": ["GRCh38"],\n\t"version": "2024-A"\n}\n',
            "refdata-gex-GRCh38-2024-A")
        assert note == "quiet"


class TestStepZeroComparesVersionsAtTheSameWidth:
    """`${1%.*}` left "8" as "8", which no three-part version reduces to."""

    def _cr_series(self, value: str) -> str:
        source = RUN_SH.read_text()
        block = re.search(r"^  cr_series\(\) \{.*?^  \}$", source,
                          re.S | re.M)
        assert block, "cr_series has moved out of step 0"
        script = "\n".join([block.group(0),
                             f"cr_series {shlex.quote(value)}"])
        done = subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, check=True)
        return done.stdout

    def test_a_full_version_reduces_to_major_minor(self):
        assert self._cr_series("8.0.1") == "8.0"
        assert self._cr_series("10.1.2") == "10.1"

    def test_a_short_version_is_filled_out_not_truncated(self):
        """`cellranger_version: "8"` on an 8.0.1 install is not a mismatch."""
        assert self._cr_series("8") == self._cr_series("8.0.1")
        assert self._cr_series("8.0") == self._cr_series("8.0.1")


class TestBothDetectorsNameTheSameVersion:
    """Step 0 logs one version; the rule adapts to another. They must be one.

    The shell must not merge the launcher's two streams with `2>&1`, which
    interleaves them as they were written, while the Snakefile reads stdout and
    then stderr. A `module load` wrapper announcing one version on stderr while
    the binary answers with another on stdout would make step 0 warn about a
    version the run never used — and the log is all anyone here gets back.
    """

    def _shell_version(self, binary: Path) -> str:
        source = RUN_SH.read_text()
        start = source.index("  CR_VERSION_CMD=(")
        end = source.index('  rm -f "$CR_VERSION_STDERR"') + len(
            '  rm -f "$CR_VERSION_STDERR"')
        block = source[start:end]
        script = "\n".join([
            "set -euo pipefail",
            f"CELLRANGER_BIN={shlex.quote(str(binary))}",
            block,
            'printf "%s" "$CELLRANGER_VERSION_FOUND"',
        ])
        done = subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, check=True)
        return done.stdout.strip()

    def _both(self, binary: Path):
        return (self._shell_version(binary),
                _load_flag_helpers(binary)["cellranger_version"]())

    def test_a_wrapper_announcing_another_version_on_stderr(self, tmp_path):
        """The case that split them: stderr says 7.0.1, the binary is 8.0.1."""
        binary = _fake_cellranger(
            tmp_path / "wrapper", "8.0.1",
            body='echo "Loading cellranger/7.0.1" >&2\necho "cellranger cellranger-8.0.1"')
        shell, python = self._both(binary)
        assert shell == python == "8.0.1"

    def test_a_version_only_on_stderr_is_found_by_both(self, tmp_path):
        binary = _fake_cellranger(
            tmp_path / "onerr", "7.0.1",
            body='echo "cellranger cellranger-7.0.1" >&2')
        shell, python = self._both(binary)
        assert shell == python == "7.0.1"

    def test_noise_on_stdout_fools_neither(self, tmp_path):
        binary = _fake_cellranger(
            tmp_path / "noisy", "8.0.1",
            body='echo "Loading singularity/3.8.7"\necho "cellranger cellranger-8.0.1"')
        shell, python = self._both(binary)
        assert shell == python == "8.0.1"

    def test_a_version_split_over_two_lines_is_read_by_neither(self, tmp_path):
        """grep cannot match across a newline, so the regex must not either."""
        binary = _fake_cellranger(tmp_path / "split", "8.0.1",
                                  body='printf "cellranger\\n8.0.1\\n"')
        shell, python = self._both(binary)
        assert shell == python == ""

    def test_a_silent_launcher_is_empty_for_both(self, tmp_path):
        binary = _fake_cellranger(tmp_path / "mute", "8.0.1", body="exit 0")
        shell, python = self._both(binary)
        assert shell == python == ""
