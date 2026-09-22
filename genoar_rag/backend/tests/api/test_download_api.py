"""API tests for accession-addressed lookup and the per-sample download endpoint."""

import json

import pytest

from app.config import settings
from app.services import download_service


@pytest.fixture(autouse=True)
def no_processed_url(monkeypatch):
    monkeypatch.setattr(settings, "processed_results_dir", "")
    monkeypatch.setattr(settings, "processed_h5_url_template", "")


@pytest.fixture
def results_dir(tmp_path, monkeypatch):
    """Cell Ranger output for SRR001, laid out the way Stage 3 writes it.

    No receipt beside it, which is what a deployment that predates provenance
    tracking holds. The default settings serve it, so the tests below that
    predate receipts read the same as they always did.
    """
    outs = tmp_path / "SRR001" / "cellranger_output" / "outs"
    outs.mkdir(parents=True)
    (outs / "filtered_feature_bc_matrix.h5").write_bytes(b"h5-content")
    monkeypatch.setattr(settings, "processed_results_dir", str(tmp_path))
    monkeypatch.setattr(settings, "processed_results_require_receipt", False)
    return tmp_path


def write_receipt(run_dir, provenance):
    """A receipt in the shape run_docker_pipeline.sh writes one."""
    (run_dir / download_service.RECEIPT_NAME).write_text(
        json.dumps(
            {
                "sample": run_dir.name,
                "run_id": "run-2026-01-02",
                "provenance": provenance,
                "input_fingerprint": "d0e1f2",
                "bam_size": 4096,
                "bam_mtime": 1767225600,
                "completed_at": "2026-01-02T03:04:05Z",
            }
        )
    )


class TestAccessionLookup:
    def test_sample_detail_by_geo_accession(self, client):
        resp = client.get("/api/v1/samples/GSM0001")
        assert resp.status_code == 200
        # The GSM resolves to its run, and the payload is the run's detail.
        assert resp.json()["run_id"] == "SRR001"

    def test_geo_and_run_lookups_agree(self, client):
        assert (
            client.get("/api/v1/samples/GSM0001").json()
            == client.get("/api/v1/samples/SRR001").json()
        )

    def test_unknown_geo_accession_is_404(self, client):
        assert client.get("/api/v1/samples/GSM999999").status_code == 404

    def test_series_by_geo_accession(self, client):
        resp = client.get("/api/v1/samples/GSM0001/series")
        assert resp.status_code == 200
        assert {i["run_id"] for i in resp.json()["items"]} == {"SRR001", "SRR002"}


class TestDownloadEndpoint:
    def test_by_run_id(self, client):
        resp = client.get("/api/v1/samples/SRR001/download")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "SRR001"
        assert data["geo_accession"] == "GSM0001"

    def test_states_that_no_api_key_is_needed(self, client):
        assert client.get("/api/v1/samples/SRR001/download").json()["requires_api_key"] is False

    def test_no_authentication_header_is_required(self, client):
        # The endpoint is reachable with no credential of any kind.
        assert client.get("/api/v1/samples/SRR001/download", headers={}).status_code == 200

    def test_the_archive_record_is_a_link_rather_than_a_download(self, client):
        sources = {s["kind"]: s for s in client.get("/api/v1/samples/SRR001/download").json()["sources"]}
        record = sources["geo_record"]
        assert record["available"] is True
        assert record["action"] == "visit"
        assert record["url"].startswith("https://")
        assert record["size_bytes"] is None

    def test_processed_source_is_flagged_unavailable(self, client):
        sources = {s["kind"]: s for s in client.get("/api/v1/samples/SRR001/download").json()["sources"]}
        h5 = sources["processed_h5"]
        assert h5["available"] is False
        assert h5["url"] is None

    def test_by_geo_accession(self, client):
        resp = client.get("/api/v1/samples/GSM0002/download")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "SRR002"
        assert data["requested_accession"] == "GSM0002"

    def test_unknown_run_is_404(self, client):
        assert client.get("/api/v1/samples/SRR999/download").status_code == 404

    def test_unknown_geo_accession_is_404(self, client):
        assert client.get("/api/v1/samples/GSM999999/download").status_code == 404


class TestProcessedFiles:
    def test_processed_output_is_offered_when_present(self, client, results_dir):
        sources = {s["kind"]: s for s in client.get("/api/v1/samples/SRR001/download").json()["sources"]}
        h5 = sources["processed_h5"]
        assert h5["available"] is True
        assert h5["url"] == "/api/v1/samples/SRR001/files/filtered_feature_bc_matrix.h5"

    def test_file_downloads(self, client, results_dir):
        resp = client.get("/api/v1/samples/SRR001/files/filtered_feature_bc_matrix.h5")
        assert resp.status_code == 200
        assert resp.content == b"h5-content"
        assert "SRR001_filtered_feature_bc_matrix.h5" in resp.headers["content-disposition"]

    def test_file_reachable_by_geo_accession(self, client, results_dir):
        resp = client.get("/api/v1/samples/GSM0001/files/filtered_feature_bc_matrix.h5")
        assert resp.status_code == 200
        assert resp.content == b"h5-content"

    def test_no_credential_is_required(self, client, results_dir):
        resp = client.get(
            "/api/v1/samples/SRR001/files/filtered_feature_bc_matrix.h5", headers={}
        )
        assert resp.status_code == 200

    def test_absent_output_is_404(self, client, results_dir):
        # Present in the layout but not written for this run.
        assert client.get("/api/v1/samples/SRR001/files/molecule_info.h5").status_code == 404

    def test_unprocessed_run_is_404(self, client, results_dir):
        assert (
            client.get("/api/v1/samples/SRR002/files/filtered_feature_bc_matrix.h5").status_code
            == 404
        )

    def test_unknown_filename_is_404(self, client, results_dir):
        assert client.get("/api/v1/samples/SRR001/files/secrets.env").status_code == 404

    def test_unprocessed_run_says_so(self, client, results_dir):
        sources = {s["kind"]: s for s in client.get("/api/v1/samples/SRR002/download").json()["sources"]}
        assert sources["processed_h5"]["available"] is False
        assert "not been processed" in sources["processed_h5"]["note"]


def _manifest(client, run_id="SRR001"):
    return client.get(f"/api/v1/samples/{run_id}/download").json()


def _h5(client, run_id="SRR001"):
    return {s["kind"]: s for s in _manifest(client, run_id)["sources"]}["processed_h5"]


class TestProvenanceOverTheApi:
    """What a caller can see about where a sample's output came from, and what
    the service does about it."""

    def test_a_verified_sample_is_offered_and_says_it_is_verified(
        self, client, results_dir
    ):
        write_receipt(results_dir / "SRR001", "fresh")

        data = _manifest(client)

        assert data["provenance"]["status"] == "verified"
        assert data["provenance"]["verified"] is True
        assert data["provenance"]["offered"] is True
        assert data["provenance"]["receipt_run_id"] == "run-2026-01-02"
        h5 = {s["kind"]: s for s in data["sources"]}["processed_h5"]
        assert h5["available"] is True
        assert h5["provenance"] == "verified"

    def test_a_verified_sample_downloads(self, client, results_dir):
        write_receipt(results_dir / "SRR001", "fresh")
        resp = client.get("/api/v1/samples/SRR001/files/filtered_feature_bc_matrix.h5")
        assert resp.status_code == 200
        assert resp.content == b"h5-content"

    def test_an_adopted_sample_is_withheld_and_says_why(self, client, results_dir):
        write_receipt(results_dir / "SRR001", "adopted")

        data = _manifest(client)

        assert data["provenance"]["status"] == "adopted"
        assert data["provenance"]["verified"] is False
        assert data["provenance"]["offered"] is False
        h5 = {s["kind"]: s for s in data["sources"]}["processed_h5"]
        assert h5["available"] is False
        assert h5["url"] is None
        assert "GENOAR_ADOPT_PRIOR_RESULTS=1" in h5["note"]

    def test_an_adopted_sample_does_not_download(self, client, results_dir):
        write_receipt(results_dir / "SRR001", "adopted")
        resp = client.get("/api/v1/samples/SRR001/files/filtered_feature_bc_matrix.h5")
        assert resp.status_code == 404

    def test_a_receipt_the_service_cannot_read_withholds_the_file(
        self, client, results_dir
    ):
        (results_dir / "SRR001" / download_service.RECEIPT_NAME).write_text("{nope")

        assert _manifest(client)["provenance"]["status"] == "unreadable"
        assert _h5(client)["available"] is False
        assert (
            client.get(
                "/api/v1/samples/SRR001/files/filtered_feature_bc_matrix.h5"
            ).status_code
            == 404
        )

    def test_output_with_no_receipt_is_served_and_labelled(self, client, results_dir):
        """The deployed case. The download panel is what it was, and the
        response now says on what basis."""
        data = _manifest(client)

        assert data["provenance"]["status"] == "unrecorded"
        assert data["provenance"]["offered"] is True
        h5 = {s["kind"]: s for s in data["sources"]}["processed_h5"]
        assert h5["available"] is True
        assert h5["url"] == "/api/v1/samples/SRR001/files/filtered_feature_bc_matrix.h5"
        assert h5["note"] is None

    def test_a_strict_deployment_withholds_output_with_no_receipt(
        self, client, results_dir, monkeypatch
    ):
        monkeypatch.setattr(settings, "processed_results_require_receipt", True)

        assert _manifest(client)["provenance"]["status"] == "unrecorded"
        assert _h5(client)["available"] is False
        assert (
            client.get(
                "/api/v1/samples/SRR001/files/filtered_feature_bc_matrix.h5"
            ).status_code
            == 404
        )

    def test_a_run_with_no_output_carries_no_provenance(self, client, results_dir):
        assert _manifest(client, "SRR002")["provenance"] is None

    def test_a_deployment_with_no_results_directory_carries_none_either(self, client):
        assert _manifest(client)["provenance"] is None
