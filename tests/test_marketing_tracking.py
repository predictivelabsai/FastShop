"""Validation and tenant-scoped admin settings for tracking providers."""

import json
import re
from types import SimpleNamespace

import pytest
from fastcore.xml import to_xml
from fasthtml.common import fast_app
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import catalog_admin, content, site_tracking, site_tracking_routes
from app.admin_ui import AdminContextMiddleware, admin_page, admin_scope
from app.content_gap_models import SitePreferences
from app.models import (
    Base,
    CommerceQuote,
    Order,
    ProductVariant,
    SitePage,
    User,
    VariantChannelListing,
)
from app.services import CommerceError
from app.site_ui import storefront as render_storefront
from tests.test_checkout_services import ready  # noqa: F401
from tests.test_store_checkout_routes import csrf, storefront  # noqa: F401
from tests.test_us_commerce import DESTINATION, shop  # noqa: F401


@pytest.fixture
def tracking_admin(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="tracking-owner@example.test", name="Tracking owner")
        other_owner = User(email="tracking-other@example.test", name="Other owner")
        db.add_all([owner, other_owner])
        db.flush()
        site = content.create_site(db, owner.id, "Tracking store", "tracking-store")
        other_site = content.create_site(db, other_owner.id, "Other tracking store", "other-tracking-store")
        site.settings_json = {**site.settings_json, "ga4_measurement_id": "G-ABC1234567"}
        site.published_settings_json = {"ga4_measurement_id": "G-ABC1234567"}
        db.add(SitePreferences(
            tenant_id=site.tenant_id,
            site_id=site.id,
            analytics_id="G-OLD1234567",
            meta_pixel_id="123456789",
        ))
        db.commit()

    state = SimpleNamespace(user_id=owner.id)

    def actor(_session):
        return state.user_id

    def shell(title, *children):
        scope = admin_scope.get() or {}
        return admin_page(title, *children, site=site, sites=[site], user=owner,
                          path=scope.get("path", ""))

    app, rt = fast_app(secret_key="tracking-settings-test-only")
    app.add_middleware(AdminContextMiddleware)
    monkeypatch.setattr(site_tracking_routes, "SessionLocal", sessions)
    site_tracking_routes.register_site_tracking_routes(
        rt, actor, lambda _session: None, lambda _session, _form: None,
        shell, lambda exc: Response(str(exc), status_code=400),
    )
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sessions=sessions, site=site,
                              other_site=other_site, owner=owner,
                              other_owner=other_owner, state=state)
    engine.dispose()


def test_tracking_ids_accept_valid_formats_and_normalise_provider_codes():
    values = site_tracking.validate_tracking_ids({
        "ga4_measurement_id": "g-abc1234567",
        "google_ads_conversion_id": "aw-1234567890",
        "google_ads_purchase_label": "Purchase_1-ABC",
        "meta_pixel_id": "123456789012345",
        "tiktok_pixel_id": "c1234567890123456789",
    })

    assert values == {
        "ga4_measurement_id": "G-ABC1234567",
        "google_ads_conversion_id": "AW-1234567890",
        "google_ads_purchase_label": "Purchase_1-ABC",
        "meta_pixel_id": "123456789012345",
        "tiktok_pixel_id": "C1234567890123456789",
    }


@pytest.mark.parametrize(("field", "value"), [
    ("ga4_measurement_id", "G-123"),
    ("google_ads_conversion_id", "AW-not-numeric"),
    ("google_ads_purchase_label", "label with spaces"),
    ("meta_pixel_id", "123abc"),
    ("tiktok_pixel_id", "not-a-tiktok-pixel"),
])
def test_tracking_ids_reject_invalid_provider_formats(field, value):
    with pytest.raises(CommerceError):
        site_tracking.validate_tracking_ids({field: value})


def test_tracking_purchase_label_requires_a_google_ads_id():
    with pytest.raises(CommerceError, match="conversion ID"):
        site_tracking.validate_tracking_ids({"google_ads_purchase_label": "Purchase_1"})


def test_tracking_currency_values_use_currency_minor_unit_precision():
    assert site_tracking.major_units(125, "USD") == 1.25
    assert site_tracking.major_units(125, "JPY") == 125
    assert site_tracking.major_units(1250, "KWD") == 1.25


def test_tracking_values_migrate_from_legacy_preference_when_ga4_site_setting_is_empty(
    tracking_admin,
):
    w = tracking_admin
    with w.sessions() as db:
        site = db.get(type(w.site), w.site.id)

        values = site_tracking.tracking_ids(db, site, {})

    assert values["ga4_measurement_id"] == "G-OLD1234567"
    assert values["meta_pixel_id"] == "123456789"


def test_tracking_form_prefills_legacy_ga4_and_meta_values(tracking_admin):
    w = tracking_admin
    response = w.client.get(f"/admin/sites/{w.site.id}/marketing/tracking")

    assert response.status_code == 200
    assert 'value="G-ABC1234567"' in response.text
    assert 'value="123456789"' in response.text
    assert "Existing GA4, Google Analytics and Meta IDs are prefilled" in response.text
    assert "Google Ads conversion ID" in response.text
    assert "TikTok pixel ID" in response.text
    assert "store-tracking.js" not in response.text
    assert "googletagmanager.com" not in response.text
    assert "connect.facebook.net" not in response.text


def test_tracking_form_saves_draft_and_publishes_the_unified_values(tracking_admin):
    w = tracking_admin
    path = f"/admin/sites/{w.site.id}/marketing/tracking"
    fields = {
        "version": str(w.site.version),
        "ga4_measurement_id": "G-NEW1234567",
        "google_ads_conversion_id": "AW-1234567890",
        "google_ads_purchase_label": "Purchase_1",
        "meta_pixel_id": "987654321",
        "tiktok_pixel_id": "C9876543210123456789",
    }

    draft = w.client.post(path, data=fields | {"action": "draft"}, follow_redirects=False)
    assert draft.status_code == 303
    with w.sessions() as db:
        site = db.get(type(w.site), w.site.id)
        assert site.settings_json["tracking"] == {
            "ga4_measurement_id": "G-NEW1234567",
            "google_ads_conversion_id": "AW-1234567890",
            "google_ads_purchase_label": "Purchase_1",
            "meta_pixel_id": "987654321",
            "tiktok_pixel_id": "C9876543210123456789",
        }
        assert "ga4_measurement_id" not in site.settings_json
        assert site.published_settings_json["ga4_measurement_id"] == "G-ABC1234567"
        fields["version"] = str(site.version)

    published = w.client.post(path, data=fields | {"action": "publish"}, follow_redirects=False)
    assert published.status_code == 303
    with w.sessions() as db:
        site = db.get(type(w.site), w.site.id)
        assert site.published_settings_json["tracking"] == site.settings_json["tracking"]


def test_tracking_routes_enforce_site_ownership(tracking_admin):
    w = tracking_admin
    w.state.user_id = w.other_owner.id

    response = w.client.get(f"/admin/sites/{w.site.id}/marketing/tracking")

    assert response.status_code == 400
    assert "access denied" in response.text.lower()


def test_storefront_tags_require_published_tracking_ids(tracking_admin):
    w = tracking_admin
    with w.sessions() as db:
        site = db.get(type(w.site), w.site.id)
        site.status = "published"
        site.hostname = "tracking.example.test"
        site.published_settings_json = {"seo_indexing": True}
        preferences = db.scalar(select(SitePreferences).where(
            SitePreferences.site_id == site.id,
            SitePreferences.tenant_id == site.tenant_id,
        ))
        preferences.analytics_id = ""
        preferences.meta_pixel_id = ""
        product = catalog_admin.create_product(db, site, {
            "name": "Canvas tote",
            "slug": "canvas-tote",
            "status": "active",
            "is_published": True,
            "options": [],
        })
        variant = db.scalar(select(ProductVariant).where(
            ProductVariant.product_id == product.id,
            ProductVariant.tenant_id == site.tenant_id,
        ))
        db.add(VariantChannelListing(
            variant_id=variant.id,
            channel_id=site.channel_id,
            currency="USD",
            price_minor=2495,
        ))
        page = db.scalar(select(SitePage).where(
            SitePage.site_id == site.id,
            SitePage.tenant_id == site.tenant_id,
            SitePage.product_id == product.id,
        ))
        db.commit()

        def markup_for_page():
            nodes = render_storefront(
                db, site, page, f"/sites/{site.slug}", "tracking-test-csrf",
                f"https://tracking.example.test{page.path}",
            )
            return "".join(str(to_xml(node)) for node in nodes if node is not None)

        unconfigured = markup_for_page()
        assert "fastshop-tracking-config" not in unconfigured
        assert "store-tracking.js" not in unconfigured
        assert "googletagmanager.com" not in unconfigured
        assert "connect.facebook.net" not in unconfigured
        assert "analytics.tiktok.com" not in unconfigured

        site.published_settings_json = {
            **site.published_settings_json,
            "tracking": {"ga4_measurement_id": "G-TEST123456"},
        }
        db.commit()
        live = markup_for_page()
        assert "fastshop-tracking-config" in live
        assert "store-tracking.js" in live
        assert "googletagmanager.com/gtag/js" not in live
        payload = re.search(
            r'<script type="application/json" id="fastshop-tracking-config">(.*?)</script>',
            live,
            re.S,
        ).group(1)
        event = json.loads(payload)["event"]
        assert event["name"] == "view_item"
        assert event["currency"] == "USD"
        assert event["items"][0]["item_id"] == product.slug
        assert event["variants"]

        assert site_tracking.tracking_nodes(db, site, {}, preview=True) == []

        site.status = "preview"
        db.commit()
        assert "store-tracking.js" not in markup_for_page()


def test_paid_confirmation_tracking_payload_is_safe_and_correct(storefront):
    r, client, base, gateway = storefront
    r.site.status = "published"
    r.site.published_settings_json = {
        **r.site.published_settings_json,
        "tracking": {
            "ga4_measurement_id": "G-TEST123456",
            "google_ads_conversion_id": "AW-1234567890",
            "google_ads_purchase_label": "Purchase_1",
            "meta_pixel_id": "123456789",
            "tiktok_pixel_id": "C1234567890123456789",
        },
    }
    r.db.commit()

    cart = client.get(base + "/cart")
    assert cart.status_code == 200
    added = client.post(base + "/cart/add", data={
        "csrf_token": csrf(cart), "variant_id": r.variant.id, "quantity": "1",
    })
    assert added.status_code == 200 and "Test tablets" in added.text
    checkout = client.get(base + "/checkout")
    version = re.search(r'name="version" value="(\d+)"', checkout.text).group(1)
    begin_markup = re.search(
        r'<script type="application/json" id="fastshop-tracking-config">(.*?)</script>',
        checkout.text,
        re.S,
    ).group(1)
    begin = json.loads(begin_markup)
    assert begin["event"]["name"] == "begin_checkout"
    assert begin["event"]["value"] == 29.95
    assert begin["event"]["currency"] == "USD"
    assert "guest@example.test" not in begin_markup
    assert "store-tracking.js" in checkout.text
    review = client.post(base + "/checkout", data=DESTINATION | {
        "version": version,
        "csrf_token": csrf(checkout),
        "email": "guest@example.test",
        "full_name": "Guest Fixture",
    })
    assert review.status_code == 200 and "Review your order" in review.text

    quote = r.db.scalar(select(CommerceQuote).where(CommerceQuote.tenant_id == r.site.tenant_id))
    lines = [dict(line) for line in quote.snapshot_json["lines"]]
    lines[0]["name"] = "Test </script><script>alert(1)</script> tablets"
    quote.snapshot_json = {**quote.snapshot_json, "lines": lines}
    r.db.commit()

    attempt_url = review.url.path
    handoff = client.post(
        attempt_url + "/pay",
        data={"csrf_token": csrf(review)},
        follow_redirects=False,
    )
    assert handoff.status_code == 303
    next(iter(gateway.responses.values())).update(
        status="complete", payment_status="paid", payment_intent="pi_tracking_fixture",
    )
    confirmation = client.post(
        attempt_url + "/refresh",
        data={"csrf_token": csrf(review)},
    )
    assert "Payment confirmed" in confirmation.text
    purchase_markup = re.search(
        r'<script type="application/json" id="fastshop-tracking-config">(.*?)</script>',
        confirmation.text,
        re.S,
    ).group(1)
    purchase = json.loads(purchase_markup)
    order = r.db.scalar(select(Order).where(Order.tenant_id == r.site.tenant_id))
    assert purchase["event"]["name"] == "purchase"
    assert purchase["event"]["transaction_id"] == order.id
    assert purchase["event"]["value"] == order.total_minor / 100
    assert purchase["event"]["currency"] == order.currency
    assert purchase["event"]["items"][0]["item_name"].startswith("Test </script>")
    assert "\\u003c/script\\u003e" in purchase_markup
    assert "</script><script>alert(1)" not in purchase_markup
    assert "guest@example.test" not in purchase_markup
    assert "email" not in purchase["event"] and "phone" not in purchase["event"]
