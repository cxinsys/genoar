"""API tests for /api/v1/filters endpoint."""


class TestFiltersAPI:
    def test_get_filters(self, client):
        resp = client.get("/api/v1/filters")
        assert resp.status_code == 200
        data = resp.json()
        assert "categories" in data
        names = {c["name"] for c in data["categories"]}
        assert "tissue" in names
        assert "organism" in names
        assert "assay_type" in names
        assert "disease" in names

    def test_filter_values_have_counts(self, client):
        resp = client.get("/api/v1/filters")
        data = resp.json()
        for cat in data["categories"]:
            for val in cat["values"]:
                assert "value" in val
                assert "count" in val
                assert val["count"] > 0


class TestFilterCategoryAPI:
    def test_returns_a_page_of_one_category(self, client):
        resp = client.get("/api/v1/filters/tissue", params={"limit": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "tissue"
        assert len(data["values"]) == 2
        assert data["total_distinct"] == 5
        assert (data["offset"], data["limit"]) == (0, 2)

    def test_offset_continues_where_the_previous_page_stopped(self, client):
        first = client.get("/api/v1/filters/tissue", params={"limit": 2}).json()
        second = client.get(
            "/api/v1/filters/tissue", params={"limit": 2, "offset": 2}
        ).json()
        whole = client.get("/api/v1/filters/tissue", params={"limit": 50}).json()

        seen = [v["value"] for v in first["values"] + second["values"]]
        assert seen == [v["value"] for v in whole["values"][:4]]
        assert len(set(seen)) == 4

    def test_search_narrows_the_category(self, client):
        resp = client.get("/api/v1/filters/tissue", params={"q": "bl"})
        data = resp.json()
        assert [v["value"] for v in data["values"]] == ["Blood"]
        assert data["total_distinct"] == 1
        assert data["query"] == "bl"

    def test_unknown_category_is_404_and_says_what_is_valid(self, client):
        resp = client.get("/api/v1/filters/not_a_category")
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert "not_a_category" in detail
        assert "tissue" in detail

    def test_limit_is_bounded(self, client):
        assert client.get("/api/v1/filters/tissue", params={"limit": 0}).status_code == 422
        assert client.get("/api/v1/filters/tissue", params={"limit": 201}).status_code == 422
        assert client.get("/api/v1/filters/tissue", params={"offset": -1}).status_code == 422

    def test_every_advertised_category_can_be_paged(self, client):
        names = [c["name"] for c in client.get("/api/v1/filters").json()["categories"]]
        for name in names:
            resp = client.get(f"/api/v1/filters/{name}", params={"limit": 1})
            assert resp.status_code == 200, name
            assert resp.json()["name"] == name
