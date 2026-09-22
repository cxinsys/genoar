"""Data-access options per sample.

The raw reads are a link to the archive that holds them. The processed matrices
are read off the pipeline's output directory, so "was this run processed?" is
answered by the files being there rather than by a manifest — and so are the
parameters they were made with, which the matrices carry themselves.
"""

import pytest

from app.config import settings
from app.services import download_service

OUTS = "cellranger_output/outs"


@pytest.fixture
def results_dir(tmp_path, monkeypatch):
    """A results directory holding Cell Ranger output for SRR001 only."""
    outs = tmp_path / "SRR001" / OUTS
    outs.mkdir(parents=True)
    (outs / "filtered_feature_bc_matrix.h5").write_bytes(b"x" * 2048)
    (outs / "raw_feature_bc_matrix.h5").write_bytes(b"y" * 4096)
    (outs / "molecule_info.h5").write_bytes(b"z" * 8192)
    monkeypatch.setattr(settings, "processed_results_dir", str(tmp_path))
    monkeypatch.setattr(settings, "processed_h5_url_template", "")
    return tmp_path


@pytest.fixture
def no_processed_source(monkeypatch):
    monkeypatch.setattr(settings, "processed_results_dir", "")
    monkeypatch.setattr(settings, "processed_h5_url_template", "")


@pytest.fixture(params=["as_text", "as_bytes"])
def cellranger_results(request, tmp_path, monkeypatch):
    """A results directory whose matrix is a real HDF5, written the way Cell
    Ranger writes one: the version and the detected chemistry as root
    attributes, the reference repeated once per feature.

    Written twice, because a real file from cellranger-8.0.1 does not store
    these the way the obvious fixture does. Its root attributes come back as
    `str` and the genome as `numpy.bytes_`, where a fixture written by hand
    gives bytes for both. A reader that assumed either one would pass its tests
    and return nothing on the deployment, which is a failure with no symptom
    beyond a panel that is quietly empty.
    """
    h5py = pytest.importorskip("h5py")
    text = request.param == "as_text"

    outs = tmp_path / "SRR001" / OUTS
    outs.mkdir(parents=True)
    with h5py.File(outs / "filtered_feature_bc_matrix.h5", "w") as f:
        f.attrs["software_version"] = (
            "cellranger-8.0.1" if text else b"cellranger-8.0.1"
        )
        f.attrs["chemistry_description"] = (
            "Single Cell 3' v2" if text else b"Single Cell 3' v2"
        )
        # A dataset of variable-length bytes either way: this is how h5py hands
        # back the real file's, one entry per feature.
        f.create_dataset("matrix/features/genome", data=[b"GRCh38"] * 3)
    monkeypatch.setattr(settings, "processed_results_dir", str(tmp_path))
    monkeypatch.setattr(settings, "processed_h5_url_template", "")
    return tmp_path


def _by_kind(sources):
    return {s.kind: s for s in sources}


class TestSourceRecord:
    def test_links_to_the_geo_record_when_the_run_has_one(
        self, db_conn, no_processed_source
    ):
        record = _by_kind(download_service.build_sources(db_conn, "SRR001"))["geo_record"]
        assert record.available
        assert record.action == "visit"
        assert "GSM0001" in record.url
        # A page, not a file. A size beside it would say otherwise.
        assert record.size_bytes is None

    def test_falls_back_to_the_sra_record_without_a_gsm(
        self, db_conn, no_processed_source
    ):
        # SRR003 deliberately carries no GEO accession, as most of the corpus does not.
        sources = _by_kind(download_service.build_sources(db_conn, "SRR003"))
        assert "geo_record" not in sources
        assert sources["sra_record"].action == "visit"
        assert "SRR003" in sources["sra_record"].url

    def test_nothing_downloadable_is_somebody_elses_file(self, db_conn, results_dir):
        """The whole point of the change. Raw reads were once offered under a
        download button pointing at NCBI's bucket; what GENOAR hands over now is
        only what its own pipeline produced."""
        downloads = [
            s for s in download_service.build_sources(db_conn, "SRR001")
            if s.action == "download"
        ]
        assert downloads, "the HDF5s should still be on offer"
        assert all(
            s.kind in {"processed_h5", "raw_h5", "molecule_info_h5"} for s in downloads
        )


class TestProcessedFromResultsDir:
    def test_offers_every_output_present_on_disk(self, db_conn, results_dir):
        sources = _by_kind(download_service.build_sources(db_conn, "SRR001"))
        assert {"geo_record", "processed_h5", "raw_h5", "molecule_info_h5"} <= set(sources)
        assert all(sources[k].available for k in ("processed_h5", "raw_h5", "molecule_info_h5"))

    def test_reports_real_file_sizes(self, db_conn, results_dir):
        sources = _by_kind(download_service.build_sources(db_conn, "SRR001"))
        assert sources["processed_h5"].size_bytes == 2048
        assert sources["molecule_info_h5"].size_bytes == 8192

    def test_links_back_to_this_service(self, db_conn, results_dir):
        h5 = _by_kind(download_service.build_sources(db_conn, "SRR001"))["processed_h5"]
        assert h5.url == "/api/v1/samples/SRR001/files/filtered_feature_bc_matrix.h5"

    def test_unprocessed_run_is_reported_as_such(self, db_conn, results_dir):
        # SRR002 has no directory under the results dir.
        h5 = _by_kind(download_service.build_sources(db_conn, "SRR002"))["processed_h5"]
        assert not h5.available
        assert h5.note == download_service.NOT_PROCESSED_NOTE

    def test_partial_output_offers_only_what_exists(self, db_conn, results_dir):
        (results_dir / "SRR001" / OUTS / "molecule_info.h5").unlink()
        sources = _by_kind(download_service.build_sources(db_conn, "SRR001"))
        assert "molecule_info_h5" not in sources
        assert sources["processed_h5"].available


class TestProcessedWithoutResultsDir:
    def test_unavailable_when_nothing_is_configured(self, db_conn, no_processed_source):
        h5 = _by_kind(download_service.build_sources(db_conn, "SRR001"))["processed_h5"]
        assert not h5.available
        assert "PROCESSED_RESULTS_DIR" in h5.note

    def test_url_template_is_the_remote_fallback(self, db_conn, monkeypatch):
        monkeypatch.setattr(settings, "processed_results_dir", "")
        monkeypatch.setattr(
            settings, "processed_h5_url_template", "https://data.example.org/{run_id}.h5"
        )
        h5 = _by_kind(download_service.build_sources(db_conn, "SRR001"))["processed_h5"]
        assert h5.available
        assert h5.url == "https://data.example.org/SRR001.h5"

    def test_results_dir_takes_precedence_over_the_template(self, db_conn, results_dir, monkeypatch):
        monkeypatch.setattr(
            settings, "processed_h5_url_template", "https://data.example.org/{run_id}.h5"
        )
        h5 = _by_kind(download_service.build_sources(db_conn, "SRR001"))["processed_h5"]
        assert h5.url.startswith("/api/v1/samples/")


class TestFileResolution:
    def test_resolves_a_known_output(self, results_dir):
        path = download_service.processed_file_path("SRR001", "filtered_feature_bc_matrix.h5")
        assert path is not None and path.is_file()

    def test_rejects_a_filename_outside_the_known_outputs(self, results_dir):
        assert download_service.processed_file_path("SRR001", "possorted_genome_bam.bam") is None

    @pytest.mark.parametrize(
        "filename", ["../../../etc/passwd", "..%2Fsecret", "/etc/passwd", "outs/../../x.h5"]
    )
    def test_refuses_path_traversal(self, results_dir, filename):
        assert download_service.processed_file_path("SRR001", filename) is None

    def test_none_when_no_results_dir_is_configured(self, no_processed_source):
        assert (
            download_service.processed_file_path("SRR001", "filtered_feature_bc_matrix.h5") is None
        )

    def test_none_for_an_unprocessed_run(self, results_dir):
        assert download_service.processed_file_path("SRR002", "filtered_feature_bc_matrix.h5") is None


class TestOtherLayouts:
    """A deployment whose outputs are not laid out the way stage 3 writes them.

    One deployment holds its two species' runs in two trees written by
    different runs of the pipeline — one nested under `cellranger_output/outs`,
    one with the files directly in the run's directory. Both are that
    deployment's processed data, and neither is the service's business to
    have opinions about, so where to look is configuration.
    """

    @pytest.fixture
    def flat_results(self, tmp_path, monkeypatch):
        """A run whose h5 sits directly in its directory, under a subtree."""
        run_dir = tmp_path / "MM" / "SRR900"
        run_dir.mkdir(parents=True)
        (run_dir / "filtered_feature_bc_matrix.h5").write_bytes(b"h5")
        monkeypatch.setattr(settings, "processed_results_dir", str(tmp_path))
        return tmp_path

    def test_a_configured_layout_finds_the_file(self, flat_results, monkeypatch):
        monkeypatch.setattr(settings, "processed_results_layouts", "MM/{run_id}")

        path = download_service.processed_file_path(
            "SRR900", "filtered_feature_bc_matrix.h5"
        )

        assert path is not None and path.is_file()

    def test_the_default_layout_still_works_beside_it(
        self, flat_results, monkeypatch
    ):
        """Configuring one layout must not cost a deployment the built-in one."""
        outs = flat_results / "SRR901" / "cellranger_output" / "outs"
        outs.mkdir(parents=True)
        (outs / "filtered_feature_bc_matrix.h5").write_bytes(b"h5")
        monkeypatch.setattr(settings, "processed_results_layouts", "MM/{run_id}")

        assert (
            download_service.processed_file_path(
                "SRR901", "filtered_feature_bc_matrix.h5"
            )
            is not None
        )

    def test_a_run_in_no_layout_is_still_unprocessed(
        self, flat_results, monkeypatch
    ):
        monkeypatch.setattr(settings, "processed_results_layouts", "MM/{run_id}")

        assert (
            download_service.processed_file_path(
                "SRR999", "filtered_feature_bc_matrix.h5"
            )
            is None
        )

    def test_a_layout_cannot_reach_outside_the_results_directory(
        self, flat_results, monkeypatch
    ):
        """Configuration is trusted, but a mistake in it should not serve /etc."""
        outside = flat_results.parent / "outside"
        outside.mkdir(exist_ok=True)
        (outside / "filtered_feature_bc_matrix.h5").write_bytes(b"h5")
        monkeypatch.setattr(settings, "processed_results_layouts", "../outside")

        assert (
            download_service.processed_file_path(
                "SRR900", "filtered_feature_bc_matrix.h5"
            )
            is None
        )


class TestResponse:
    def test_reports_no_api_key_requirement(self, db_conn, no_processed_source):
        assert download_service.build_response(db_conn, "SRR001").requires_api_key is False

    def test_echoes_the_requested_accession_and_geo_id(self, db_conn, no_processed_source):
        resp = download_service.build_response(db_conn, "SRR001", requested_accession="GSM0001")
        assert resp.run_id == "SRR001"
        assert resp.geo_accession == "GSM0001"
        assert resp.requested_accession == "GSM0001"


class TestProcessingParameters:
    """How the matrix was made, read from the matrix.

    The pipeline's own `_cmdline` and `_versions` are not on the deployment —
    only `outs/` survived — so the file itself is the last place these values
    exist. That is also the best place: they cannot fall out of step with it.
    """

    def test_reads_them_from_the_matrix(self, cellranger_results):
        params = download_service.processing_parameters("SRR001")
        assert params.software == "cellranger-8.0.1"
        assert params.chemistry == "Single Cell 3' v2"
        assert params.reference == "GRCh38"

    def test_none_when_the_run_was_never_processed(self, cellranger_results):
        assert download_service.processing_parameters("SRR002") is None

    def test_none_when_no_results_directory_is_configured(self, no_processed_source):
        assert download_service.processing_parameters("SRR001") is None

    def test_a_file_that_is_not_hdf5_is_not_an_error(self, results_dir):
        """`results_dir` writes filler bytes under the matrix's name. A sample
        whose file is unreadable should lose its parameters, not its page."""
        assert download_service.processing_parameters("SRR001") is None

    def test_the_response_carries_them(self, db_conn, cellranger_results):
        response = download_service.build_response(db_conn, "SRR001")
        assert response.processing.software == "cellranger-8.0.1"
