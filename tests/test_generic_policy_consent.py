"""Generic storefront policy pages use published content and sanitised settings."""

import copy

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Site, SitePage
from tests.test_site_routes import editorial_fixture  # noqa: F401


@pytest.fixture
def policy_store(editorial_fixture):
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "northstar"))
        page = db.scalar(select(SitePage).where(
            SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
            SitePage.path == "/pages/privacy-policy"))
        original_settings = copy.deepcopy(site.published_settings_json)
        original_page = (page.kind, copy.deepcopy(page.published_json))
        page.published_json = None
        site.published_settings_json = {
            key: value for key, value in original_settings.items() if key != "theme_pack"
        }
        db.commit()
        slug = site.slug

    yield TestClient(app), slug

    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == slug))
        page = db.scalar(select(SitePage).where(
            SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
            SitePage.path == "/pages/privacy-policy"))
        site.published_settings_json = original_settings
        page.kind, page.published_json = original_page
        db.commit()


def test_generic_policy_renders_sanitised_content_and_empty_message(policy_store):
    client, slug = policy_store
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == slug))
        site.published_settings_json = site.published_settings_json | {
            "policies": {"privacy": "<h2>Privacy details</h2><p>Stored policy.</p><script>alert(1)</script>"}
        }
        db.commit()

    response = client.get(f"/sites/{slug}/pages/privacy-policy")
    assert response.status_code == 200
    assert "Privacy policy" in response.text
    assert "<h2>Privacy details</h2><p>Stored policy.</p>" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "This policy has not been added yet." not in response.text

    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == slug))
        site.published_settings_json = site.published_settings_json | {"policies": {"privacy": ""}}
        db.commit()
    response = client.get(f"/sites/{slug}/pages/privacy-policy")
    assert response.status_code == 200
    assert "This policy has not been added yet." in response.text


def test_published_site_page_takes_precedence_over_policy_setting(policy_store):
    client, slug = policy_store
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == slug))
        page = db.scalar(select(SitePage).where(
            SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
            SitePage.path == "/pages/privacy-policy"))
        site.published_settings_json = site.published_settings_json | {
            "policies": {"privacy": "<p>Settings policy.</p>"}
        }
        page.kind = "page"
        page.published_json = {"title": "Merchant privacy page", "sections": [
            {"type": "text", "heading": "Privacy", "body": "Published page wins."}
        ]}
        db.commit()

    response = client.get(f"/sites/{slug}/pages/privacy-policy")
    assert response.status_code == 200
    assert "Merchant privacy page" in response.text
    assert "Published page wins." in response.text
    assert "Settings policy." not in response.text
