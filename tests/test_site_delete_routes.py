"""Recoverable deletion routes and visibility rules."""

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app import content
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


@pytest.fixture
def store():
    suffix = uuid4().hex[:10]
    name = f"Deletion fixture {suffix}"
    slug = f"deletion-{suffix}"
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, user.id, name, slug)
        membership = db.scalar(select(Membership).where(
            Membership.tenant_id == site.tenant_id,
            Membership.user_id == user.id,
        ))
        result = SimpleNamespace(
            id=site.id,
            name=name,
            slug=slug,
            tenant_id=site.tenant_id,
            membership_id=membership.id,
        )
        db.commit()
    yield result
    with SessionLocal() as db:
        site = db.get(Site, result.id)
        if site:
            site.status = "deleted"
            site.hostname = None
            db.commit()


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


def test_delete_confirmation_visibility_and_platform_restore(store):
    client, token = signed_in()
    general = client.get(f"/admin/sites/{store.id}/settings/general")
    assert general.status_code == 200
    assert "Danger zone" in general.text
    confirm = client.get(f"/admin/sites/{store.id}/settings/delete")
    assert confirm.status_code == 200
    assert f"Type {store.name} exactly to confirm." in confirm.text
    assert f"Type {store.name} to confirm deletion" in confirm.text

    with SessionLocal() as db:
        site = db.get(Site, store.id)
        version = site.version
    base = f"/admin/sites/{store.id}/settings/delete"
    wrong = client.post(
        base,
        data={"csrf_token": token, "version": version, "confirmation": "wrong"},
    )
    assert wrong.status_code == 400
    with SessionLocal() as db:
        assert db.get(Site, store.id).status != "deleted"
    invalid_csrf = client.post(
        base,
        data={"csrf_token": "wrong", "version": version, "confirmation": store.name},
    )
    assert invalid_csrf.status_code == 400
    stale = client.post(
        base,
        data={"csrf_token": token, "version": version - 1, "confirmation": store.name},
    )
    assert stale.status_code == 400
    deleted = client.post(
        base,
        data={"csrf_token": token, "version": version, "confirmation": store.name},
        follow_redirects=False,
    )
    assert deleted.status_code == 303

    with SessionLocal() as db:
        site = db.get(Site, store.id)
        assert site.status == "deleted" and site.hostname is None
        deleted_version = site.version
        activity = db.scalar(select(AdminActivity).where(
            AdminActivity.site_id == store.id,
            AdminActivity.action == "deleted",
        ))
        assert activity and activity.summary
        commerce = db.scalar(select(SiteCommerceSettings).where(
            SiteCommerceSettings.site_id == store.id,
        ))
        assert not commerce or commerce.mode == "disabled"

    assert client.get(f"/sites/{store.slug}/").status_code == 404
    assert client.get(f"/sites/{store.slug}/robots.txt").status_code == 404
    assert client.get(f"/sites/{store.slug}/sitemap.xml").status_code == 404
    listing = client.get("/admin/sites")
    assert listing.status_code == 200 and store.name not in listing.text
    assert client.get(f"/admin/sites/{store.id}/settings/general").status_code == 400
    revenue = client.get("/admin/platform/revenue")
    assert revenue.status_code == 200 and store.name not in revenue.text
    platform = client.get("/admin/platform/deleted-stores")
    assert platform.status_code == 200 and store.name in platform.text

    restored = client.post(
        f"/admin/platform/deleted-stores/{store.id}/restore",
        data={"csrf_token": token, "version": deleted_version},
        follow_redirects=False,
    )
    assert restored.status_code == 303
    with SessionLocal() as db:
        site = db.get(Site, store.id)
        assert site.status == "draft" and site.hostname is None
        commerce = db.scalar(select(SiteCommerceSettings).where(
            SiteCommerceSettings.site_id == store.id,
        ))
        assert not commerce or commerce.mode == "disabled"


def test_delete_is_hidden_from_editors(store):
    client, _token = signed_in()
    with SessionLocal() as db:
        db.get(Membership, store.membership_id).role = "editor"
        db.commit()
    general = client.get(f"/admin/sites/{store.id}/settings/general")
    assert general.status_code == 200 and "Danger zone" not in general.text
    assert client.get(f"/admin/sites/{store.id}/settings/delete").status_code == 400


def test_delete_refuses_live_payments(store):
    client, token = signed_in()
    with SessionLocal() as db:
        site = db.get(Site, store.id)
        commerce = SiteCommerceSettings(
            tenant_id=site.tenant_id,
            site_id=site.id,
            mode="live",
        )
        db.add(commerce)
        version = site.version
        db.commit()
    response = client.post(
        f"/admin/sites/{store.id}/settings/delete",
        data={"csrf_token": token, "version": version, "confirmation": store.name},
    )
    assert response.status_code == 400
    assert "Switch live payments off" in response.text
    with SessionLocal() as db:
        assert db.get(Site, store.id).status != "deleted"


def test_delete_refuses_an_active_checkout(store):
    client, token = signed_in()
    with SessionLocal() as db:
        site = db.get(Site, store.id)
        now = datetime.now(UTC)
        customer = ShopCustomer(
            tenant_id=site.tenant_id,
            site_id=site.id,
            email=f"delete-{new_id()}@example.test",
        )
        quote = CommerceQuote(
            tenant_id=site.tenant_id,
            site_id=site.id,
            fingerprint=new_id(),
            provider_id="test",
            currency="USD",
            subtotal_minor=100,
            discount_minor=0,
            shipping_minor=0,
            tax_minor=0,
            total_minor=100,
            expires_at=now + timedelta(hours=1),
            snapshot_json={},
        )
        db.add_all([customer, quote])
        db.flush()
        attempt = CheckoutAttempt(
            tenant_id=site.tenant_id,
            site_id=site.id,
            customer_id=customer.id,
            quote_id=quote.id,
            request_key=new_id(),
            request_hash=new_id(),
            state="open",
            expires_at=now + timedelta(hours=1),
        )
        db.add(attempt)
        db.commit()
        version = site.version

    response = client.post(
        f"/admin/sites/{store.id}/settings/delete",
        data={"csrf_token": token, "version": version, "confirmation": store.name},
    )
    assert response.status_code == 400
    assert "A checkout is in progress" in response.text
    with SessionLocal() as db:
        assert db.get(Site, store.id).status != "deleted"
