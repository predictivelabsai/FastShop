"""Growth settings stay tenant-scoped, deterministic and provider independent."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fasthtml.common import fast_app, to_xml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

from app import content
from app import growth_settings as service
from app.growth_settings_models import (
    NotificationTemplate,
    ShippingRate,
    ShippingZone,
    StaffInvite,
    TaxRate,
)
from app.models import Base, CommerceMail, Membership, User
from app.services import CommerceError


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'growth.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="owner@example.test", name="Owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Settings shop", "settings-shop")
        db.commit()
        yield SimpleNamespace(db=db, site=site, owner=owner, engine=engine)
    engine.dispose()


def zone(store, **kwargs):
    row = ShippingZone(tenant_id=store.site.tenant_id, site_id=store.site.id,
                       name="Delivery", countries_json=["US"], **kwargs)
    store.db.add(row)
    store.db.flush()
    return row


def rate(store, zone, **kwargs):
    row = ShippingRate(tenant_id=store.site.tenant_id, site_id=store.site.id,
                       zone_id=zone.id, name="Standard", **kwargs)
    store.db.add(row)
    store.db.flush()
    return row


def test_shipping_fallback_threshold_and_specific_zone(store):
    db, site = store.db, store.site
    address = {"country": "US", "state": "CA"}
    assert service.shipping_amount(db, site, address, 1000) is None
    national = zone(store)
    rate(store, national, amount_minor=500, free_shipping_threshold_minor=2000)
    assert service.shipping_amount(db, site, address, 1999) == 500
    assert service.shipping_amount(db, site, address, 2000) == 0
    regional = zone(store, states_json=["CA"])
    rate(store, regional, amount_minor=800)
    assert service.shipping_amount(db, site, address, 2000) == 800
    assert service.shipping_amount(db, site, {"country": "US", "state": "NY"}, 2000) == 0
    with pytest.raises(CommerceError, match="No delivery"):
        service.shipping_amount(db, site, {"country": "CA"}, 2000)


def test_price_weight_ranges_and_pickup_require_explicit_selection(store):
    delivery = zone(store)
    rate(store, delivery, amount_minor=400, kind="price_based", minimum=1000, maximum=2000)
    weighted = rate(store, delivery, amount_minor=600, kind="weight_based", minimum=500, maximum=1000)
    pickup = rate(store, delivery, amount_minor=0, local_pickup=True)
    address = {"country": "US"}
    assert service.shipping_amount(store.db, store.site, address, 1000) == 400
    assert service.shipping_amount(store.db, store.site, address, 2001, weight_grams=500) == 600
    assert service.shipping_amount(store.db, store.site, address, 1000, rate_id=pickup.id) == 0
    with pytest.raises(CommerceError):
        service.shipping_amount(store.db, store.site, address, 2001, weight_grams=1001)
    assert service.shipping_options(store.db, store.site, address, 2001, weight_grams=500)[0]["id"] == weighted.id


def test_tax_rounding_inclusive_and_region_priority(store):
    db, site = store.db, store.site
    assert service.tax_amount(db, site, {"country": "US"}, 100) is None
    db.add(TaxRate(tenant_id=site.tenant_id, site_id=site.id, country="US", rate_bps=1000))
    db.flush()
    assert service.tax_amount(db, site, {"country": "US"}, 105)["amount_minor"] == 11
    db.add(TaxRate(tenant_id=site.tenant_id, site_id=site.id, country="US", region="CA",
                   rate_bps=2000, prices_include_tax=True))
    db.flush()
    result = service.tax_amount(db, site, {"country": "US", "state": "CA"}, 1200)
    assert result["amount_minor"] == 200 and result["prices_include_tax"]
    assert service.tax_amount(db, site, {"country": "US", "state": "NY"}, 1200)["amount_minor"] == 120


def test_invite_accept_new_user_single_use_and_mail_queue(store):
    db, site, owner = store.db, store.site, store.owner
    invite = service.invite_staff(db, site, owner.id, " Staff@Example.Test ", "editor")
    mail = db.scalar(select(CommerceMail))
    assert mail.kind == "staff_invite" and mail.reference_json == {"invite_id": invite.id}
    token = service.invite_token(invite)
    assert token not in str(mail.reference_json) and invite.token_hash != token
    user, accepted = service.accept_invite(db, token, name="New Staff", password="long-test-password-for-staff", confirmation="long-test-password-for-staff")
    assert user.email == "staff@example.test" and user.password_hash and accepted.accepted_at
    assert db.scalar(select(Membership.role).where(Membership.user_id == user.id)) == "editor"
    with pytest.raises(CommerceError, match="invalid or has expired"):
        service.accept_invite(db, token)


def test_existing_user_requires_matching_authenticated_account(store):
    db, site = store.db, store.site
    user = User(email="staff@example.test", name="Staff")
    db.add(user)
    db.flush()
    invite = service.invite_staff(db, site, store.owner.id, user.email, "merchant")
    token = service.invite_token(invite)
    for user_id in (None, store.owner.id):
        with pytest.raises(CommerceError, match="Sign in with the invited"):
            service.accept_invite(db, token, user_id=user_id)
    result, _ = service.accept_invite(db, token, user_id=user.id)
    assert result.id == user.id


def test_expired_invite_and_staff_authorisation(store):
    db, site = store.db, store.site
    outsider = User(email="other@example.test")
    db.add(outsider)
    db.flush()
    with pytest.raises(CommerceError, match="Only administrators"):
        service.invite_staff(db, site, outsider.id, "new@example.test", "editor")
    invite = service.invite_staff(db, site, store.owner.id, "new@example.test", "editor")
    invite.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(CommerceError, match="invalid or has expired"):
        service.pending_invite(db, service.invite_token(invite))
    with pytest.raises(CommerceError, match="Choose an administrator"):
        service.invite_staff(db, site, store.owner.id, "new@example.test", "owner")


def test_last_admin_guard_and_tenant_isolation(store):
    db, site, owner = store.db, store.site, store.owner
    admin = db.scalar(select(Membership).where(Membership.user_id == owner.id))
    for kwargs in ({"remove": True}, {"role": "editor"}):
        with pytest.raises(CommerceError, match="at least one administrator"):
            service.change_staff(db, site, owner.id, admin.id, **kwargs)
    other = User(email="second@example.test")
    db.add(other)
    db.flush()
    foreign = content.create_site(db, other.id, "Other", "other-store")
    foreign_member = db.scalar(select(Membership).where(Membership.tenant_id == foreign.tenant_id))
    with pytest.raises(CommerceError, match="not found"):
        service.change_staff(db, site, owner.id, foreign_member.id, remove=True)
    db.add(Membership(tenant_id=site.tenant_id, user_id=other.id, role="admin"))
    db.flush()
    other.is_active = False
    db.flush()
    with pytest.raises(CommerceError, match="at least one administrator"):
        service.change_staff(db, site, owner.id, admin.id, role="merchant")
    other.is_active = True
    db.flush()
    service.change_staff(db, site, owner.id, admin.id, role="merchant")
    assert admin.role == "merchant"
    with pytest.raises(CommerceError, match="Only administrators"):
        service.change_staff(db, site, owner.id, admin.id, role="admin")


def test_notification_escaping_safe_syntax_and_disabled(store):
    assert service.render_template("Hello {{ customer_name }}", "<script>{{ message }}</script>",
        {"customer_name": '<img src=x onerror="alert(1)">', "message": "<b>Hi</b>"}) == (
        'Hello &lt;img src=x onerror=&quot;alert(1)&quot;&gt;', '&lt;script&gt;&lt;b&gt;Hi&lt;/b&gt;&lt;/script&gt;')
    for body in ("{{ unknown }}", "{{ customer.__class__ }}", "{{ broken", "{{ x() }}"):
        with pytest.raises(CommerceError, match="placeholder"):
            service.render_template("Subject", body, {})
    with pytest.raises(CommerceError):
        service.render_template("Bad\r\nSubject", "Body", {})
    db, site = store.db, store.site
    default = ("Subject", "Body")
    assert service.notification_message(db, site, "refund", {}, default=default) == default
    db.add(NotificationTemplate(tenant_id=site.tenant_id, site_id=site.id, event="refund",
        subject="{{ store_name }}", body="{{ total }}", enabled=False))
    db.flush()
    assert service.notification_message(db, site, "refund", {}, default=default) is None


def test_settings_routes_csrf_and_tenant_isolation(store, monkeypatch):
    import app.growth_settings_routes as routes
    monkeypatch.setattr(routes, "SessionLocal", lambda: Session(store.engine, expire_on_commit=False))
    application, rt = fast_app(default_hdrs=False)
    application.add_middleware(SessionMiddleware, secret_key="settings-test-secret")
    def actor(session):
        return store.owner.id
    def csrf(session):
        from fasthtml.common import Input
        session["csrf_token"] = "expected-csrf"
        return Input(type="hidden", name="csrf_token", value="expected-csrf")
    def check_csrf(session, values):
        if session.get("csrf_token") != values.get("csrf_token") or not session.get("csrf_token"):
            raise CommerceError("CSRF check failed")
    def shell(title, *children):
        return str(to_xml(Div(*children)))
    from fasthtml.common import Div
    from starlette.responses import Response
    routes.register_growth_settings_routes(rt, actor, csrf, check_csrf, shell,
        lambda exc: Response(str(exc), status_code=400))
    client = TestClient(application)
    path = f"/admin/sites/{store.site.id}/settings/users"
    assert client.get(path).status_code == 200
    assert client.post(path + "/invite", data={"email": "new@example.test", "role": "editor"}).status_code == 400
    assert client.post(path + "/invite", data={"csrf_token": "expected-csrf", "email": "new@example.test", "role": "editor"}, follow_redirects=False).status_code == 303
    assert client.get("/admin/sites/foreign/settings/users").status_code == 400
    with Session(store.engine) as db:
        invite = db.scalar(select(StaffInvite).where(StaffInvite.email == "new@example.test"))
        token = service.invite_token(invite)
    invitee = TestClient(application)
    assert invitee.get("/staff/accept", params={"token": token}).status_code == 200
    values = {"token": token, "name": "New staff", "password": "a-long-safe-password-for-staff", "confirmation": "a-long-safe-password-for-staff"}
    assert invitee.post("/staff/accept", data=values).status_code == 400
    assert invitee.post("/staff/accept", data={**values, "csrf_token": "expected-csrf"}, follow_redirects=False).status_code == 303
    assert invitee.get("/staff/accept", params={"token": token}).status_code == 400


def test_invite_delivery_uses_invited_recipient_and_safe_template(store):
    from app.integrations.commerce_email import dispatch_mail
    invite = service.invite_staff(store.db, store.site, store.owner.id, "invited@example.test", "editor")
    mail_id = store.db.scalar(select(CommerceMail.id))
    store.db.add(NotificationTemplate(tenant_id=store.site.tenant_id, site_id=store.site.id,
        event="invite", subject="Join {{ store_name }}", body="Open {{ action_url }}"))
    store.db.commit()
    sent = []
    def sender(recipient, subject, body, **kwargs):
        sent.append((recipient, subject, body))
        return "sent", "fake-message-id"
    assert dispatch_mail(mail_id, sessions=lambda: Session(store.engine), sender=sender)
    assert sent == [(invite.email, "Join Settings shop", "Open " + service.invite_url(invite))]


def test_notification_test_is_sent_only_to_current_user(store):
    from app.integrations.commerce_email import dispatch_mail
    mail = CommerceMail(tenant_id=store.site.tenant_id, site_id=store.site.id,
        user_id=store.owner.id, kind="notification_test", dedupe_key="test-preview",
        reference_json={"subject": "Preview", "body": "Hello"}, available_at=datetime.now(UTC))
    store.db.add(mail)
    store.db.commit()
    sent = []
    def sender(recipient, subject, body, **kwargs):
        sent.append(recipient)
        return "sent", "fake-message-id"
    assert dispatch_mail(mail.id, sessions=lambda: Session(store.engine), sender=sender)
    assert sent == [store.owner.email]
