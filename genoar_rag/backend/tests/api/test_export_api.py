"""API tests for /api/v1/export."""

import csv
import io

import pytest

from app.services import export_service

WITHHELD = ("sample_name", "treatment", "sex", "age", "strain", "genotype")


def _csv_rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


class TestCsvExport:
    def test_defaults_to_csv(self, client):
        resp = client.get("/api/v1/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")

    def test_offered_as_a_file_download(self, client):
        resp = client.get("/api/v1/export")
        assert "attachment" in resp.headers["content-disposition"]
        assert "genoar_metadata.csv" in resp.headers["content-disposition"]

    def test_every_sample_is_exported(self, client):
        rows = _csv_rows(client.get("/api/v1/export").text)
        assert len(rows) == 10
        assert {r["run_id"] for r in rows} == {f"SRR{i:03d}" for i in range(1, 11)}

    def test_columns_are_the_allowlist(self, client):
        rows = _csv_rows(client.get("/api/v1/export").text)
        assert tuple(rows[0]) == export_service.HEADERS

    def test_submitter_written_fields_are_withheld(self, client):
        header = client.get("/api/v1/export").text.splitlines()[0]
        for column in WITHHELD:
            assert column not in header

    def test_filters_narrow_the_export(self, client):
        rows = _csv_rows(client.get("/api/v1/export?organism=Mus+musculus").text)
        assert rows
        assert {r["organism"] for r in rows} == {"Mus musculus"}

    def test_max_rows_caps_the_export(self, client):
        assert len(_csv_rows(client.get("/api/v1/export?max_rows=3").text)) == 3


class TestJsonExport:
    def test_returns_a_document_with_columns_and_items(self, client):
        data = client.get("/api/v1/export?format=json").json()
        assert data["count"] == 10
        assert data["columns"] == list(export_service.HEADERS)
        assert len(data["items"]) == 10

    def test_items_carry_curated_values(self, client):
        items = client.get("/api/v1/export?format=json").json()["items"]
        first = next(i for i in items if i["run_id"] == "SRR001")
        assert first["tissue"] == "Bone Marrow"
        assert first["organism"] == "Homo sapiens"


class TestSingleSampleExport:
    def test_by_run_id(self, client):
        rows = _csv_rows(client.get("/api/v1/export?accession=SRR001").text)
        assert len(rows) == 1
        assert rows[0]["run_id"] == "SRR001"
        assert rows[0]["tissue"] == "Bone Marrow"

    def test_by_geo_accession(self, client):
        rows = _csv_rows(client.get("/api/v1/export?accession=GSM0001").text)
        assert len(rows) == 1
        assert rows[0]["run_id"] == "SRR001"

    def test_file_is_named_after_the_sample(self, client):
        resp = client.get("/api/v1/export?accession=GSM0001")
        assert "genoar_SRR001.csv" in resp.headers["content-disposition"]

    def test_other_filters_are_ignored(self, client):
        # SRR001 is Homo sapiens; the contradicting filter must not empty the result.
        rows = _csv_rows(client.get("/api/v1/export?accession=SRR001&organism=Mus+musculus").text)
        assert len(rows) == 1
        assert rows[0]["run_id"] == "SRR001"

    def test_json_form(self, client):
        data = client.get("/api/v1/export?accession=SRR001&format=json").json()
        assert data["count"] == 1
        assert data["items"][0]["run_id"] == "SRR001"

    def test_columns_match_the_bulk_export(self, client):
        rows = _csv_rows(client.get("/api/v1/export?accession=SRR001").text)
        assert tuple(rows[0]) == export_service.HEADERS

    def test_unknown_geo_accession_is_404(self, client):
        assert client.get("/api/v1/export?accession=GSM999999").status_code == 404

    def test_unknown_run_id_exports_nothing(self, client):
        # resolve() passes run ids through, so an absent one simply matches no rows.
        assert _csv_rows(client.get("/api/v1/export?accession=SRR999").text) == []

    @pytest.mark.parametrize(
        "value",
        ['x"; filename=pwn.exe', "SRR001%0d%0aX-Injected:%201", "../../etc/passwd", "NOTANID"],
    )
    def test_malformed_accession_is_404_and_names_no_file(self, client, value):
        # The accession becomes the download's filename. A value that is not
        # an accession is refused before it can reach the Content-Disposition
        # header, in either format.
        for fmt in ("csv", "json"):
            resp = client.get(f"/api/v1/export?accession={value}&format={fmt}")
            assert resp.status_code == 404
            assert "content-disposition" not in resp.headers
            assert "pwn" not in str(resp.headers)

    def test_filename_is_the_resolved_run_id_only(self, client):
        disposition = client.get("/api/v1/export?accession=srr001").headers["content-disposition"]
        assert disposition == 'attachment; filename="genoar_srr001.csv"'


class TestValidation:
    def test_unknown_format_is_rejected(self, client):
        assert client.get("/api/v1/export?format=xlsx").status_code == 422

    def test_max_rows_ceiling_is_enforced(self, client):
        assert client.get("/api/v1/export?max_rows=250001").status_code == 422

    def test_an_export_asked_for_nothing_in_particular_is_not_truncated(self, client):
        """The default has to be above the corpus, not a round number under it.

        The UI's export passes no max_rows, so whatever this defaults to is the
        export the site actually offers — and one that silently stops a fifth of
        the way through looks exactly like one that finished."""
        from app.routers.export import MAX_ROWS_CEILING

        assert MAX_ROWS_CEILING > 197_757
        rows = _csv_rows(client.get("/api/v1/export").text)
        assert len(rows) == len(_csv_rows(client.get("/api/v1/export?max_rows=250000").text))

    def test_no_credential_is_required(self, client):
        assert client.get("/api/v1/export", headers={}).status_code == 200
