"""Validation and tenant-scoped admin settings for tracking providers."""

from types import SimpleNamespace

import pytest
from fasthtml.common import fast_app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import content, site_tracking, site_tracking_routes
from app.admin_ui import AdminContextMiddleware, admin_page, admin_scope
from app.content_gap_models import SitePreferences
from app.models import Base, User
from app.services import CommerceError


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
