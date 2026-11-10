"""Merchant Stripe connection storage and provider lifecycle."""

from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import commerce, content, live_credentials, stripe_connections
from app.checkout_payments import publishable_for
from app.commerce_webhooks import CHECKOUT_EVENTS, RENEWAL_EVENTS, signature_secret
from app.integrations.stripe_commerce import StripeGateway, credentials, make_gateway
from app.models import Base, SiteStripeConnection, User
from app.services import CommerceError

KEY = "sk_test_connection_fixture_1234"
PUBLISHABLE = "pk_test_connection_fixture_5678"
WEBHOOK = "whsec_manual_fixture_1234"


@pytest.fixture
def workspace(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(live_credentials, "settings", SimpleNamespace(
        store_key_encryption_key="merchant-connection-encryption-fixture",
        admin_email="operator@example.test",
    ))
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="owner@example.test", name="Owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Connection test", "stripe-connect-test")
        db.commit()
        yield db, owner, site
    engine.dispose()


def fake_transport(seen):
    def reply(request):
        seen.append(request)
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"id": "acct_fixture", "country": "US",
                "livemode": False, "business_profile": {"name": "Fixture Shop"}})
        if request.url.path == "/v1/tax/settings":
            return httpx.Response(200, json={"object": "tax.settings", "livemode": False,
                "status": "active"})
        if request.url.path == "/v1/webhook_endpoints" and request.method == "POST":
            count = sum(item.url.path == "/v1/webhook_endpoints" and item.method == "POST"
                        for item in seen)
            return httpx.Response(200, json={"id": f"we_fixture{count}",
                "secret": f"whsec_created_fixture_{count:04d}"})
        if request.url.path.startswith("/v1/webhook_endpoints/we_") and request.method == "DELETE":
            return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1], "deleted": True})
        return httpx.Response(404, json={"error": {"message": "missing fixture"}})
    return httpx.MockTransport(reply)


def test_storage_encrypts_masks_and_resolves_admin_before_environment(workspace, monkeypatch):
    db, owner, site = workspace
    seen = []
    connection = stripe_connections.save(db, site, owner.id, "test", KEY, PUBLISHABLE,
        WEBHOOK, transport=fake_transport(seen))
    config = commerce.settings_for(db, site, create=True)
    config.mode = "sandbox"
    db.commit()
    assert connection.key_last_four == "1234"
    assert connection.publishable_last_four == "5678"
    assert KEY.encode() not in connection.secret_key_ciphertext
    assert PUBLISHABLE.encode() not in connection.publishable_key_ciphertext
    assert WEBHOOK.encode() not in connection.webhook_secret_ciphertext
    monkeypatch.setenv(f"FASTSHOP_STRIPE_{site.id.upper()}_SECRET_KEY", "sk_test_environment_key_1234")
    monkeypatch.setenv(f"FASTSHOP_STRIPE_{site.id.upper()}_WEBHOOK_SECRET", "whsec_environment_1234")
    monkeypatch.setenv(f"FASTSHOP_STRIPE_{site.id.upper()}_PUBLISHABLE_KEY", "pk_test_environment_1234")
    assert credentials(site, db=db) == (KEY, WEBHOOK, PUBLISHABLE)
    from app import db as app_db
    monkeypatch.setattr(app_db, "SessionLocal", sessionmaker(bind=db.bind, expire_on_commit=False))
    assert credentials(site) == (KEY, WEBHOOK, PUBLISHABLE)
    assert stripe_connections.decrypted(db, site, connection) == (KEY, WEBHOOK, PUBLISHABLE)
    assert publishable_for(db, site) == PUBLISHABLE
    assert make_gateway(StripeGateway, site, db).key == KEY
    assert signature_secret(db, site, config) == ("sandbox", "whsec_manual_fixture_1234")
    assert connection.account_name == "Fixture Shop" and connection.account_id == "acct_fixture"


def test_webhook_registration_uses_handler_event_allowlist(workspace):
    db, owner, site = workspace
    seen = []
    stripe_connections.save(db, site, owner.id, "test", KEY, PUBLISHABLE, "",
        webhook_url="https://shop.example.test/api/v1/commerce/site/stripe-webhook",
        transport=fake_transport(seen))
    request = next(item for item in seen if item.url.path == "/v1/webhook_endpoints")
    values = parse_qs(request.content.decode())
    events = {value for key, values_for_key in values.items()
              if key.startswith("enabled_events[") for value in values_for_key}
    assert events == CHECKOUT_EVENTS | RENEWAL_EVENTS


def test_verification_failure_and_mode_pairing_are_rejected(workspace):
    db, owner, site = workspace
    with pytest.raises(CommerceError, match="publishable"):
        stripe_connections.save(db, site, owner.id, "test", KEY,
            "pk_live_connection_fixture_5678", WEBHOOK)
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": {"message": "secret"}}))
    with pytest.raises(CommerceError, match="could not complete"):
        stripe_connections.save(db, site, owner.id, "test", KEY, PUBLISHABLE, WEBHOOK,
            transport=transport)
    with pytest.raises(CommerceError, match="test secret"):
        stripe_connections.save(db, site, owner.id, "test", "sk_live_connection_fixture_1234",
            PUBLISHABLE, WEBHOOK)


def test_tax_settings_failure_does_not_block_connection_save(workspace):
    db, owner, site = workspace

    def account_only(request):
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"id": "acct_fixture", "livemode": False})
        return httpx.Response(403, json={"error": {"message": "tax access unavailable"}})

    record = stripe_connections.save(db, site, owner.id, "test", KEY, PUBLISHABLE, WEBHOOK,
        transport=httpx.MockTransport(account_only))
    assert record.verified_at is not None and record.account_id == "acct_fixture"
    assert stripe_connections.cached_tax_status(site, "test")["status"] == "unknown"


def test_replacement_and_disconnect_delete_old_endpoint_best_effort(workspace):
    db, owner, site = workspace
    seen = []
    transport = fake_transport(seen)
    stripe_connections.save(db, site, owner.id, "test", KEY, PUBLISHABLE, "",
        webhook_url="https://shop.example.test/hook", transport=transport)
    stripe_connections.save(db, site, owner.id, "test", KEY, PUBLISHABLE, "",
        webhook_url="https://shop.example.test/hook", transport=transport)
    assert any(item.method == "DELETE" for item in seen)
    stripe_connections.disconnect(db, site, "test", transport=transport)
    assert db.query(SiteStripeConnection).filter_by(site_id=site.id).first() is None
    assert sum(item.method == "DELETE" for item in seen) == 2


def test_test_connection_reports_tax_status(workspace):
    db, owner, site = workspace
    seen = []
    stripe_connections.save(db, site, owner.id, "test", KEY, PUBLISHABLE, WEBHOOK,
        transport=fake_transport(seen))
    status = stripe_connections.test_connection(db, site, "test", transport=fake_transport(seen))
    assert status["tax_settings_active"] is True
    assert status["account_name"] == "Fixture Shop"


def test_pending_tax_settings_are_saved_with_missing_fields(workspace):
    db, owner, site = workspace

    def reply(request):
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"id": "acct_fixture", "livemode": False})
        if request.url.path == "/v1/tax/settings":
            return httpx.Response(200, json={"object": "tax.settings", "livemode": False,
                "status": "pending", "status_details": {"pending": {
                    "missing_fields": ["head_office.address.city", "head_office.address.line1"]}},
                "defaults": {"tax_behavior": "exclusive"}, "head_office": {"country": "US"}})
        return httpx.Response(404, json={})

    stripe_connections.save(db, site, owner.id, "test", KEY, PUBLISHABLE, WEBHOOK,
        transport=httpx.MockTransport(reply))
    status = stripe_connections.cached_tax_status(site, "test")
    assert status["status"] == "pending"
    assert status["missing_fields"] == ["head_office.address.city", "head_office.address.line1"]
    assert status["defaults"] == {"tax_behavior": "exclusive"}
    assert status["head_office"] == {"country": "US"}


def test_merchant_live_connection_still_needs_operator_acceptance(workspace):
    db, owner, site = workspace
    config = commerce.settings_for(db, site, create=True)
    live_key = "sk_live_connection_fixture_1234"
    live_publishable = "pk_live_connection_fixture_5678"

    def reply(request):
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"id": "acct_live_fixture", "country": "US",
                "livemode": True, "business_profile": {"name": "Live fixture"}})
        if request.url.path == "/v1/tax/settings":
            return httpx.Response(200, json={"object": "tax.settings", "livemode": True,
                "status": "active"})
        return httpx.Response(404, json={})

    stripe_connections.save(db, site, owner.id, "live", live_key, live_publishable, WEBHOOK,
        transport=httpx.MockTransport(reply))
    mirror = live_credentials.credential_for(db, site)
    assert mirror and live_credentials.decrypted(db, site, mirror) == (live_key, WEBHOOK)
    assert mirror.publishable_key is None
    assert live_credentials.state_for(db, site, config).masked_publishable_key == "pk_live_...5678"
    config.mode = "live"
    with pytest.raises(CommerceError, match="not enabled"):
        commerce.payment_mode(db, site, config)
