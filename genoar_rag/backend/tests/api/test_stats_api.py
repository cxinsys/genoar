"""API tests for /api/v1/stats endpoint."""


class TestStatsAPI:
    def test_get_stats(self, client):
        resp = client.get("/api/v1/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_samples"] == 10
        assert data["total_series"] == 6  # SRR001 is in two studies; see conftest
        assert len(data["organism_distribution"]) > 0
        assert len(data["assay_distribution"]) > 0
        assert len(data["platform_distribution"]) > 0
        assert len(data["library_source_distribution"]) > 0

    def test_stats_distribution_format(self, client):
        resp = client.get("/api/v1/stats")
        data = resp.json()
        for item in data["organism_distribution"]:
            assert "label" in item
            assert "count" in item
            assert "percentage" in item
            assert 0 <= item["percentage"] <= 100
