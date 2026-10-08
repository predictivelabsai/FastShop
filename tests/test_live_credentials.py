import hashlib
import hmac
import json
import re
import time
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app import commerce, content, live_credentials, site_builder_services, site_golive
from app.commerce_webhooks import signature_secret
from app.config import settings
from app.integrations.stripe_commerce import StripeGateway, verify_webhook
from app.models import Base, Membership, SiteChangeSet, User
from app.services import CommerceError

LIVE_KEY = "sk_live_fixture_secret_1234"
LIVE_WEBHOOK = "whsec_fixture_live_5678"


@contextmanager
def workspace(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(live_credentials, "settings", SimpleNamespace(
        admin_email=settings.admin_email,
        store_key_encryption_key="operator-encryption-material-for-tests",
    ))
    with Session(engine, expire_on_commit=False) as db:
        operator = User(email=settings.admin_email, name="Platform operator")
        merchant = User(email="merchant-live@example.test", name="Merchant")
        db.add_all([operator, merchant])
        db.flush()
        site = content.create_site(db, operator.id, "Live test", "live-test")
        db.add(Membership(tenant_id=site.tenant_id, user_id=merchant.id, role="merchant"))
        config = commerce.settings_for(db, site, create=True)
        db.commit()
        yield db, operator, merchant, site, config


def pass_report(site_id):
    check = site_golive.ReadinessCheck("ready", "Ready", True, "Ready.", "/")
    return site_golive.ReadinessReport(site_id, True, (check,))


def stored_and_verified(db, operator, site):
    credential = live_credentials.store(
        db, site.id, operator.id, LIVE_KEY, LIVE_WEBHOOK
    )
    live_credentials.mark_verified(db, site.id, operator.id, credential.id)
    return credential


def test_encryption_round_trip_is_bound_to_context_and_missing_key_fails_closed():
    material = "fixture-key-material-at-least-32-bytes"
    ciphertext = live_credentials.encrypt_secret(
        LIVE_KEY, aad="site:key:secret", key_material=material
    )
    assert LIVE_KEY.encode() not in ciphertext
    assert live_credentials.decrypt_secret(
        ciphertext, aad="site:key:secret", key_material=material
    ) == LIVE_KEY
    with pytest.raises(CommerceError):
        live_credentials.decrypt_secret(
            ciphertext, aad="other:key:secret", key_material=material
        )
    with pytest.raises(CommerceError):
        live_credentials.encrypt_secret(LIVE_KEY, aad="site:key:secret", key_material="")


def test_operator_storage_is_encrypted_masked_tenant_scoped_and_secret_free(monkeypatch):
    with workspace(monkeypatch) as (db, operator, merchant, site, config):
        with pytest.raises(CommerceError, match="operator"):
            live_credentials.store(db, site.id, merchant.id, LIVE_KEY, LIVE_WEBHOOK)
        credential = live_credentials.store(
            db, site.id, operator.id, LIVE_KEY, LIVE_WEBHOOK
        )
        state = live_credentials.state_for(db, site, config)
        assert state.masked_key == "sk_live_****1234"
        assert state.created_by == operator.id and state.credential_id == credential.id
        assert LIVE_KEY.encode() not in credential.secret_key_ciphertext
        assert LIVE_WEBHOOK.encode() not in credential.webhook_secret_ciphertext
        assert LIVE_KEY not in str(site.settings_json)
        change = db.scalar(select(SiteChangeSet).where(
            SiteChangeSet.site_id == site.id,
            SiteChangeSet.source == "live-acceptance",
        ))
        audit = str(change.before_json) + str(change.after_json)
        assert credential.id in audit
        assert LIVE_KEY not in audit and LIVE_WEBHOOK not in audit


def test_acceptance_is_hard_gated_versioned_audited_and_disable_requires_reverification(
    monkeypatch,
):
    with workspace(monkeypatch) as (db, operator, _, site, config):
        credential = stored_and_verified(db, operator, site)
        config.mode = "sandbox"
        site.status = "preview"
        site.hostname = "live.example.test"
        db.flush()
        with pytest.raises(CommerceError, match="blocked"):
            live_credentials.accept(
                db, site.id, operator.id, site.version, config.version,
                credential.id, "Preview sites must not accept live payments.",
            )
        assert config.mode == "sandbox"

        site.status = "published"
        monkeypatch.setattr(
            live_credentials.site_golive, "assess", lambda db, site, commerce_requested: pass_report(site.id)
        )
        stale_version = site.version - 1
        with pytest.raises(CommerceError, match="changed"):
            live_credentials.accept(
                db, site.id, operator.id, stale_version, config.version,
                credential.id, "This stale acceptance must be rejected.",
            )
        live_credentials.accept(
            db, site.id, operator.id, site.version, config.version,
            credential.id, "Operator verified the published store and live Stripe account.",
        )
        assert config.mode == "live"
        assert config.live_accepted_by == operator.id
        assert commerce.payment_mode(db, site, config) == "live"
        assert live_credentials.live_secrets(db, site, config) == (LIVE_KEY, LIVE_WEBHOOK)
        accepted = db.scalar(select(SiteChangeSet).where(
            SiteChangeSet.site_id == site.id,
            SiteChangeSet.summary == "live-acceptance: approved",
        ))
        assert accepted and accepted.user_id == operator.id
        assert LIVE_KEY not in str(accepted.before_json) + str(accepted.after_json)
        with pytest.raises(CommerceError, match="current unchanged draft"):
            site_builder_services.undo_change(
                db, site.id, operator.id, accepted.id, site.version
            )

        with pytest.raises(CommerceError, match="operator"):
            site_golive.set_sandbox_commerce(
                db, site.id, operator.id, site.version, False,
                config_version=config.version,
            )
        live_credentials.disable(
            db, site.id, operator.id, site.version, config.version,
            "Operator disabled live processing for a credential review.",
        )
        assert config.mode == "sandbox" and not config.live_accepted_at
        assert not live_credentials.state_for(db, site, config).verified
        with pytest.raises(CommerceError, match="Verify"):
            live_credentials.accept(
                db, site.id, operator.id, site.version, config.version,
                credential.id, "A second acceptance must require verification again.",
            )


def test_live_mode_fails_closed_without_encryption_key_and_on_draft(monkeypatch):
    with workspace(monkeypatch) as (db, operator, _, site, config):
        credential = stored_and_verified(db, operator, site)
        config.mode = "live"
        config.live_accepted_at = credential.verified_at
        config.live_accepted_by = operator.id
        config.live_credential_id = credential.id
        site.hostname = "payments.example.test"
        site.status = "draft"
        with pytest.raises(CommerceError):
            commerce.payment_mode(db, site, config)
        site.status = "published"
        monkeypatch.setattr(live_credentials, "settings", SimpleNamespace(
            admin_email=settings.admin_email, store_key_encryption_key=""
        ))
        with pytest.raises(CommerceError, match="unavailable"):
            commerce.payment_mode(db, site, config)


def test_webhook_secret_dispatches_by_site_mode_and_rejects_mismatch(monkeypatch):
    with workspace(monkeypatch) as (db, operator, _, site, config):
        sandbox_secret = "whsec_sandbox_fixture"
        prefix = f"FASTSHOP_STRIPE_{site.id.upper()}_"
        monkeypatch.setenv(prefix + "WEBHOOK_SECRET", sandbox_secret)
        config.mode = "sandbox"
        assert signature_secret(db, site, config) == ("sandbox", sandbox_secret)

        credential = stored_and_verified(db, operator, site)
        site.status = "published"
        site.hostname = "hooks.example.test"
        config.mode = "live"
        config.live_accepted_at = credential.verified_at
        config.live_accepted_by = operator.id
        config.live_credential_id = credential.id
        assert signature_secret(db, site, config) == ("live", LIVE_WEBHOOK)

        body = json.dumps({"id": "evt_live_fixture", "livemode": True}).encode()
        stamp = int(time.time())
        digest = hmac.new(
            LIVE_WEBHOOK.encode(), str(stamp).encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        assert verify_webhook(body, f"t={stamp},v1={digest}", LIVE_WEBHOOK)["id"] == "evt_live_fixture"
        with pytest.raises(CommerceError):
            verify_webhook(body, f"t={stamp},v1={digest}", sandbox_secret)


def test_live_validation_is_read_only_mocked_and_live_gateway_uses_stored_key(monkeypatch):
    seen = []

    def respond(request):
        seen.append((request.method, request.url.path, request.headers["Authorization"]))
        return httpx.Response(200, json={"id": "acct_fixture", "livemode": True})

    gateway = StripeGateway.for_live_validation(
        LIVE_KEY, LIVE_WEBHOOK, transport=httpx.MockTransport(respond)
    )
    assert gateway.validate_live_account() == {"connected": True, "mode": "live"}
    assert seen == [("GET", "/v1/account", f"Bearer {LIVE_KEY}")]

    with workspace(monkeypatch) as (db, operator, _, site, config):
        credential = stored_and_verified(db, operator, site)
        site.status = "published"
        site.hostname = "gateway.example.test"
        config.mode = "live"
        config.live_accepted_at = credential.verified_at
        config.live_accepted_by = operator.id
        config.live_credential_id = credential.id
        stored_gateway = StripeGateway(site, db=db, transport=httpx.MockTransport(respond))
        assert stored_gateway.live_mode and stored_gateway.key == LIVE_KEY


def test_operator_form_is_csrf_protected_never_echoes_secrets_and_merchant_page_is_badge_only(
    monkeypatch,
):
    from uuid import uuid4

    from app.db import SessionLocal
    from app.main import app
    from app.models import SiteStripeLiveCredential

    monkeypatch.setattr(live_credentials, "settings", SimpleNamespace(
        admin_email=settings.admin_email,
        store_key_encryption_key="route-encryption-material-for-tests",
    ))
    client = TestClient(app)
    login = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login.text).group(1)
    response = client.post("/login", data={
        "csrf_token": token,
        "email": settings.admin_email,
        "password": settings.admin_password,
    })
    assert response.status_code == 200
    with SessionLocal() as db:
        operator = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, operator.id, "Credential route", "credential-" + uuid4().hex[:8])
        commerce.settings_for(db, site, create=True)
        site_id = site.id
        db.commit()

    url = f"/admin/platform/sites/{site_id}/live-credentials"
    page = client.get(url)
    assert page.status_code == 200
    assert 'type="password"' in page.text
    assert LIVE_KEY not in page.text and LIVE_WEBHOOK not in page.text
    client.post(url + "/store", data={
        "csrf_token": "wrong", "secret_key": LIVE_KEY, "webhook_secret": LIVE_WEBHOOK,
    })
    with SessionLocal() as db:
        assert not db.scalar(select(SiteStripeLiveCredential).where(
            SiteStripeLiveCredential.site_id == site_id
        ))

    client.post(url + "/store", data={
        "csrf_token": token, "secret_key": LIVE_KEY, "webhook_secret": LIVE_WEBHOOK,
    })
    rendered = client.get(url).text
    assert "sk_live_****1234" in rendered
    assert LIVE_KEY not in rendered and LIVE_WEBHOOK not in rendered
    merchant_surface = client.get(f"/admin/sites/{site_id}/integrations").text
    assert "Live payments: not approved" in merchant_surface
    assert LIVE_KEY not in merchant_surface and LIVE_WEBHOOK not in merchant_surface
