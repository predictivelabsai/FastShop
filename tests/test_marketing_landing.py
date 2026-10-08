from dataclasses import replace

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


def test_platform_root_gate_off_keeps_the_legacy_demo(monkeypatch):
    monkeypatch.setattr(
        site_context,
        "settings",
        replace(
            site_context.settings,
            environment="production",
            public_url="https://platform.fastshop.example",
            landing_root=False,
        ),
    )
    response = _client().get("/", headers={"host": "platform.fastshop.example"})
    assert response.status_code == 200
    assert "/static/site.css" in response.text
    assert "/static/marketing.css" not in response.text


def test_platform_root_gate_on_serves_landing_without_database_access(monkeypatch):
    monkeypatch.setattr(
        site_context,
        "settings",
        replace(
            site_context.settings,
            environment="production",
            public_url="https://platform.fastshop.example",
            landing_root=True,
        ),
    )

    def database_access_is_a_failure():
        raise AssertionError("platform landing root must bypass tenant lookup")

    monkeypatch.setattr(site_context, "SessionLocal", database_access_is_a_failure)
    response = _client().get("/", headers={"host": "platform.fastshop.example"})
    assert response.status_code == 200
    assert "/static/marketing.css" in response.text
    assert "From a short description to a live, checkout-capable store" in response.text


def test_development_always_keeps_the_legacy_demo_root(monkeypatch):
    monkeypatch.setattr(
        site_context,
        "settings",
        replace(
            site_context.settings,
            environment="development",
            public_url="http://platform.fastshop.test",
            landing_root=True,
        ),
    )
    response = _client().get("/", headers={"host": "platform.fastshop.test"})
    assert response.status_code == 200
    assert "/static/site.css" in response.text
    assert "/static/marketing.css" not in response.text


def test_landing_root_gate_only_applies_to_the_configured_platform_host(monkeypatch):
    monkeypatch.setattr(
        site_context,
        "settings",
        replace(
            site_context.settings,
            environment="production",
            public_url="https://platform.fastshop.example",
            landing_root=True,
        ),
    )
    response = _client().get("/", headers={"host": "unbound.example"})
    assert response.status_code == 200
    assert "/static/site.css" in response.text
    assert "/static/marketing.css" not in response.text
