"""What the service does not expose.

A vulnerability checklist reads a reachable Swagger UI as a management
interface, and a CORS preflight that advertises DELETE and credentials as an
API that has them. Neither describes this service, and these pin that down.

Against the real application object, not the fixture's: the docs switches and
the CORS policy live on the `FastAPI(...)` call in app.main, which the test
app in conftest does not go through. No lifespan is entered — nothing here
needs a database, and a preflight is answered by the middleware before any
route is looked up.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

ORIGIN = "http://localhost:3000"


@pytest.fixture
def bare_client():
    return TestClient(app)


class TestNoOpenApiSurface:
    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
    def test_schema_and_ui_are_off(self, bare_client, path):
        assert bare_client.get(path).status_code == 404

    def test_nothing_is_registered_under_those_names(self):
        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None


class TestResolveRouteShape:
    @pytest.mark.parametrize("value", ['x"; filename=pwn.exe', "NOTANID", "SRR001;"])
    def test_malformed_accession_is_404(self, client, value):
        assert client.get(f"/api/v1/samples/resolve/{value}").status_code == 404

    def test_well_formed_accession_still_resolves(self, client):
        assert client.get("/api/v1/samples/resolve/GSM0001").status_code == 200


class TestResponseHeaders:
    @pytest.mark.parametrize("path", ["/no-such-path", "/docs"])
    def test_every_response_carries_them(self, bare_client, path):
        # Including the ones the frontend proxies through untouched: the
        # rewrite hands the backend's headers to the browser as they are.
        headers = bare_client.get(path).headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "strict-origin-when-cross-origin"


class TestCors:
    def test_allowed_origin_gets_get_only(self, bare_client):
        resp = bare_client.options(
            "/api/v1/stats",
            headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"},
        )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == ORIGIN
        assert resp.headers["access-control-allow-methods"] == "GET"
        assert "access-control-allow-credentials" not in resp.headers

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
    def test_other_methods_are_refused_at_preflight(self, bare_client, method):
        resp = bare_client.options(
            "/api/v1/stats",
            headers={"Origin": ORIGIN, "Access-Control-Request-Method": method},
        )
        assert resp.status_code == 400

    def test_unknown_origin_is_not_reflected(self, bare_client):
        resp = bare_client.get("/no-such-path", headers={"Origin": "https://evil.example"})
        assert resp.status_code == 404
        assert "access-control-allow-origin" not in resp.headers
        assert "access-control-allow-credentials" not in resp.headers
