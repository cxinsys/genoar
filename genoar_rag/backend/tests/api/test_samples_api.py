"""API tests for /api/v1/samples endpoints."""


class TestSamplesAPI:
    def test_list_samples(self, client):
        resp = client.get("/api/v1/samples")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 10
        assert len(data["items"]) == 10
        assert data["offset"] == 0
        assert data["limit"] == 20

    def test_list_samples_pagination(self, client):
        resp = client.get("/api/v1/samples?limit=3&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 3
        assert data["total"] == 10

    def test_list_samples_limit_exceeded(self, client):
        resp = client.get("/api/v1/samples?limit=101")
        assert resp.status_code == 422

    def test_get_sample_detail(self, client):
        resp = client.get("/api/v1/samples/SRR001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "SRR001"
        assert data["tissue"] == "Bone Marrow"
        assert data["organism"] == "Homo sapiens"
        assert "extended_fields" in data
        assert len(data["extended_fields"]) > 0

    def test_get_sample_not_found(self, client):
        resp = client.get("/api/v1/samples/NONEXISTENT")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_get_series_samples(self, client):
        resp = client.get("/api/v1/samples/SRR001/series")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        run_ids = {item["run_id"] for item in data["items"]}
        assert run_ids == {"SRR001", "SRR002"}

    def test_get_series_samples_not_found(self, client):
        resp = client.get("/api/v1/samples/NONEXISTENT/series")
        assert resp.status_code == 404
