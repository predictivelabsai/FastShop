"""Merchant-managed PayPal and Postmark connections use isolated fake HTTP clients."""

from types import SimpleNamespace
from urllib.parse import unquote_plus

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import commerce, content, live_credentials, site_integrations
from app.integrations import commerce_email, paypal
from app.models import Base, Membership, SiteIntegrationCredential, User
from app.services import CommerceError


class FakeResponse:
    def __init__(self, body=None, status=200):
        self.body = body or {}
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            import httpx

            raise httpx.HTTPError("provider rejected request")

    def json(self):
        return self.body


class FakeHTTP:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return next(self.responses)

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return next(self.responses)


@pytest.fixture
def workspace(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(live_credentials, "settings", SimpleNamespace(
        store_key_encryption_key="integration-test-encryption-material-32-bytes",
        admin_email="operator@example.test",
    ))
    with Session(engine, expire_on_commit=False) as db:
        merchant = User(email="merchant@example.test", name="Merchant")
        stranger = User(email="stranger@example.test", name="Stranger")
        db.add_all([merchant, stranger])
        db.flush()
        site = content.create_site(db, merchant.id, "Integration shop", "integration-shop")
        foreign = content.create_site(db, stranger.id, "Other shop", "other-shop")
        db.scalar(select(Membership).where(
            Membership.tenant_id == site.tenant_id, Membership.user_id == merchant.id,
        )).role = "merchant"
        db.commit()
        yield db, site, foreign, merchant, stranger
    engine.dispose()


def test_encryption_round_trip_masking_and_paypal_precedence(workspace, monkeypatch):
    db, site, _, _, _ = workspace
    monkeypatch.setenv("FASTSHOP_PAYPAL_" + site.id.upper() + "_CLIENT_ID", "fallback-id")
    monkeypatch.setenv("FASTSHOP_PAYPAL_" + site.id.upper() + "_SECRET", "fallback-secret")
    site_integrations.save(db, site, "paypal", {
        "client_id": "merchant-client-id", "secret": "merchant-secret", "mode": "sandbox",
    }, "sandbox", {"client_id": site_integrations.mask("merchant-client-id")})
    db.commit()

    row = db.scalar(select(SiteIntegrationCredential).where(
        SiteIntegrationCredential.site_id == site.id,
        SiteIntegrationCredential.provider == "paypal",
    ))
    assert b"merchant-secret" not in row.encrypted_payload
    assert row.masked_hints_json["client_id"] == "••••t-id"
    assert site_integrations.payload_for(site, "paypal", db)["secret"] == "merchant-secret"
    assert paypal.credentials(site, db) == ("merchant-client-id", "merchant-secret", "sandbox")


def test_paypal_verification_success_and_failure():
    ok = FakeHTTP([FakeResponse({"access_token": "token"})])
    assert site_integrations.verify_paypal("client", "secret", "sandbox", client=ok)
    assert "api-m.sandbox.paypal.com" in ok.calls[0][1]
    rejected = FakeHTTP([FakeResponse(status=401)])
    assert not site_integrations.verify_paypal("client", "secret", "live", client=rejected)
    assert "api-m.paypal.com" in rejected.calls[0][1]


def test_postmark_verification_and_test_email():
    api = FakeHTTP([
        FakeResponse({"Name": "Store mail"}),
        FakeResponse({"SenderSignatures": [{"EmailAddress": "orders@example.test",
                                             "Confirmed": True}]}),
        FakeResponse({"Domains": []}),
    ])
    assert site_integrations.verify_postmark("token", "orders@example.test",
                                             account_token="account", client=api) == (
        True, "confirmed")
    failed = FakeHTTP([FakeResponse(status=401)])
    assert site_integrations.verify_postmark("bad", "orders@example.test", client=failed) == (
        False, "unverified")


def test_email_connection_precedence_and_disconnect(workspace, monkeypatch):
    db, site, _, _, _ = workspace
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "fallback-token")
    monkeypatch.setenv("FROM_EMAIL", "fallback@example.test")
    site_integrations.save(db, site, "email", {
        "token": "site-token", "account_token": "", "sender": "orders@example.test",
        "sender_name": "Shop",
    }, "default", {"sender": "orders@example.test", "sender_name": "Shop",
                    "sender_confirmation": "confirmed"})
    db.commit()
    assert site_integrations.mail_settings(site, db) == {
        "token": "site-token", "sender": "orders@example.test", "sender_name": "Shop",
    }
    api = FakeHTTP([FakeResponse({"ErrorCode": 0, "MessageID": "provider-id"})])
    monkeypatch.setattr(commerce_email.httpx, "post", api.post)
    assert commerce_email.send_email("buyer@example.test", "Subject", "Body",
        message_id="mail-id", site=site, db=db) == ("sent", "provider-id")
    assert api.calls[0][2]["headers"]["X-Postmark-Server-Token"] == "site-token"
    assert api.calls[0][2]["json"]["From"] == "Shop <orders@example.test>"
    assert "ReplyTo" not in api.calls[0][2]["json"]
    assert site_integrations.disconnect(db, site, "email")
    db.commit()
    assert site_integrations.mail_settings(site, db)["token"] == "fallback-token"
    assert site_integrations.disconnect(db, site, "email") is False


def test_platform_sender_uses_store_name_and_merchant_reply_to(workspace, monkeypatch):
    db, site, _, _, _ = workspace
    site.settings_json = {"name": "Editable brand", "email": "help@example.test"}
    site.published_settings_json = site.settings_json
    monkeypatch.setenv("POSTMARK_API_TOKEN", "fallback-token")
    monkeypatch.setenv("FROM_EMAIL", 'Platform sender <platform@example.test>')
    api = FakeHTTP([FakeResponse({"ErrorCode": 0, "MessageID": "provider-id"})])
    monkeypatch.setattr(commerce_email.httpx, "post", api.post)

    assert commerce_email.send_email("buyer@example.test", "Subject", "Body",
        message_id="mail-id", site=site, db=db) == ("sent", "provider-id")
    payload = api.calls[0][2]["json"]
    assert payload["From"] == "Editable brand <platform@example.test>"
    assert payload["ReplyTo"] == "help@example.test"


def test_platform_sender_omits_reply_to_without_site_contact_email(workspace, monkeypatch):
    db, site, _, _, _ = workspace
    site.settings_json = {"name": "Editable brand"}
    monkeypatch.setenv("POSTMARK_API_TOKEN", "fallback-token")
    monkeypatch.setenv("FROM_EMAIL", "platform@example.test")
    api = FakeHTTP([FakeResponse({"ErrorCode": 0, "MessageID": "provider-id"})])
    monkeypatch.setattr(commerce_email.httpx, "post", api.post)

    assert commerce_email.send_email("buyer@example.test", "Subject", "Body",
        message_id="mail-id", site=site, db=db) == ("sent", "provider-id")
    assert "ReplyTo" not in api.calls[0][2]["json"]


def test_platform_store_name_is_sanitised_for_from_header(workspace, monkeypatch):
    db, site, _, _, _ = workspace
    site.settings_json = {"name": 'Bad"\r\n<Bcc:attacker@example.test>'}
    site.published_settings_json = site.settings_json
    monkeypatch.setenv("POSTMARK_API_TOKEN", "fallback-token")
    monkeypatch.setenv("FROM_EMAIL", "platform@example.test")
    api = FakeHTTP([FakeResponse({"ErrorCode": 0, "MessageID": "provider-id"})])
    monkeypatch.setattr(commerce_email.httpx, "post", api.post)

    assert commerce_email.send_email("buyer@example.test", "Subject", "Body",
        message_id="mail-id", site=site, db=db) == ("sent", "provider-id")
    assert api.calls[0][2]["json"]["From"] == '"BadBcc:attacker@example.test" <platform@example.test>'


def test_postmark_test_message_uses_merchant_address(workspace):
    db, site, _, merchant, _ = workspace
    site_integrations.save(db, site, "email", {
        "token": "site-token", "account_token": "", "sender": "orders@example.test",
        "sender_name": "Shop",
    }, "default", {"sender": "orders@example.test", "sender_name": "Shop",
                    "sender_confirmation": "confirmed"})
    api = FakeHTTP([FakeResponse({"ErrorCode": 0})])
    assert site_integrations.send_test_email(site, merchant.email, db, client=api)
    assert api.calls[0][2]["json"]["To"] == merchant.email


def test_paypal_live_credentials_need_operator_approved_commerce_mode(workspace):
    db, site, _, _, _ = workspace
    commerce.settings_for(db, site, create=True)
    with pytest.raises(CommerceError, match="Commerce is not enabled"):
        if commerce.payment_mode(db, site) != "live":
            raise CommerceError("Live credentials require operator-approved live payments in Commerce settings.")


def test_admin_routes_enforce_csrf_membership_and_never_redisplay_secrets(
    workspace, monkeypatch,
):
    from fasthtml.common import Div, Input, fast_app
    from starlette.responses import Response
    from starlette.testclient import TestClient

    from app import site_integration_routes

    db, site, foreign, merchant, stranger = workspace
    editor = User(email="editor@example.test")
    db.add(editor)
    db.flush()
    db.add(Membership(tenant_id=site.tenant_id, user_id=editor.id, role="editor"))
    db.commit()
    def sessions():
        return Session(bind=db.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(site_integration_routes, "SessionLocal", sessions)
    monkeypatch.setattr(site_integrations, "verify_paypal", lambda *args, **kwargs: True)
    site_integrations.save(db, site, "email", {
        "token": "mail-token", "account_token": "", "sender": "orders@example.test",
        "sender_name": "Shop",
    }, "default", {"sender": "orders@example.test", "sender_name": "Shop",
                    "sender_confirmation": "not checked"})
    db.commit()
    test_recipients = []
    monkeypatch.setattr(site_integrations, "send_test_email",
                        lambda _site, recipient, _db: test_recipients.append(recipient) or True)
    app, rt = fast_app(secret_key="site-integration-test")
    current_actor = {"id": merchant.id}

    def check_csrf(_session, form):
        if form.get("csrf_token") != "integration-csrf":
            raise CommerceError("Invalid CSRF token.")

    site_integration_routes.register_integration_routes(
        rt, lambda _session: current_actor["id"],
        lambda _session: Input(type="hidden", name="csrf_token", value="integration-csrf"),
        check_csrf, lambda _title, *children: Div(*children),
        lambda exc: Response(str(exc), status_code=400),
    )
    base = f"/admin/sites/{site.id}/settings/integrations/paypal"
    payload = {"csrf_token": "integration-csrf", "client_id": "merchant-client",
               "secret": "never-render-this-secret", "mode": "sandbox"}
    with TestClient(app) as client:
        assert client.post(base + "/save", data={k: v for k, v in payload.items()
                                                   if k != "csrf_token"},
                           follow_redirects=False).status_code == 303
        assert client.get(base).status_code == 200
        saved = client.post(base + "/save", data=payload, follow_redirects=False)
        assert saved.status_code == 303
        assert "Connection verified and saved" in unquote_plus(saved.headers["location"])
        live = client.post(base + "/save", data=payload | {"mode": "live"},
                           follow_redirects=False)
        assert "Switch on live payments" in unquote_plus(live.headers["location"])
        page = client.get(base)
        assert page.status_code == 200
        assert "never-render-this-secret" not in page.text
        with sessions() as check_db:
            assert check_db.scalar(select(SiteIntegrationCredential).where(
                SiteIntegrationCredential.site_id == site.id,
                SiteIntegrationCredential.provider == "paypal",
            )) is not None
        test_email = client.post(
            f"/admin/sites/{site.id}/settings/integrations/email/send-test",
            data={"csrf_token": "integration-csrf"}, follow_redirects=False,
        )
        assert test_email.status_code == 303
        assert test_recipients == [merchant.email]
        assert client.post(f"/admin/sites/{foreign.id}/settings/integrations/paypal/save",
                           data=payload, follow_redirects=False).status_code == 303
        current_actor["id"] = editor.id
        assert client.post(base + "/disconnect", data=payload,
                           follow_redirects=False).status_code == 303
    with sessions() as check_db:
        assert check_db.scalar(select(SiteIntegrationCredential).where(
            SiteIntegrationCredential.site_id == site.id,
            SiteIntegrationCredential.provider == "paypal",
        )) is not None
