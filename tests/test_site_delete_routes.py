"""Recoverable store deletion routes and visibility rules."""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from starlette.testclient import TestClient

from app.admin_entry import accessible_sites
from app.admin_gap_models import AdminActivity
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import (
    CheckoutAttempt,
    CommerceQuote,
    Membership,
    ShopCustomer,
    Site,
    SiteCommerceSettings,
    User,
    new_id,
)


def signed_in():
    client = TestClient(app)
    login = client.get("/login?next=/admin/sites")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login.text).group(1)
    response = client.post(
        "/login",
        data={
            "csrf_token": token,
            "email": settings.admin_email,
            "password": settings.admin_password,
        },
    )
    assert response.status_code == 200
    return client, token


def fixture_state():
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you"))
        user = db.scalar(select(User).where(User.email == settings.admin_email))
        member = db.scalar(
            select(Membership).where(
                Membership.tenant_id == site.tenant_id,
                Membership.user_id == user.id,
            )
        )
        commerce = db.scalar(
            select(SiteCommerceSettings).where(
                SiteCommerceSettings.site_id == site.id,
            )
        )
        return (
            site.id,
            member.id,
            member.role,
            site.status,
            site.hostname,
            commerce.mode if commerce else None,
        )


def reset_fixture(state):
    site_id, member_id, role, status, hostname, mode = state
    with SessionLocal() as db:
        site = db.get(Site, site_id)
        if site:
            site.status, site.hostname = status, hostname
            member = db.get(Membership, member_id)
            if member:
                member.role = role
            commerce = db.scalar(
                select(SiteCommerceSettings).where(
                    SiteCommerceSettings.site_id == site.id,
                )
            )
            if commerce and mode:
                commerce.mode = mode
            elif commerce and mode is None:
                db.delete(commerce)
            db.commit()


def test_delete_confirmation_visibility_and_platform_restore():
    state = fixture_state()
    site_id = state[0]
    client, token = signed_in()
    try:
        general = client.get(f"/admin/sites/{site_id}/settings/general")
        assert general.status_code == 200
        assert "Danger zone" in general.text
        confirm = client.get(f"/admin/sites/{site_id}/settings/delete")
        assert confirm.status_code == 200
        with SessionLocal() as db:
            site = db.get(Site, site_id)
            name, version = site.name, site.version
        assert f"Type {name} exactly to confirm." in confirm.text
        assert f"Type {name} to confirm deletion" in confirm.text
        base = f"/admin/sites/{site_id}/settings/delete"
        wrong = client.post(
            base, data={"csrf_token": token, "version": version, "confirmation": "wrong"}
        )
        assert wrong.status_code == 400
        with SessionLocal() as db:
            assert db.get(Site, site_id).status != "deleted"
        invalid_csrf = client.post(
            base, data={"csrf_token": "wrong", "version": version, "confirmation": name}
        )
        assert invalid_csrf.status_code == 400
        stale = client.post(
            base, data={"csrf_token": token, "version": version - 1, "confirmation": name}
        )
        assert stale.status_code == 400
        deleted = client.post(
            base,
            data={"csrf_token": token, "version": version, "confirmation": name},
            follow_redirects=False,
        )
        assert deleted.status_code == 303
        with SessionLocal() as db:
            site = db.get(Site, site_id)
            assert site.status == "deleted" and site.hostname is None
            user = db.scalar(select(User).where(User.email == settings.admin_email))
            assert site_id not in {row.id for row in accessible_sites(db, user.id)}
            deleted_version = site.version
            activity = db.scalar(
                select(AdminActivity).where(
                    AdminActivity.site_id == site_id,
                    AdminActivity.action == "deleted",
                )
            )
            assert activity and activity.summary
        assert client.get("/sites/h24you/").status_code == 404
        assert client.get("/sites/h24you/robots.txt").status_code == 404
        assert client.get("/sites/h24you/sitemap.xml").status_code == 404
        listing = client.get("/admin/sites")
        assert listing.status_code == 200 and name not in listing.text
        for suffix in ("/search?q=test", "/products", "/orders", "/customers"):
            assert client.get(f"/admin/sites/{site_id}{suffix}").status_code == 400
        revenue = client.get("/admin/platform/revenue")
        assert revenue.status_code == 200 and name not in revenue.text
        platform = client.get("/admin/platform/deleted-stores")
        assert platform.status_code == 200 and name in platform.text
        restored = client.post(
            f"/admin/platform/deleted-stores/{site_id}/restore",
            data={"csrf_token": token, "version": deleted_version},
            follow_redirects=False,
        )
        assert restored.status_code == 303
        with SessionLocal() as db:
            site = db.get(Site, site_id)
            assert site.status == "draft" and site.hostname is None
            commerce = db.scalar(
                select(SiteCommerceSettings).where(
                    SiteCommerceSettings.site_id == site.id,
                )
            )
            assert not commerce or commerce.mode == "disabled"
    finally:
        reset_fixture(state)


def test_delete_is_hidden_from_editors():
    state = fixture_state()
    client, _token = signed_in()
    try:
        with SessionLocal() as db:
            db.get(Membership, state[1]).role = "editor"
            db.commit()
        general = client.get(f"/admin/sites/{state[0]}/settings/general")
        assert general.status_code == 200 and "Danger zone" not in general.text
        assert client.get(f"/admin/sites/{state[0]}/settings/delete").status_code == 400
    finally:
        reset_fixture(state)


def test_delete_refuses_live_payments():
    state = fixture_state()
    client, token = signed_in()
    try:
        with SessionLocal() as db:
            site = db.get(Site, state[0])
            commerce = db.scalar(
                select(SiteCommerceSettings).where(
                    SiteCommerceSettings.site_id == site.id,
                )
            )
            if commerce:
                commerce.mode = "live"
            else:
                commerce = SiteCommerceSettings(
                    tenant_id=site.tenant_id, site_id=site.id, mode="live"
                )
                db.add(commerce)
            name, version = site.name, site.version
            db.commit()
        response = client.post(
            f"/admin/sites/{state[0]}/settings/delete",
            data={
                "csrf_token": token,
                "version": version,
                "confirmation": name,
            },
        )
        assert response.status_code == 400
        assert "Switch live payments off" in response.text
        with SessionLocal() as db:
            assert db.get(Site, state[0]).status != "deleted"
    finally:
        reset_fixture(state)


def test_delete_refuses_an_active_checkout():
    state = fixture_state()
    client, token = signed_in()
    attempt_id = quote_id = customer_id = None
    try:
        with SessionLocal() as db:
            site = db.get(Site, state[0])
            now = datetime.now(UTC)
            customer = ShopCustomer(
                tenant_id=site.tenant_id, site_id=site.id,
                email=f"delete-{new_id()}@example.test",
            )
            quote = CommerceQuote(
                tenant_id=site.tenant_id, site_id=site.id, fingerprint=new_id(),
                provider_id="test", currency="USD", subtotal_minor=100,
                discount_minor=0, shipping_minor=0, tax_minor=0, total_minor=100,
                expires_at=now + timedelta(hours=1), snapshot_json={},
            )
            db.add_all([customer, quote])
            db.flush()
            attempt = CheckoutAttempt(
                tenant_id=site.tenant_id, site_id=site.id,
                customer_id=customer.id, quote_id=quote.id,
                request_key=new_id(), request_hash=new_id(), state="open",
                expires_at=now + timedelta(hours=1),
            )
            db.add(attempt)
            db.commit()
            attempt_id, quote_id, customer_id = attempt.id, quote.id, customer.id
            name, version = site.name, site.version
        response = client.post(f"/admin/sites/{state[0]}/settings/delete", data={
            "csrf_token": token, "version": version, "confirmation": name,
        })
        assert response.status_code == 400
        assert "A checkout is in progress" in response.text
        with SessionLocal() as db:
            assert db.get(Site, state[0]).status != "deleted"
    finally:
        with SessionLocal() as db:
            if attempt_id:
                db.delete(db.get(CheckoutAttempt, attempt_id))
            if quote_id:
                db.delete(db.get(CommerceQuote, quote_id))
            if customer_id:
                db.delete(db.get(ShopCustomer, customer_id))
            db.commit()
        reset_fixture(state)


def test_seeders_do_not_modify_deleted_sites(monkeypatch):
    from app.site_seed import seed_h24you
    from app.site_seed_liquid import seed_h24you_liquid

    state = fixture_state()
    try:
        with SessionLocal() as db:
            site = db.get(Site, state[0])
            site.status = "deleted"
            site.hostname = None
            before = (
                site.version,
                site.status,
                dict(site.settings_json),
                dict(site.published_settings_json),
            )
            seed_h24you(db, settings.admin_email)
            assert (
                site.version,
                site.status,
                dict(site.settings_json),
                dict(site.published_settings_json),
            ) == before
            liquid = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
            assert liquid is not None
            liquid.status = "deleted"
            liquid.hostname = None
            liquid_before = (
                liquid.version,
                liquid.status,
                liquid.hostname,
                dict(liquid.settings_json),
            )
            monkeypatch.setenv("FASTSHOP_H24YOU_LIQUID_HOSTNAME", "deleted.example.test")
            seed_h24you_liquid(db, settings.admin_email)
            assert (
                liquid.version,
                liquid.status,
                liquid.hostname,
                dict(liquid.settings_json),
            ) == liquid_before
            db.rollback()
    finally:
        reset_fixture(state)
