"""Admin integrations: PayPal credential resolution and guide rendering."""

from types import SimpleNamespace

from fasthtml.common import to_xml
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import ui
from app.integrations import paypal
from app.models import Base, User
from app.site_seed import seed_h24you


def _site(site_id="abc123", slug="somestore"):
    return SimpleNamespace(id=site_id, slug=slug)


def test_paypal_credentials_read_per_site_env(monkeypatch):
    site = _site(site_id="deadbeef", slug="somestore")
    monkeypatch.setenv("FASTSHOP_PAYPAL_DEADBEEF_CLIENT_ID", "cid")
    monkeypatch.setenv("FASTSHOP_PAYPAL_DEADBEEF_SECRET", "sec")
    monkeypatch.setenv("FASTSHOP_PAYPAL_DEADBEEF_MODE", "live")
    client_id, secret, mode = paypal.credentials(site)
    assert (client_id, secret, mode) == ("cid", "sec", "live")
    state = paypal.readiness(site)
    assert state.configured and state.mode == "live" and state.client_id_set and state.secret_set


def test_paypal_primary_site_uses_globals(monkeypatch):
    for var in ("FASTSHOP_PAYPAL_PRIMARY_SITE", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET", "PAYPAL_MODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "gid")
    monkeypatch.setenv("PAYPAL_SECRET", "gsec")
    # default primary site slug is h24you
    client_id, secret, mode = paypal.credentials(_site(slug="h24you"))
    assert (client_id, secret, mode) == ("gid", "gsec", "sandbox")
    # a non-primary store gets nothing from the globals
    assert paypal.readiness(_site(slug="other")).configured is False


def test_readiness_never_exposes_values(monkeypatch):
    site = _site(site_id="feed01", slug="s")
    monkeypatch.setenv("FASTSHOP_PAYPAL_FEED01_CLIENT_ID", "supersecret-id")
    monkeypatch.setenv("FASTSHOP_PAYPAL_FEED01_SECRET", "supersecret-value")
    state = paypal.readiness(site)
    blob = repr(state)
    assert "supersecret-id" not in blob and "supersecret-value" not in blob


def test_static_guides_render_with_env_var_names():
    stripe_html = "".join(to_xml(p) for p in ui.stripe_guide() if p is not None)
    assert "FASTSHOP_STRIPE_{SITE_ID}_SECRET_KEY" in stripe_html and "sk_test_" in stripe_html
    woo_html = "".join(to_xml(p) for p in ui.woocommerce_guide() if p is not None)
    assert "FASTSHOP_WOOCOMMERCE_{SITE_ID}_CONSUMER_KEY" in woo_html


def test_paypal_guide_lists_key_env_vars_and_readiness(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add(User(email="admin@fastshop.example", name="Admin"))
        db.flush()
        seed_h24you(db, "admin@fastshop.example")
        db.commit()
        html = "".join(to_xml(p) for p in ui.paypal_guide(db) if p is not None)
    assert "FASTSHOP_PAYPAL_{SITE_ID}_CLIENT_ID" in html
    assert "FASTSHOP_PAYPAL_{SITE_ID}_SECRET" in html
    assert "PayPal readiness by store" in html
    assert "h24you" in html  # the seeded store appears in the readiness table


def test_integrations_hub_renders_all_providers():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        html = "".join(to_xml(p) for p in ui.integrations_hub(db) if p is not None)
    for provider in ("Stripe", "PayPal", "WooCommerce", "FastERP"):
        assert provider in html
