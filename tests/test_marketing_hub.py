"""Tenant-scoped merchant marketing overview and navigation."""

from types import SimpleNamespace

import pytest
from fasthtml.common import fast_app, to_xml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import content, growth_content_routes, marketing_routes, site_seo_routes
from app.admin_ui import AdminContextMiddleware, admin_page, admin_scope, nav_section
from app.content_gap_models import SitePreferences, UrlRedirect
from app.models import (
    Base,
    Discount,
    MarketingConsent,
    ShopCustomer,
    SitePage,
    User,
)


@pytest.fixture
def marketing_hub(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="marketing-owner@example.test", name="Store owner")
        other_owner = User(email="marketing-other@example.test", name="Other owner")
        db.add_all([owner, other_owner])
        db.flush()
        site = content.create_site(db, owner.id, "Marketing store", "marketing-store")
        other_site = content.create_site(db, other_owner.id, "Other store", "other-marketing-store")
        page = db.query(SitePage).filter_by(site_id=site.id, tenant_id=site.tenant_id, path="/").one()
        page.published_json = {
            **(page.draft_json or {}),
            "title": "Published home title",
            "seo_title": "Published home title",
        }
        site.published_settings_json = {
            "seo_indexing": False,
            "seo_default_description": "A published description",
            "ga4_measurement_id": "G-ABC1234567",
        }
        preferences = SitePreferences(
            tenant_id=site.tenant_id,
            site_id=site.id,
            analytics_id="G-OLD1234567",
            meta_pixel_id="123456789",
        )
        own_customer = ShopCustomer(
            tenant_id=site.tenant_id, site_id=site.id, email="subscribed@example.test",
        )
        other_customer = ShopCustomer(
            tenant_id=other_site.tenant_id, site_id=other_site.id, email="foreign@example.test",
        )
        db.add_all([preferences, own_customer, other_customer])
        db.flush()
        db.add_all([
            UrlRedirect(tenant_id=site.tenant_id, site_id=site.id,
                        source="/old", target="/new"),
            UrlRedirect(tenant_id=other_site.tenant_id, site_id=other_site.id,
                        source="/foreign", target="/private"),
            Discount(tenant_id=site.tenant_id, site_id=site.id, title="Store discount"),
            Discount(tenant_id=other_site.tenant_id, site_id=other_site.id,
                     title="Other store discount"),
            MarketingConsent(tenant_id=site.tenant_id, site_id=site.id,
                             customer_id=own_customer.id, status="subscribed"),
            MarketingConsent(tenant_id=other_site.tenant_id, site_id=other_site.id,
                             customer_id=other_customer.id, status="subscribed"),
        ])
        db.commit()

    state = SimpleNamespace(user_id=owner.id)

    def actor(session):
        return state.user_id

    def shell(title, *children):
        scope = admin_scope.get() or {}
        return admin_page(
            title,
            *children,
            site=site,
            sites=[site],
            user=owner,
            path=scope.get("path", ""),
        )

    app, rt = fast_app(secret_key="marketing-hub-test-only")
    app.add_middleware(AdminContextMiddleware)
    monkeypatch.setattr(marketing_routes, "SessionLocal", sessions)
    monkeypatch.setattr(growth_content_routes, "SessionLocal", sessions)
    monkeypatch.setattr(site_seo_routes, "SessionLocal", sessions)
    def error(exc):
        return Response(str(exc), status_code=400)
    marketing_routes.register_admin_marketing_routes(rt, actor, shell, error)
    site_seo_routes.register_site_seo_routes(
        rt, actor, lambda session: None, lambda session, form: None, shell, error,
    )
    growth_content_routes.register_growth_content_routes(
        rt, actor, lambda session: None, lambda session, form: None, shell, error,
    )
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            sessions=sessions,
            site=site,
            other_site=other_site,
            owner=owner,
            other_owner=other_owner,
            state=state,
        )
    engine.dispose()


def test_marketing_hub_renders_for_owner(marketing_hub):
    w = marketing_hub
    response = w.client.get(f"/admin/sites/{w.site.id}/marketing")

    assert response.status_code == 200
    for heading in (
        "Marketing",
        "Search engine optimisation",
        "URL redirects",
        "Tracking and pixels",
        "Discounts",
        "Email subscribers",
    ):
        assert heading in response.text
    assert "Indexing: Off" in response.text
    assert "Home page title: Set" in response.text
    assert "Default description: Set" in response.text
    assert "1 URL redirect" in response.text
    assert "G-ABC1234567" in response.text
    assert "G-OLD1234567" in response.text
    assert "123456789" in response.text
    assert "1 active discount" in response.text
    assert "1 subscribed customer" in response.text
    assert "https://your-store.example/sitemap.xml" in response.text


def test_marketing_hub_denies_another_tenants_user(marketing_hub):
    w = marketing_hub
    w.state.user_id = w.other_owner.id

    response = w.client.get(f"/admin/sites/{w.site.id}/marketing")

    assert response.status_code == 400
    assert "access denied" in response.text.lower()


def test_marketing_hub_counts_are_tenant_scoped(marketing_hub):
    w = marketing_hub

    response = w.client.get(f"/admin/sites/{w.site.id}/marketing")

    assert response.status_code == 200
    assert "1 URL redirect" in response.text
    assert "2 URL redirects" not in response.text
    assert "1 active discount" in response.text
    assert "2 active discounts" not in response.text
    assert "1 subscribed customer" in response.text
    assert "2 subscribed customers" not in response.text


def test_seo_settings_header_links_back_to_marketing_hub(marketing_hub):
    w = marketing_hub

    response = w.client.get(f"/admin/sites/{w.site.id}/settings/seo")

    assert response.status_code == 200
    assert f'href="/admin/sites/{w.site.id}/marketing"' in response.text


@pytest.mark.parametrize("suffix", [
    "/marketing", "/settings/seo", "/redirects", "/discounts", "/integrations",
])
def test_marketing_routes_highlight_marketing_navigation(suffix):
    prefix = "/admin/sites/store-one"
    assert nav_section(prefix + suffix) == "Marketing"

    site = SimpleNamespace(id="store-one", name="My store")
    html = str(to_xml(admin_page(
        "Marketing", site=site, sites=[site], path=prefix + suffix,
    )))
    assert "Marketing" in html
    assert "aria-current=\"page\"" in html
    for destination in ("marketing", "settings/seo", "redirects", "discounts", "integrations"):
        assert f'href="{prefix}/{destination}"' in html
    assert "Marketing/Integrations" not in html
