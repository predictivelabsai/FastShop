"""Merchants connect Shopify, WooCommerce or WordPress importers themselves."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from fasthtml.common import Div, Input, fast_app, to_xml
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

from app import importer_credential_routes as routes
from app import live_credentials, site_integrations
from app.integrations import shopify, woocommerce, wordpress
from app.services import CommerceError
from tests.test_site_golive import workspace

SITE = SimpleNamespace(id="abc123", tenant_id="t1", channel_currency="USD")


@pytest.mark.parametrize("module,stored,check", [
    (woocommerce, {"url": "https://shop.example.com", "consumer_key": "ck_1", "consumer_secret": "cs_1"},
     lambda c: c.origin == "https://shop.example.com" and c.key == "ck_1"),
    (shopify, {"shop": "store.myshopify.com", "token": "shpat_" + "a" * 32},
     lambda c: c.shop == "store.myshopify.com"),
    (wordpress, {"base_url": "https://site.example.com/wp-json/wp/v2", "username": "", "application_password": ""},
     lambda c: c.base_url == "https://site.example.com/wp-json/wp/v2"),
])
def test_a_saved_connection_takes_precedence_over_operator_settings(monkeypatch, module, stored, check):
    monkeypatch.setattr(site_integrations, "importer_credentials", lambda site, platform: stored)
    assert check(module._config(SITE, allow_incomplete=True))
    monkeypatch.setattr(site_integrations, "importer_credentials", lambda site, platform: None)
    assert not check(module._config(SITE, allow_incomplete=True))


def client_for(engine, user_id, monkeypatch):
    monkeypatch.setattr(routes, "SessionLocal", lambda: Session(engine, expire_on_commit=False))
    application, rt = fast_app(default_hdrs=False)
    application.add_middleware(SessionMiddleware, secret_key="importer-test-secret")

    def csrf(session):
        session["csrf_token"] = "expected-csrf"
        return Input(type="hidden", name="csrf_token", value="expected-csrf")

    def check_csrf(session, values):
        if session.get("csrf_token") != values.get("csrf_token"):
            raise CommerceError("Your session expired. Reload and try again.")

    routes.register_importer_credential_routes(rt, lambda session: user_id, csrf, check_csrf,
                                               lambda title, *children: str(to_xml(Div(*children))))
    return TestClient(application)


def test_merchant_saves_a_woocommerce_connection_encrypted(monkeypatch):
    with workspace() as (db, engine, owner, _, site):
        client = client_for(engine, owner.id, monkeypatch)
        url = f"/admin/sites/{site.id}/settings/integrations/importers/woocommerce"
        assert "Connect WooCommerce" in client.get(url).text
        invalid = client.post(url, data={"csrf_token": "expected-csrf", "url": "http://shop.example.com",
                                         "consumer_key": "x", "consumer_secret": "y"}, follow_redirects=False)
        assert "https+address" in invalid.headers["location"]
        # Without a server encryption key the merchant is told plainly, not shown a crash.
        monkeypatch.setattr(live_credentials, "settings", replace(live_credentials.settings, store_key_encryption_key=""))
        data = {"csrf_token": "expected-csrf", "url": "https://shop.example.com",
                "consumer_key": "ck_123", "consumer_secret": "cs_456"}
        unavailable = client.post(url, data=data, follow_redirects=False)
        assert "Secure+storage" in unavailable.headers["location"]
        monkeypatch.setattr(live_credentials, "settings",
                            replace(live_credentials.settings, store_key_encryption_key="k" * 40))
        saved = client.post(url, data=data, follow_redirects=False)
        assert saved.status_code == 303 and "WooCommerce+connected" in saved.headers["location"]
        db.expire_all()
        row = site_integrations.credential_row(db, site, "woocommerce")
        assert row.status == "connected" and b"cs_456" not in row.encrypted_payload
        assert site_integrations.payload_for(site, "woocommerce", db)["consumer_secret"] == "cs_456"
        assert "Connected: https://shop.example.com" in client.get(url).text
