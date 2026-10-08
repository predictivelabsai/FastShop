from dataclasses import replace
from types import SimpleNamespace

from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from app import site_context
from app.main import app


def _client():
    return TestClient(app)


def test_marketing_routes_render_without_opening_a_database_session(monkeypatch):
    def database_access_is_a_failure():
        raise AssertionError("marketing routes must not open a database session")

    monkeypatch.setattr(site_context, "SessionLocal", database_access_is_a_failure)
    for path in ("/marketing/", "/signup"):
        response = _client().get(path)
        assert response.status_code == 200


def test_marketing_landing_contains_required_content_and_no_active_markup():
    response = _client().get("/marketing/")
    assert response.status_code == 200
    html = response.text.lower()
    assert html.count("<h1") == 1
    assert "from a short description to a live, checkout-capable store" in html
    assert "shopify" in html
    assert "woocommerce" in html
    assert "wordpress" in html
    assert "google merchant center" in html
    assert "simple plans based on sites and ai generations" in html
    assert 'href="/signup"' in html
    assert "<script" not in html
    assert "<iframe" not in html
    assert "analytics" not in html


def test_signup_closed_state_is_honest_and_has_no_form():
    response = _client().get("/signup")
    assert response.status_code == 200
    html = response.text.lower()
    assert "public signup is currently closed" in html
    assert "no information is collected while signup is closed" in html
    assert "<form" not in html
    assert "<script" not in html
    assert "<iframe" not in html


def test_platform_root_serves_landing_in_development_without_database_access(monkeypatch):
    monkeypatch.setattr(
        site_context,
        "settings",
        replace(
            site_context.settings,
            environment="development",
            public_url="https://platform.fastshop.example",
        ),
    )

    def database_access_is_a_failure():
        raise AssertionError("platform landing root must bypass tenant lookup")

    monkeypatch.setattr(site_context, "SessionLocal", database_access_is_a_failure)
    response = _client().get("/", headers={"host": "platform.fastshop.example"})
    assert response.status_code == 200
    assert "/static/marketing.css" in response.text
    assert "From a short description to a live, checkout-capable store" in response.text


def test_platform_demo_root_and_subpath_render_the_legacy_storefront(monkeypatch):
    monkeypatch.setattr(
        site_context,
        "settings",
        replace(
            site_context.settings,
            environment="development",
            public_url="http://platform.fastshop.test",
        ),
    )
    for path in ("/demo", "/demo/products"):
        response = _client().get(path, headers={"host": "platform.fastshop.test"})
        assert response.status_code == 200
        assert "/static/site.css" in response.text
        assert "/static/marketing.css" not in response.text


def test_platform_rewrites_do_not_apply_to_a_non_platform_host(monkeypatch):
    monkeypatch.setattr(
        site_context,
        "settings",
        replace(
            site_context.settings,
            environment="development",
            public_url="https://platform.fastshop.example",
        ),
    )
    root_response = _client().get("/", headers={"host": "unbound.example"})
    assert root_response.status_code == 200
    assert "/static/site.css" in root_response.text
    assert "/static/marketing.css" not in root_response.text

    demo_response = _client().get("/demo", headers={"host": "unbound.example"})
    assert demo_response.status_code == 404


def test_demo_prefix_requires_an_exact_path_segment(monkeypatch):
    monkeypatch.setattr(
        site_context,
        "settings",
        replace(site_context.settings, public_url="https://platform.fastshop.example"),
    )
    for path in ("/demos", "/demofoo"):
        response = _client().get(path, headers={"host": "platform.fastshop.example"})
        assert response.status_code == 404


def test_tenant_hostname_matching_is_unaffected(monkeypatch):
    monkeypatch.setattr(
        site_context,
        "settings",
        replace(site_context.settings, public_url="https://platform.fastshop.example"),
    )

    class TenantSession:
        def __enter__(self):
            self.results = iter(
                (
                    SimpleNamespace(
                        hostname="tenant.fastshop.example",
                        status="published",
                        slug="tenant-store",
                        tenant_id="tenant-id",
                        id="site-id",
                    ),
                    None,
                )
            )
            return self

        def __exit__(self, *_):
            return None

        def scalar(self, _query):
            return next(self.results)

    monkeypatch.setattr(site_context, "SessionLocal", TenantSession)

    async def endpoint(scope, receive, send):
        await PlainTextResponse(scope["path"])(scope, receive, send)

    client = TestClient(site_context.SiteHostMiddleware(endpoint))
    response = client.get("/products", headers={"host": "tenant.fastshop.example"})
    assert response.status_code == 200
    assert response.text == "/sites/tenant-store/products"
