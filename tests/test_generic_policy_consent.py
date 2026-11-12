"""Generic storefront policy pages use published content and sanitised settings."""

import copy

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Site, SitePage
from tests.test_site_routes import editorial_fixture  # noqa: F401

POLICY_PAGES = (
    ("privacy-policy", "privacy", "Privacy policy"),
    ("terms-and-conditions", "terms", "Terms and conditions"),
    ("returns-and-refunds", "refunds", "Returns and refunds"),
    ("shipping-policy", "shipping", "Shipping policy"),
)


@pytest.fixture
def policy_store(editorial_fixture):
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "northstar"))
        pages = list(db.scalars(select(SitePage).where(
            SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
            SitePage.path.in_(["/pages/" + path for path, _, _ in POLICY_PAGES]),
        )))
        original_settings = copy.deepcopy(site.published_settings_json)
        original_pages = {page.id: (page.kind, copy.deepcopy(page.published_json)) for page in pages}
        for page in pages:
            page.published_json = None
        site.published_settings_json = {
            key: value for key, value in original_settings.items() if key != "theme_pack"
        }
        db.commit()
        slug = site.slug

    yield TestClient(app), slug

    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == slug))
        site.published_settings_json = original_settings
        pages = list(db.scalars(select(SitePage).where(SitePage.id.in_(original_pages))))
        for page in pages:
            page.kind, page.published_json = original_pages[page.id]
        db.commit()


def test_generic_policy_renders_sanitised_content_and_empty_message(policy_store):
    client, slug = policy_store
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == slug))
        site.published_settings_json = site.published_settings_json | {
            "policies": {
                "privacy": "<h2>Privacy details</h2><p>Stored policy.</p>"
                "<ul><li>Clear choices</li></ul><script>alert(1)</script>"
            }
        }
        db.commit()

    response = client.get(f"/sites/{slug}/pages/privacy-policy")
    assert response.status_code == 200
    assert "Privacy policy" in response.text
    assert "<h2>Privacy details</h2><p>Stored policy.</p>" in response.text
    assert "<ul><li>Clear choices</li></ul>" in response.text
    assert 'class="h-container h-policy-content"' in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "This policy has not been added yet." not in response.text

    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == slug))
        site.published_settings_json = site.published_settings_json | {"policies": {"privacy": ""}}
        db.commit()
    response = client.get(f"/sites/{slug}/pages/privacy-policy")
    assert response.status_code == 200
    assert "This policy has not been added yet." in response.text


@pytest.mark.parametrize(("path", "policy_key", "title"), POLICY_PAGES)
def test_default_policy_pages_have_one_heading(policy_store, path, policy_key, title):
    client, slug = policy_store
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == slug))
        site.published_settings_json = site.published_settings_json | {
            "policies": {policy_key: "<h2>Policy details</h2><p>Editable policy text.</p>"}
        }
        db.commit()

    response = client.get(f"/sites/{slug}/pages/{path}")

    assert response.status_code == 200
    assert response.text.count("<h1") == 1
    assert f">{title}</h1>" in response.text
    assert "<h2>Policy details</h2><p>Editable policy text.</p>" in response.text


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
