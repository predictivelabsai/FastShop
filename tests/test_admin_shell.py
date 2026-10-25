"""Presentation integration and mutation protection for the shared admin shell."""

import re
from types import SimpleNamespace

import pytest
from fasthtml.common import to_xml
from sqlalchemy import select
from starlette.testclient import TestClient

from app.admin_ui import admin_page, nav_section
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Site


def merchant():
    client = TestClient(app)
    login = client.get("/login?next=/admin/sites")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login.text).group(1)
    response = client.post("/login", data={"csrf_token": token, "email": settings.admin_email,
                                           "password": settings.admin_password})
    assert response.status_code == 200
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you"))
    return client, site, token


def test_shell_components_and_active_navigation():
    site = SimpleNamespace(id="store-one", name="My store")
    html = str(to_xml(admin_page("Products", site=site, sites=[site],
                                path="/admin/sites/store-one/products")))
    assert 'aria-label="Main navigation"' in html
    assert re.search(r'aria-current="page"[^>]*>.*?Products', html, re.S)
    assert "Collections" in html and "Inventory" in html
    assert "/admin/sites/store-one/collections" in html
    assert "Blog posts" not in html  # Only the active section expands.
    assert "admin/admin.css?v=" in html and "admin/admin.js?v=" in html
    assert 'aria-controls="admin-search-results"' in html
    assert "Skip to content" in html


@pytest.mark.parametrize(("suffix", "destinations"), [
    ("orders", ["orders", "drafts", "abandoned-checkouts"]),
    ("products", ["products", "collections", "inventory", "subscriptions"]),
    ("content/pages", ["content/pages", "content/blog-posts", "content/menus", "content/files"]),
])
def test_merged_navigation_links_to_implemented_routes(suffix, destinations):
    site = SimpleNamespace(id="store-one", name="My store")
    prefix = "/admin/sites/store-one/"
    html = str(to_xml(admin_page("Store", site=site, sites=[site], path=prefix + suffix)))
    for destination in [*destinations, "discounts", "analytics"]:
        assert f'href="{prefix}{destination}"' in html
    assert "/coming-soon/" not in html


@pytest.mark.parametrize(("suffix", "section"), [
    ("/orders/example/refund", "Orders"), ("/media", "Content"),
    ("/pages/example", "Content"), ("/build", "Online Store"),
    ("/settings/general", "Settings"), ("/coming-soon/collections", "Products"),
])
def test_detail_navigation_sections(suffix, section):
    assert nav_section("/admin/sites/store-one" + suffix) == section


@pytest.mark.parametrize("suffix", ["", "/products", "/orders", "/settings",
    "/settings/general", "/pages", "/menus", "/media", "/snippets", "/reviews",
    "/placeholders", "/customers", "/commerce", "/revenue", "/golive",
    "/integrations", "/inbox", "/samples", "/build", "/coming-soon/collections"])
def test_existing_site_surfaces_share_one_shell(suffix):
    client, site, _ = merchant()
    response = client.get(f"/admin/sites/{site.id}" + suffix)
    assert response.status_code == 200
    assert response.text.count('id="admin-main"') == 1
    assert 'aria-label="Store administration"' in response.text
    assert "admin/admin.css?v=" in response.text
    assert 'class="e-top"' not in response.text


def test_stores_chooser_and_notice_escape():
    client, _, _ = merchant()
    response = client.get("/admin/sites?notice=%3Cscript%3Ebad%3C/script%3E")
    assert "<h1>Stores</h1>" in response.text
    assert 'id="admin-main"' in response.text
    assert "&lt;script&gt;bad&lt;/script&gt;" in response.text
    assert "<script>bad</script>" not in response.text


def test_design_controls_highlight_the_design_sub_item():
    client, site, _ = merchant()
    response = client.get(f"/admin/sites/{site.id}/build?view=design")
    assert response.status_code == 200
    assert re.search(r'href="[^\"]+/build\?view=design"[^>]+aria-current="page"', response.text)


def test_visual_editor_navigation_and_classic_fallback_links():
    client, site, _ = merchant()
    prefix = f"/admin/sites/{site.id}"
    pages = client.get(prefix + "/pages")
    assert 'href="' + prefix + '/editor"' in pages.text
    assert "/editor?page=" in pages.text
    assert "Classic editor" in pages.text
    guided = client.get(prefix + "/build")
    assert 'href="' + prefix + '/editor"' in guided.text
    with SessionLocal() as db:
        from app import content
        page = content.site_pages(db, site)[0]
    classic = client.get(prefix + f"/pages/{page.id}")
    assert prefix + f"/editor?page={page.id}" in classic.text


def test_general_settings_keeps_csrf_and_version_checks():
    client, site, token = merchant()
    url = f"/admin/sites/{site.id}/settings"
    page = client.get(url + "/general")
    assert 'data-dirty-guard=""' in page.text
    assert client.post(url, data={"version": str(site.version)}).status_code == 400
    assert client.post(url, data={"csrf_token": "wrong", "version": str(site.version)}).status_code == 400
    assert client.post(url, data={"csrf_token": token, "version": "stale"}).status_code == 400
    with SessionLocal() as db:
        assert db.get(Site, site.id).version == site.version


def test_shell_sign_out_is_csrf_protected():
    client, _, token = merchant()
    assert client.post("/logout", data={"csrf_token": "wrong"}).status_code == 400
    assert client.post("/logout", data={"csrf_token": token}, follow_redirects=False).status_code == 303
    assert client.get("/admin/sites", follow_redirects=False).status_code == 303
