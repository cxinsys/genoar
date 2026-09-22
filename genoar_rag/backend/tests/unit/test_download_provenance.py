"""What the service serves, and what it says, per provenance receipt.

Stage 3 leaves a receipt beside each sample's Cell Ranger output naming what
produced it. Before receipts existed the service answered "was this run
processed?" by looking for the file, which meant it handed out output an
operator had adopted on trust exactly as it handed out verified work. These
tests fix the four cases: a `fresh` receipt, an `adopted` receipt, a receipt the
service cannot act on, and no receipt at all.

The last of those is the deployed case. Servers hold results produced before any
receipt was written, and making those vanish would be a regression however good
the provenance argument, so the no-receipt case is tested under both settings of
PROCESSED_RESULTS_REQUIRE_RECEIPT.
"""

import json

import pytest

from app.config import settings
from app.services import download_service

OUTS = "cellranger_output/outs"
MATRIX = "filtered_feature_bc_matrix.h5"


def write_receipt(run_dir, **fields):
    """A receipt in the shape run_docker_pipeline.sh writes one."""
    payload = {
        "sample": run_dir.name,
        "run_id": "run-2026-01-02",
        "input_fingerprint": "d0e1f2",
        "bam_size": 4096,
        "bam_mtime": 1767225600,
        "completed_at": "2026-01-02T03:04:05Z",
    }
    payload.update(fields)
    (run_dir / download_service.RECEIPT_NAME).write_text(json.dumps(payload))
    return payload


@pytest.fixture
def results(tmp_path, monkeypatch):
    """A results directory holding Cell Ranger output for SRR001 and no receipt.

    This is a deployment that predates provenance tracking: files, and nothing
    beside them saying where they came from.
    """
    outs = tmp_path / "SRR001" / OUTS
    outs.mkdir(parents=True)
    (outs / MATRIX).write_bytes(b"x" * 2048)
    (outs / "raw_feature_bc_matrix.h5").write_bytes(b"y" * 4096)
    monkeypatch.setattr(settings, "processed_results_dir", str(tmp_path))
    monkeypatch.setattr(settings, "processed_h5_url_template", "")
    monkeypatch.setattr(settings, "processed_results_layouts", "")
    monkeypatch.setattr(settings, "processed_results_require_receipt", False)
    return tmp_path


@pytest.fixture
def strict(monkeypatch):
    """The deployment that requires a receipt."""
    monkeypatch.setattr(settings, "processed_results_require_receipt", True)


def _by_kind(sources):
    return {s.kind: s for s in sources}


def _processed(db_conn, run_id="SRR001"):
    return _by_kind(download_service.build_sources(db_conn, run_id))["processed_h5"]


class TestVerified:
    """A receipt saying `fresh`: a run ran Cell Ranger over this input and
    recorded that it did. This is the only thing the pipeline counts as its own
    work, and it is the only thing that is offered without qualification."""

    @pytest.fixture(autouse=True)
    def receipt(self, results):
        return write_receipt(results / "SRR001", provenance="fresh")

    def test_the_output_is_offered(self, db_conn, results):
        h5 = _processed(db_conn)
        assert h5.available
        assert h5.url == f"/api/v1/samples/SRR001/files/{MATRIX}"

    def test_the_source_says_it_is_verified(self, db_conn, results):
        assert _processed(db_conn).provenance == "verified"

    def test_every_output_in_the_directory_is_offered(self, db_conn, results):
        sources = _by_kind(download_service.build_sources(db_conn, "SRR001"))
        assert {"processed_h5", "raw_h5"} <= set(sources)
        assert all(sources[k].available for k in ("processed_h5", "raw_h5"))

    def test_the_file_resolves(self, results):
        assert download_service.processed_file_path("SRR001", MATRIX) is not None

    def test_provenance_names_the_run_that_did_the_work(self, results):
        prov = download_service.sample_provenance("SRR001")
        assert prov.status == "verified"
        assert prov.verified is True
        assert prov.offered is True
        assert prov.receipt_run_id == "run-2026-01-02"
        assert prov.recorded_at == "2026-01-02T03:04:05Z"

    def test_requiring_a_receipt_changes_nothing_for_it(self, results, strict):
        """The strict deployment is stricter about output with no receipt. It
        has nothing to say about output that has one saying `fresh`."""
        prov = download_service.sample_provenance("SRR001")
        assert prov.offered is True
        assert download_service.processed_file_path("SRR001", MATRIX) is not None


class TestAdopted:
    """A receipt saying `adopted`: an operator told a run to reuse output nobody
    could account for. The pipeline reports it and counts it towards nothing.
    The service withholds it, under either setting of the switch, because
    adoption is what lets a run proceed and has never been a finding about the
    output."""

    @pytest.fixture(autouse=True)
    def receipt(self, results):
        return write_receipt(results / "SRR001", provenance="adopted")

    def test_the_output_is_not_offered(self, db_conn, results):
        h5 = _processed(db_conn)
        assert h5.available is False
        assert h5.url is None

    def test_the_source_says_why(self, db_conn, results):
        h5 = _processed(db_conn)
        assert h5.provenance == "adopted"
        assert h5.note == download_service.ADOPTED_NOTE
        assert "GENOAR_ADOPT_PRIOR_RESULTS=1" in h5.note

    def test_it_is_reported_as_output_rather_than_as_absence(self, db_conn, results):
        """An adopted sample has output. Reporting it as unprocessed would hide
        a state the operator put the deployment in on purpose."""
        assert _processed(db_conn).note != download_service.NOT_PROCESSED_NOTE

    def test_no_other_output_in_the_directory_is_offered_either(
        self, db_conn, results
    ):
        sources = _by_kind(download_service.build_sources(db_conn, "SRR001"))
        assert sources["raw_h5"].available is False
        assert sources["raw_h5"].url is None

    def test_the_file_is_not_reachable_by_name(self, results):
        """The manifest withholding a URL is worth nothing if typing the URL
        still works."""
        assert download_service.processed_file_path("SRR001", MATRIX) is None

    def test_the_switch_does_not_rescue_it(self, results, monkeypatch):
        """PROCESSED_RESULTS_REQUIRE_RECEIPT is about output with no receipt.
        Leaving it off is not permission to serve an adoption."""
        monkeypatch.setattr(settings, "processed_results_require_receipt", False)
        assert download_service.sample_provenance("SRR001").offered is False

    def test_it_stays_withheld_under_the_strict_setting(self, results, strict):
        assert download_service.sample_provenance("SRR001").offered is False

    def test_provenance_names_the_run_that_adopted_it(self, results):
        prov = download_service.sample_provenance("SRR001")
        assert prov.status == "adopted"
        assert prov.verified is False
        assert prov.receipt_run_id == "run-2026-01-02"


class TestUnreadableReceipt:
    """A receipt this service cannot act on. Withheld, because a guess at what
    it means could credit an adoption as verified work, which is the one
    mistake the receipt exists to prevent."""

    def test_a_receipt_that_does_not_parse(self, db_conn, results):
        (results / "SRR001" / download_service.RECEIPT_NAME).write_text("{not json")
        h5 = _processed(db_conn)
        assert h5.available is False
        assert h5.provenance == "unreadable"
        assert h5.note == download_service.UNREADABLE_RECEIPT_NOTE

    def test_a_receipt_that_is_not_a_record(self, results):
        (results / "SRR001" / download_service.RECEIPT_NAME).write_text("[1, 2]")
        assert download_service.sample_provenance("SRR001").status == "unreadable"

    def test_a_receipt_with_no_provenance_field(self, results):
        write_receipt(results / "SRR001")
        prov = download_service.sample_provenance("SRR001")
        assert prov.status == "unreadable"
        assert prov.offered is False

    def test_a_receipt_naming_a_provenance_this_reader_does_not_know(self, results):
        """A word whose meaning is guessed at is worth less than no word."""
        write_receipt(results / "SRR001", provenance="cache_hit")
        assert download_service.sample_provenance("SRR001").status == "unreadable"

    def test_it_is_distinct_from_having_no_receipt(self, results):
        """Absence is explained by history. A receipt that says nothing usable
        is a receipt-era deployment with something wrong beside the output, so
        it is withheld with no switch to soften it."""
        write_receipt(results / "SRR001", provenance="cache_hit")
        assert download_service.sample_provenance("SRR001").offered is False
        (results / "SRR001" / download_service.RECEIPT_NAME).unlink()
        assert download_service.sample_provenance("SRR001").offered is True

    def test_the_file_is_not_reachable_by_name(self, results):
        write_receipt(results / "SRR001", provenance="cache_hit")
        assert download_service.processed_file_path("SRR001", MATRIX) is None

    def test_a_run_id_of_the_wrong_type_does_not_break_the_page(self, results):
        """A receipt is a file, and a field in it can hold any JSON value."""
        write_receipt(results / "SRR001", provenance="fresh", run_id=7)
        prov = download_service.sample_provenance("SRR001")
        assert prov.status == "verified"
        assert prov.receipt_run_id is None


class TestNoReceipt:
    """Output produced before the pipeline recorded provenance. This is what the
    deployed servers hold, so the default has to keep serving it."""

    def test_it_is_offered_by_default(self, db_conn, results):
        h5 = _processed(db_conn)
        assert h5.available is True
        assert h5.url == f"/api/v1/samples/SRR001/files/{MATRIX}"

    def test_the_default_response_looks_exactly_as_it_did(self, db_conn, results):
        """The file, its size and its URL, with no note attached. A legacy
        deployment's download panel is unchanged."""
        h5 = _processed(db_conn)
        assert h5.size_bytes == 2048
        assert h5.note is None

    def test_it_says_so_in_the_provenance(self, db_conn, results):
        prov = download_service.sample_provenance("SRR001")
        assert prov.status == "unrecorded"
        assert prov.verified is False
        assert prov.offered is True
        assert prov.receipt_run_id is None
        assert "PROCESSED_RESULTS_REQUIRE_RECEIPT" in prov.note

    def test_the_source_carries_the_status_too(self, db_conn, results):
        assert _processed(db_conn).provenance == "unrecorded"

    def test_the_file_resolves_by_default(self, results):
        assert download_service.processed_file_path("SRR001", MATRIX) is not None

    def test_a_strict_deployment_withholds_it(self, db_conn, results, strict):
        h5 = _processed(db_conn)
        assert h5.available is False
        assert h5.provenance == "unrecorded"
        assert h5.note == download_service.UNRECORDED_WITHHELD_NOTE

    def test_a_strict_deployment_does_not_serve_it_by_name(self, results, strict):
        assert download_service.processed_file_path("SRR001", MATRIX) is None

    def test_the_two_settings_differ_only_in_what_is_served(self, results, monkeypatch):
        """The reading of the output is the same either way. What the deployment
        does about that reading is the setting."""
        monkeypatch.setattr(settings, "processed_results_require_receipt", False)
        lenient = download_service.sample_provenance("SRR001")
        monkeypatch.setattr(settings, "processed_results_require_receipt", True)
        strict = download_service.sample_provenance("SRR001")
        assert lenient.status == strict.status == "unrecorded"
        assert (lenient.offered, strict.offered) == (True, False)


class TestNothingToHaveProvenanceAbout:
    def test_a_run_with_no_output_has_none(self, results):
        assert download_service.sample_provenance("SRR002") is None

    def test_and_is_still_reported_as_unprocessed(self, db_conn, results):
        h5 = _processed(db_conn, "SRR002")
        assert h5.available is False
        assert h5.note == download_service.NOT_PROCESSED_NOTE
        assert h5.provenance is None

    def test_a_strict_deployment_reports_it_the_same_way(self, db_conn, results, strict):
        """No output is no output. The switch is about output that exists."""
        assert _processed(db_conn, "SRR002").note == download_service.NOT_PROCESSED_NOTE

    def test_no_results_directory_means_no_provenance(self, monkeypatch):
        monkeypatch.setattr(settings, "processed_results_dir", "")
        assert download_service.sample_provenance("SRR001") is None

    def test_an_empty_results_directory_still_disables_the_download(
        self, db_conn, monkeypatch
    ):
        """The preserved case: nothing configured, nothing offered, and the
        switch has no bearing on it."""
        monkeypatch.setattr(settings, "processed_results_dir", "")
        monkeypatch.setattr(settings, "processed_h5_url_template", "")
        monkeypatch.setattr(settings, "processed_results_require_receipt", True)
        h5 = _processed(db_conn)
        assert h5.available is False
        assert "PROCESSED_RESULTS_DIR" in h5.note

    def test_files_on_another_host_are_unaffected(self, db_conn, monkeypatch):
        """Nothing about a remote file can be checked, so nothing is claimed and
        nothing is withheld."""
        monkeypatch.setattr(settings, "processed_results_dir", "")
        monkeypatch.setattr(
            settings, "processed_h5_url_template", "https://data.example.org/{run_id}.h5"
        )
        monkeypatch.setattr(settings, "processed_results_require_receipt", True)
        h5 = _processed(db_conn)
        assert h5.available is True
        assert h5.provenance is None


class TestWhereTheReceiptIsLookedFor:
    def test_in_the_run_directory_stage_3_writes_it_in(self, results):
        """Beside `cellranger_output`, one level above `outs`."""
        write_receipt(results / "SRR001", provenance="adopted")
        assert download_service.sample_provenance("SRR001").status == "adopted"

    def test_in_a_configured_layout_the_run_directory_is_still_the_run_directory(
        self, tmp_path, monkeypatch
    ):
        run_dir = tmp_path / "HS" / "SRR900"
        outs = run_dir / OUTS
        outs.mkdir(parents=True)
        (outs / MATRIX).write_bytes(b"h5")
        monkeypatch.setattr(settings, "processed_results_dir", str(tmp_path))
        monkeypatch.setattr(
            settings, "processed_results_layouts", "HS/{run_id}/" + OUTS
        )
        monkeypatch.setattr(settings, "processed_results_require_receipt", False)
        write_receipt(run_dir, provenance="fresh")

        assert download_service.sample_provenance("SRR900").status == "verified"

    def test_a_flat_layout_finds_the_receipt_beside_the_files(
        self, tmp_path, monkeypatch
    ):
        """Where the run directory holds the outputs directly, both places to
        look are the same place."""
        run_dir = tmp_path / "MM" / "SRR901"
        run_dir.mkdir(parents=True)
        (run_dir / MATRIX).write_bytes(b"h5")
        monkeypatch.setattr(settings, "processed_results_dir", str(tmp_path))
        monkeypatch.setattr(settings, "processed_results_layouts", "MM/{run_id}")
        monkeypatch.setattr(settings, "processed_results_require_receipt", False)
        write_receipt(run_dir, provenance="adopted")

        assert download_service.sample_provenance("SRR901").status == "adopted"

    def test_a_receipt_carried_along_beside_the_outputs_is_read(self, results):
        """A deployment that copied `outs/` somewhere may have brought the
        receipt with it."""
        write_receipt(results / "SRR001" / OUTS, provenance="adopted")
        assert download_service.sample_provenance("SRR001").status == "adopted"

    def test_the_run_directory_wins_where_both_exist(self, results):
        """The pipeline writes one place. A copy is a copy."""
        write_receipt(results / "SRR001", provenance="adopted")
        write_receipt(results / "SRR001" / OUTS, provenance="fresh")
        assert download_service.sample_provenance("SRR001").status == "adopted"

    def test_a_layout_reaching_outside_the_results_directory_reads_nothing(
        self, tmp_path, monkeypatch
    ):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / MATRIX).write_bytes(b"h5")
        write_receipt(outside, provenance="fresh")
        root = tmp_path / "results"
        root.mkdir()
        monkeypatch.setattr(settings, "processed_results_dir", str(root))
        monkeypatch.setattr(settings, "processed_results_layouts", "../outside")
        monkeypatch.setattr(settings, "processed_results_require_receipt", False)

        assert download_service.sample_provenance("SRR900") is None
        assert download_service.processed_file_path("SRR900", MATRIX) is None


class TestTheResponse:
    def test_it_carries_the_provenance(self, db_conn, results):
        write_receipt(results / "SRR001", provenance="fresh")
        response = download_service.build_response(db_conn, "SRR001")
        assert response.provenance.status == "verified"
        assert response.provenance.verified is True

    def test_a_withheld_sample_still_says_what_it_holds(self, db_conn, results):
        """The caller is told the run has output and that GENOAR will not hand
        it over. Deciding for them is fine. Deciding silently is not."""
        write_receipt(results / "SRR001", provenance="adopted")
        response = download_service.build_response(db_conn, "SRR001")
        assert response.provenance.status == "adopted"
        assert response.provenance.offered is False
        assert response.provenance.receipt_run_id == "run-2026-01-02"

    def test_a_run_with_no_output_carries_none(self, db_conn, results):
        assert download_service.build_response(db_conn, "SRR002").provenance is None

    def test_the_archive_record_is_untouched_by_any_of_it(self, db_conn, results):
        """Provenance is about what GENOAR produced. Where the raw data is
        deposited is true whatever the receipt says."""
        write_receipt(results / "SRR001", provenance="adopted")
        record = _by_kind(download_service.build_sources(db_conn, "SRR001"))["geo_record"]
        assert record.available is True
        assert record.provenance is None


class TestNamingWhatIsWithheld:
    """What the withheld rows say the run holds.

    Withholding is settled elsewhere and none of it changes here. The question
    is whether the list names the files that are actually on disk. It used to
    answer with one row headed as the preprocessed matrix whatever was there, so
    a run holding three files understated what it was keeping back and a run
    holding only a raw matrix named a file it does not have.
    """

    def _withheld(self, db_conn, run_id):
        return [
            s
            for s in download_service.build_sources(db_conn, run_id)
            if s.provenance and not s.available
        ]

    def test_a_run_holding_all_three_names_all_three(self, db_conn, results):
        outs = results / "SRR001" / OUTS
        (outs / "molecule_info.h5").write_bytes(b"z" * 8192)
        write_receipt(results / "SRR001", provenance="adopted")

        sources = self._withheld(db_conn, "SRR001")

        assert [s.kind for s in sources] == [
            "processed_h5",
            "raw_h5",
            "molecule_info_h5",
        ]
        assert [s.description for s in sources] == [d for _, _, d in download_service.PROCESSED_FILES]

    def test_each_of_them_carries_the_note_and_the_status(self, db_conn, results):
        (results / "SRR001" / OUTS / "molecule_info.h5").write_bytes(b"z")
        write_receipt(results / "SRR001", provenance="adopted")

        sources = self._withheld(db_conn, "SRR001")

        assert len(sources) == 3
        assert all(s.note == download_service.ADOPTED_NOTE for s in sources)
        assert all(s.provenance == "adopted" for s in sources)
        assert all(s.url is None for s in sources)

    def test_a_run_holding_only_a_raw_matrix_names_the_raw_matrix(
        self, db_conn, results
    ):
        """The row a caller was given here named the preprocessed matrix, which
        this run has never had."""
        outs = results / "SRR003" / OUTS
        outs.mkdir(parents=True)
        (outs / "raw_feature_bc_matrix.h5").write_bytes(b"y" * 4096)
        write_receipt(results / "SRR003", provenance="adopted")

        sources = self._withheld(db_conn, "SRR003")

        assert [s.kind for s in sources] == ["raw_h5"]
        assert sources[0].description == download_service.PROCESSED_FILES[1][2]
        assert sources[0].note == download_service.ADOPTED_NOTE
        assert sources[0].provenance == "adopted"

    def test_the_unreadable_case_is_enumerated_the_same_way(self, db_conn, results):
        """Withholding has more than one reason. The list is built from disk in
        every one of them."""
        write_receipt(results / "SRR001", provenance="cache_hit")

        sources = self._withheld(db_conn, "SRR001")

        assert [s.kind for s in sources] == ["processed_h5", "raw_h5"]
        assert all(s.note == download_service.UNREADABLE_RECEIPT_NOTE for s in sources)

    def test_a_strict_deployment_enumerates_what_it_holds_back(
        self, db_conn, results, strict
    ):
        sources = self._withheld(db_conn, "SRR001")

        assert [s.kind for s in sources] == ["processed_h5", "raw_h5"]
        assert all(s.provenance == "unrecorded" for s in sources)

    def test_the_offered_list_is_unchanged(self, db_conn, results):
        """The branch this was copied from. Same files, same order, still with
        their URLs and sizes."""
        write_receipt(results / "SRR001", provenance="fresh")

        sources = _by_kind(download_service.build_sources(db_conn, "SRR001"))

        assert sources["processed_h5"].url == f"/api/v1/samples/SRR001/files/{MATRIX}"
        assert sources["processed_h5"].size_bytes == 2048
        assert sources["raw_h5"].size_bytes == 4096
        assert all(sources[k].available for k in ("processed_h5", "raw_h5"))

    def test_the_never_processed_row_still_stands_alone(self, db_conn, results):
        """Nothing on disk to enumerate, so the one row stays. It carries no
        provenance, which is how a caller tells it from a withheld run."""
        sources = [
            s
            for s in download_service.build_sources(db_conn, "SRR002")
            if s.kind in {k for k, _, _ in download_service.PROCESSED_FILES}
        ]
        assert [s.kind for s in sources] == ["processed_h5"]
        assert sources[0].provenance is None
        assert sources[0].note == download_service.NOT_PROCESSED_NOTE


class TestWithheldOutputThatCannotBeResolved:
    """Provenance is decided only where output was found, so a withheld run with
    no resolvable file should not happen. It has one way to happen anyway: the
    provenance read checks containment on the outputs directory and the file
    read checks it on the file, so a file symlinked out of the results directory
    passes the first and fails the second. Output removed mid-request does the
    same. Reporting nothing would drop the withholding out of the list, so the
    row stays under the preferred output's name."""

    @pytest.fixture
    def escaping(self, tmp_path, monkeypatch):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (outside / MATRIX).write_bytes(b"x" * 2048)
        root = tmp_path / "results"
        outs = root / "SRR001" / OUTS
        outs.mkdir(parents=True)
        (outs / MATRIX).symlink_to(outside / MATRIX)
        monkeypatch.setattr(settings, "processed_results_dir", str(root))
        monkeypatch.setattr(settings, "processed_h5_url_template", "")
        monkeypatch.setattr(settings, "processed_results_layouts", "")
        monkeypatch.setattr(settings, "processed_results_require_receipt", False)
        write_receipt(root / "SRR001", provenance="adopted")
        return root

    def test_the_case_is_reachable(self, escaping):
        assert download_service.sample_provenance("SRR001").status == "adopted"
        assert download_service._locate("SRR001", MATRIX) is None

    def test_one_row_still_says_the_output_is_withheld(self, db_conn, escaping):
        h5 = _by_kind(download_service.build_sources(db_conn, "SRR001"))["processed_h5"]
        assert h5.available is False
        assert h5.url is None
        assert h5.provenance == "adopted"
        assert h5.note == download_service.ADOPTED_NOTE

    def test_it_is_not_reported_as_never_processed(self, db_conn, escaping):
        h5 = _processed(db_conn)
        assert h5.note != download_service.NOT_PROCESSED_NOTE

    def test_it_is_logged(self, db_conn, escaping, caplog):
        with caplog.at_level("WARNING"):
            download_service.build_sources(db_conn, "SRR001")
        assert "no output this service can resolve" in caplog.text


class TestDescribingWhatIsNotOffered:
    """Parameters are read out of the matrix. A matrix the service will not hand
    over is one it should not describe either, in the sample page or in the
    export's preprocessing columns."""

    @pytest.fixture
    def matrix(self, tmp_path, monkeypatch):
        h5py = pytest.importorskip("h5py")
        outs = tmp_path / "SRR001" / OUTS
        outs.mkdir(parents=True)
        with h5py.File(outs / MATRIX, "w") as f:
            f.attrs["software_version"] = "cellranger-8.0.1"
            f.attrs["chemistry_description"] = "Single Cell 3' v2"
            f.create_dataset("matrix/features/genome", data=[b"GRCh38"] * 3)
        monkeypatch.setattr(settings, "processed_results_dir", str(tmp_path))
        monkeypatch.setattr(settings, "processed_h5_url_template", "")
        monkeypatch.setattr(settings, "processed_results_layouts", "")
        monkeypatch.setattr(settings, "processed_results_require_receipt", False)
        return tmp_path

    def test_verified_output_is_described(self, matrix):
        write_receipt(matrix / "SRR001", provenance="fresh")
        assert download_service.processing_parameters("SRR001").software == "cellranger-8.0.1"

    def test_adopted_output_is_not(self, matrix):
        write_receipt(matrix / "SRR001", provenance="adopted")
        assert download_service.processing_parameters("SRR001") is None

    def test_legacy_output_is_described_by_default(self, matrix):
        assert download_service.processing_parameters("SRR001") is not None

    def test_and_is_not_on_a_strict_deployment(self, matrix, strict):
        assert download_service.processing_parameters("SRR001") is None
