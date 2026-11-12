"""Admin Custom CSS controls and storefront injection order."""

import copy
import re
from uuid import uuid4

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app import content
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import SitePage, User


@pytest.fixture
def browser():
    client = TestClient(app)
    login_page = client.get("/login")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
    assert client.post("/login", data={
        "csrf_token": csrf_token,
        "email": settings.admin_email,
        "password": settings.admin_password,
    }).status_code == 200
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, owner.id, "Custom CSS browser", "custom-css-" + uuid4().hex[:10])
        page = next(page for page in content.site_pages(db, site) if page.path == "/")
        page.published_json = copy.deepcopy(page.draft_json)
        site.settings_json = site.settings_json | {"theme_pack": "liquid"}
        site.published_settings_json = site.published_settings_json | {"theme_pack": "liquid"}
        site.status = "published"
        db.commit()
        return client, csrf_token, site.id, site.slug, page.id


def test_custom_css_admin_controls_use_drafts_then_publish_last_in_the_liquid_head(browser):
    client, csrf_token, site_id, slug, page_id = browser
    build_url = f"/admin/sites/{site_id}/build?view=design&page={page_id}"
    page = client.get(build_url)

    assert page.status_code == 200
    assert "Custom CSS" in page.text
    assert "Preview draft" in page.text
    assert 'name="css"' in page.text
    assert "Published CSS history" in page.text

    css = ".metal-header { color: rgb(1, 2, 3); }"
    draft = client.post(f"/admin/sites/{site_id}/build/design-overrides", data={
        "csrf_token": csrf_token,
        "version": 0,
        "page_id": page_id,
        "action": "draft",
        "css": css,
    }, follow_redirects=False)
    assert draft.status_code == 303
    assert "Custom+CSS+draft+saved" in draft.headers["location"]

    public_before_publish = client.get(f"/sites/{slug}/").text
    preview = client.get(f"/admin/sites/{site_id}/preview/{page_id}").text
    with SessionLocal() as db:
        document = copy.deepcopy(db.get(SitePage, page_id).draft_json)
    canvas = client.post(
        f"/admin/sites/{site_id}/editor/api/render?page={page_id}",
        json={"document": document},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert 'data-fs-site-root=""' in preview
    assert "fs-design-overrides" in preview
    assert "rgb(1, 2, 3)" in preview
    assert canvas.status_code == 200
    assert "fs-design-overrides" in canvas.json()["html"]
    assert "rgb(1, 2, 3)" in canvas.json()["html"]
    assert "fs-design-overrides" not in public_before_publish

    published = client.post(f"/admin/sites/{site_id}/build/design-overrides", data={
        "csrf_token": csrf_token,
        "version": 1,
        "page_id": page_id,
        "action": "publish",
        "css": css,
        "note": "Adjust header colour",
    }, follow_redirects=False)
    assert published.status_code == 303
    assert "Custom+CSS+published" in published.headers["location"]

    public = client.get(f"/sites/{slug}/").text
    head = public.split("</head>", 1)[0]
    assert 'data-fs-site-root=""' in public
    assert "[data-fs-site-root] .metal-header" in head
    assert "rgb(1, 2, 3)" in head
    assert head.index("site-theme-liquid-editorial.css") < head.index("fs-design-overrides")
    assert "fs-design-overrides" not in client.get(build_url).text
    assert "Adjust header colour" in client.get(build_url).text


def test_custom_css_is_not_injected_into_another_storefront(browser):
    client, csrf_token, site_id, _, page_id = browser
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        other = content.create_site(db, owner.id, "No custom CSS", "no-custom-css-" + uuid4().hex[:10])
        page = next(page for page in content.site_pages(db, other) if page.path == "/")
        page.published_json = copy.deepcopy(page.draft_json)
        other.settings_json = other.settings_json | {"theme_pack": "liquid"}
        other.published_settings_json = other.published_settings_json | {"theme_pack": "liquid"}
        other.status = "published"
        db.commit()
        other_slug = other.slug

    saved = client.post(f"/admin/sites/{site_id}/build/design-overrides", data={
        "csrf_token": csrf_token,
        "version": 0,
        "page_id": page_id,
        "action": "publish",
        "css": ".metal-header { color: red; }",
    }, follow_redirects=False)
    assert saved.status_code == 303
    assert "fs-design-overrides" not in client.get(f"/sites/{other_slug}/").text
