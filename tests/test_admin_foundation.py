"""Isolated route coverage for the Merchant Admin foundation."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fasthtml.common import H1, Div, Input, fast_app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import content
from app.models import Base, Order, ShopCustomer, SiteOrder, SitePage, User
from app.services import CommerceError


@pytest.fixture
def foundation(monkeypatch):
    from app import admin_routes

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="foundation@example.test", name="Foundation Merchant")
        outsider = User(email="outsider@example.test", name="Another Merchant")
        db.add_all([owner, outsider])
        db.flush()
        site = content.create_site(db, owner.id, "Foundation store", "foundation-store")
        other = content.create_site(db, outsider.id, "Private store", "private-store")
        db.add_all([
            SitePage(tenant_id=site.tenant_id, site_id=site.id, title="Find own page",
                     path="/pages/find-own", draft_json={}),
            SitePage(tenant_id=other.tenant_id, site_id=other.id, title="Find secret page",
                     path="/pages/find-secret", draft_json={}),
        ])
        db.commit()
    monkeypatch.setattr(admin_routes, "SessionLocal", sessions)
    app, rt = fast_app(secret_key="foundation-test-only")

    def actor(session):
        return owner.id

    def csrf(session):
        return Input(name="csrf_token", type="hidden", value="foundation-csrf")

    def shell(title, *children):
        return Div(H1(title), *children)

    admin_routes.register_admin_routes(rt, actor, csrf, lambda *_: None, shell,
        lambda exc: Response(str(exc), status_code=400))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sessions=sessions, site=site, other=other, owner=owner)
    engine.dispose()


def test_search_scopes_results_and_rejects_non_members(foundation):
    w = foundation
    response = w.client.get(f"/admin/sites/{w.site.id}/search?q=find&format=json")
    assert response.status_code == 200
    results = response.json()["results"]
    assert any(row["title"] == "Find own page" for row in results)
    assert "secret" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert w.client.get(f"/admin/sites/{w.other.id}/search?q=find&format=json").status_code == 400
    html = w.client.get(f"/admin/sites/{w.site.id}/search?q=find")
    assert "Find own page" in html.text and "Find secret page" not in html.text


def test_search_matches_customer_and_order_number_or_email(foundation):
    w = foundation
    with w.sessions() as db:
        customer = ShopCustomer(tenant_id=w.site.tenant_id, site_id=w.site.id,
                                name="Search Buyer", email="search-buyer@example.test")
        db.add(customer)
        db.flush()
        order = Order(tenant_id=w.site.tenant_id, channel_id=w.site.channel_id,
                      number="FOUNDATION-123", idempotency_key="foundation-order",
                      email=customer.email, currency="USD", subtotal_minor=1200, total_minor=1200,
                      payment_status="paid", created_at=datetime.now(UTC))
        db.add(order)
        db.flush()
        db.add(SiteOrder(tenant_id=w.site.tenant_id, site_id=w.site.id,
                         customer_id=customer.id, order_id=order.id))
        db.commit()
    base = f"/admin/sites/{w.site.id}/search?format=json&q="
    assert any(row["type"] == "Order" for row in w.client.get(base + "FOUNDATION-123").json()["results"])
    results = w.client.get(base + "search-buyer").json()["results"]
    assert {row["type"] for row in results} == {"Order", "Customer"}


def test_dashboard_empty_and_seeded(foundation):
    w = foundation
    base = f"/admin/sites/{w.site.id}"
    empty = w.client.get(base)
    assert empty.status_code == 200
    assert "Total sales" in empty.text and "Setup guide" in empty.text
    assert "Your orders will appear here" in empty.text
    assert "Sessions" not in empty.text
    with w.sessions() as db:
        customer = ShopCustomer(tenant_id=w.site.tenant_id, site_id=w.site.id,
                                email="dashboard@example.test")
        db.add(customer)
        db.flush()
        order = Order(tenant_id=w.site.tenant_id, channel_id=w.site.channel_id,
                      number="HOME-REAL-ORDER", idempotency_key="home-order", email=customer.email,
                      currency="USD", subtotal_minor=4500, total_minor=4500,
                      payment_status="paid", created_at=datetime.now(UTC))
        db.add(order)
        db.flush()
        db.add(SiteOrder(tenant_id=w.site.tenant_id, site_id=w.site.id,
                         customer_id=customer.id, order_id=order.id))
        db.commit()
    seeded = w.client.get(base + "?days=1")
    assert seeded.status_code == 200
    assert "HOME-REAL-ORDER" in seeded.text and "1 order to fulfil" in seeded.text
    assert "Daily total sales" in seeded.text
    assert w.client.get(base + "?days=invalid").status_code == 200
    assert w.client.get(f"/admin/sites/{w.other.id}").status_code == 400


def test_settings_placeholder_and_pages(foundation):
    w = foundation
    prefix = f"/admin/sites/{w.site.id}"
    settings = w.client.get(prefix + "/settings")
    assert settings.status_code == 200
    for label in ("General", "Plan and billing", "Shipping and delivery", "Policies"):
        assert label in settings.text
    assert "data-settings-search" in settings.text
    placeholder = w.client.get(prefix + "/coming-soon/collections")
    assert placeholder.status_code == 200 and "Coming in this release" in placeholder.text
    pages = w.client.get(prefix + "/pages")
    assert pages.status_code == 200 and "Find own page" in pages.text
    assert "Find secret page" not in pages.text
    assert "csrf_token" in pages.text
    assert w.client.get(f"/admin/sites/{w.other.id}/settings").status_code == 400


def test_owned_search_boundary_requires_membership(foundation):
    with foundation.sessions() as db:
        with pytest.raises(CommerceError):
            content.owned_site(db, foundation.other.id, foundation.owner.id)


def test_search_products_stay_tenant_scoped_and_wildcards_are_literal(foundation):
    from app.site_catalog import create_catalog_product

    w = foundation
    with w.sessions() as db:
        for site, name in ((w.site, "Own needle 100%"), (w.other, "Secret needle 100%")):
            create_catalog_product(db, site, {
                "name": name, "slug": "needle", "subtitle": "", "description": "",
                "image_url": "", "category_slug": "general", "category_name": "General",
                "variants": [{"name": "Original", "price_minor": 1200}],
            })
        db.commit()
    prefix = f"/admin/sites/{w.site.id}/search"
    response = w.client.get(prefix, params={"q": "needle", "format": "json"})
    assert [row["title"] for row in response.json()["results"]] == ["Own needle 100%"]
    assert "Secret" not in response.text
    assert w.client.get(prefix, params={"q": "%", "format": "json"}).json()["results"]
    assert not w.client.get(prefix, params={"q": "_", "format": "json"}).json()["results"]


def test_editor_search_does_not_expose_manager_order_links(foundation):
    from sqlalchemy import select

    from app.models import Membership

    w = foundation
    with w.sessions() as db:
        customer = ShopCustomer(tenant_id=w.site.tenant_id, site_id=w.site.id,
                                email="editor-order@example.test")
        db.add(customer)
        db.flush()
        order = Order(tenant_id=w.site.tenant_id, channel_id=w.site.channel_id,
                      number="MANAGER-ONLY", idempotency_key="editor-search-order",
                      email=customer.email, currency="USD", subtotal_minor=1200,
                      total_minor=1200, payment_status="paid")
        db.add(order)
        db.flush()
        db.add(SiteOrder(tenant_id=w.site.tenant_id, site_id=w.site.id,
                         customer_id=customer.id, order_id=order.id))
        db.scalar(select(Membership).where(Membership.tenant_id == w.site.tenant_id,
                                            Membership.user_id == w.owner.id)).role = "editor"
        db.commit()
    response = w.client.get(f"/admin/sites/{w.site.id}/search?q=MANAGER-ONLY&format=json")
    assert response.status_code == 200 and response.json()["results"] == []
    dashboard = w.client.get(f"/admin/sites/{w.site.id}")
    assert dashboard.status_code == 200 and "MANAGER-ONLY" not in dashboard.text
