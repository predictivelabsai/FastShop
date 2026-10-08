"""Menu ownership, compatibility, publication, forms and JSON API."""

import copy
import os
import re
import subprocess
import sys
from uuid import uuid4

import pytest
from fastcore.xml import to_xml
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app import content
from app import site_builder_services as builder
from app import site_menus as menus
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Base, Membership, Site, SiteMenu, SitePage, User
from app.services import CommerceError
from app.site_ui import storefront


@pytest.fixture
def workspace():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="menus@example.test", name="Owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Menus", "menus-test")
        other = content.create_site(db, owner.id, "Other", "other-menus")
        db.commit()
        yield db, owner, site, other
    engine.dispose()


def page_item(path="/", label="Home", id="home"):
    return {"id": id, "label": label, "kind": "page", "path": path}


def test_seed_adaptation_is_idempotent_and_keeps_snapshots(workspace):
    db, _, site, _ = workspace
    assert {m.name for m in menus.menus_for(db, site)} == {"header", "footer"}
    db.execute(delete(SiteMenu).where(SiteMenu.site_id == site.id, SiteMenu.tenant_id == site.tenant_id))
    site.settings_json = site.settings_json | {"navigation": [{"label": "Draft", "path": "/"}]}
    assert menus.adapt_navigation(db, site)
    header = menus.get_menu(db, site, "header")
    assert header.items_json[0]["label"] == "Draft"
    assert header.published_items_json[0]["label"] == "Home"
    before = copy.deepcopy(header.items_json)
    assert menus.adapt_navigation(db, site)
    assert header.items_json == before


def test_missing_and_empty_menus_have_distinct_fallback(workspace):
    db, _, site, _ = workspace
    header = menus.get_menu(db, site, "header")
    db.delete(header)
    db.flush()
    assert menus.rendered_navigation(db, site, "header") == site.published_settings_json["navigation"]
    menus.put_menu(db, site, "header", [])
    assert menus.rendered_navigation(db, site, "header", preview=True) == []
    assert menus.rendered_navigation(db, site, "header") == site.published_settings_json["navigation"]
    menus.put_menu(db, site, "header", [], publish=True)
    assert menus.rendered_navigation(db, site, "header") == []


def test_invalid_legacy_navigation_retains_fallback(workspace):
    db, _, site, _ = workspace
    db.execute(delete(SiteMenu).where(SiteMenu.site_id == site.id, SiteMenu.tenant_id == site.tenant_id))
    site.settings_json = site.settings_json | {"navigation": [{"label": "Old link", "path": "/gone"}]}
    assert not menus.adapt_navigation(db, site)
    assert not menus.menus_for(db, site)
    assert menus.rendered_navigation(db, site, "header", preview=True) == site.settings_json["navigation"]


@pytest.mark.parametrize("item", [
    page_item("/missing"), page_item(label={"et": "Kodu"}), page_item(label={"bad_locale": "Home"}),
    page_item(label=3), page_item(label="x" * 201), page_item() | {"extra": "bad"},
    {"id": "x", "label": "Bad", "kind": "external", "url": "javascript:alert(1)"},
    {"id": "x", "label": "Bad", "kind": "external", "url": "//evil.test"},
    {"id": "x", "label": "Bad", "kind": "external", "url": "/missing"},
    {"id": "x", "label": "Bad", "kind": "anchor", "path": "/", "block_id": "missing"},
])
def test_invalid_items_rejected_atomically(workspace, item):
    db, _, site, _ = workspace
    original = copy.deepcopy(menus.get_menu(db, site, "header").items_json)
    with pytest.raises(CommerceError):
        menus.put_menu(db, site, "header", [item])
    assert menus.get_menu(db, site, "header").items_json == original


def test_bounds_duplicates_and_cross_site_targets(workspace):
    db, _, site, other = workspace
    foreign = content.create_page(db, other, "Private", "/private")
    for items in ([page_item(foreign.path)], [page_item(), page_item()], [page_item(id=str(i)) for i in range(41)]):
        with pytest.raises(CommerceError):
            menus.put_menu(db, site, "header", items)
    # A forged tenant/site pairing cannot retrieve a menu owned by another tenant.
    site_id = site.id
    site.id = other.id
    with db.no_autoflush:
        assert menus.get_menu(db, site, "header") is None
    site.id = site_id


def test_locales_draft_publication_anchor_and_external_render(workspace):
    db, _, site, _ = workspace
    page = next(p for p in content.site_pages(db, site) if p.path == "/")
    page.published_json = page.draft_json
    block_id = page.draft_json["blocks"][0]["id"]
    items = [page_item(label={"en": "Welcome", "et": "Tere"}),
        {"id": "anchor", "label": "Introduction", "kind": "anchor", "path": "/", "block_id": block_id},
        {"id": "external", "label": "Elsewhere", "kind": "external", "url": "https://example.test/path"}]
    menus.put_menu(db, site, "header", items)
    assert menus.rendered_navigation(db, site, "header")[0]["label"] == "Home"
    assert menus.rendered_navigation(db, site, "header", preview=True)[0]["label"] == "Welcome"
    menus.put_menu(db, site, "header", items, publish=True)
    html = "".join(str(to_xml(n)) for n in storefront(db, site, page, "/sites/menus-test", "csrf", "https://example.test/") if n is not None)
    assert f'href="/sites/menus-test/#block-{block_id}"' in html
    assert f'id="block-{block_id}"' in html
    assert 'href="https://example.test/path"' in html
    site.published_settings_json = site.published_settings_json | {"default_locale": "et"}
    assert menus.rendered_navigation(db, site, "header")[0]["label"] == "Tere"
    site.published_settings_json = site.published_settings_json | {"default_locale": "fr"}
    assert menus.rendered_navigation(db, site, "header")[0]["label"] == ""


def test_anchor_publish_requires_published_block(workspace):
    db, _, site, _ = workspace
    page = content.site_pages(db, site)[0]
    item = {"id": "anchor", "label": "Intro", "kind": "anchor", "path": page.path, "block_id": page.draft_json["blocks"][0]["id"]}
    menus.put_menu(db, site, "header", [item])
    with pytest.raises(CommerceError, match="visible block"):
        menus.put_menu(db, site, "header", [item], publish=True)


def test_navigation_command_and_undo_restore_menu(workspace):
    db, owner, site, _ = workspace
    menus.put_menu(db, site, "header", [page_item(label={"en": "Home", "et": "Kodu"})])
    before = builder.snapshot(db, site)
    change = builder.apply_operations(db, site.id, owner.id, before, [{"op": "navigation", "items": [{"label": "Start", "path": "/"}]}])
    assert menus.get_menu(db, site, "header").items_json[0]["label"] == {"en": "Start", "et": "Kodu"}
    assert menus.get_menu(db, site, "header").items_json[0]["id"] == "home"
    builder.undo_change(db, site.id, owner.id, change.id, site.version)
    assert menus.snapshot(db, site) == before["menus"]


def test_database_cascades_site_menus(workspace):
    db, _, site, _ = workspace
    # Remove unrelated page children first; turn on SQLite FK enforcement outside a transaction.
    db.execute(delete(SitePage).where(SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id))
    site_id, tenant_id = site.id, site.tenant_id
    db.commit()
    db.execute(text("PRAGMA foreign_keys=ON"))
    db.execute(delete(Site).where(Site.id == site_id, Site.tenant_id == tenant_id))
    db.flush()
    assert not db.scalar(select(SiteMenu).where(SiteMenu.site_id == site_id, SiteMenu.tenant_id == tenant_id))


def test_upgrade_adapts_persisted_legacy_rows(tmp_path):
    url = "sqlite:///" + (tmp_path / "migration.db").as_posix()
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = User(email="migration@example.test", name="Migration")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Existing site", "existing-site")
        site_id, tenant_id = site.id, site.tenant_id
        db.commit()
    SiteMenu.__table__.drop(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('20261007_0016')"))
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
        env=os.environ | {"DB_URL": url, "FASTSHOP_ENV": "development"}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with Session(engine) as db:
        saved = list(db.scalars(select(SiteMenu).where(SiteMenu.site_id == site_id, SiteMenu.tenant_id == tenant_id)))
        assert {menu.name for menu in saved} == {"header", "footer"}
        assert all(menu.items_json[0]["label"] == "Home" for menu in saved)
    engine.dispose()


def test_menu_forms_api_csrf_stale_versions_and_permissions():
    client = TestClient(app)
    login = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login.text).group(1)
    assert client.post("/login", data={"csrf_token": token, "email": settings.admin_email, "password": settings.admin_password}).status_code == 200
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, owner.id, "Menu browser", "menu-" + uuid4().hex[:10])
        outsider = User(email=uuid4().hex + "@example.test", name="Other")
        db.add(outsider)
        db.flush()
        other = content.create_site(db, outsider.id, "Other", "other-" + uuid4().hex[:10])
        site_id, other_id, tenant_id, owner_id = site.id, other.id, site.tenant_id, owner.id
        db.commit()
    base = f"/admin/sites/{site_id}/menus"
    assert "Add an item" in client.get(base).text
    state = client.get(base + ".json").json()
    items = [page_item(label={"en": "Home", "et": "Kodu"}), page_item("/shop", "Shop", "shop")]
    payload = {"version": state["version"], "items": items, "csrf_token": token}
    assert client.post(base + "/header/api", json=payload | {"csrf_token": "bad"}).status_code == 400
    assert client.post(f"/admin/sites/{other_id}/menus/header/api", json=payload).status_code == 400
    response = client.post(base + "/header/api", json=payload)
    assert response.status_code == 200, response.text
    assert client.post(base + "/header/api", json=payload).status_code == 400
    version = response.json()["version"]
    for action, fields in [("edit", {"item_id": "home", "label": "Start", "kind": "page", "path": "/"}),
                           ("down", {"item_id": "home"}), ("remove", {"item_id": "shop"}),
                           ("add", {"label": "Contact", "kind": "page", "path": "/pages/contact"})]:
        response = client.post(base + "/header", data={"csrf_token": token, "version": version, "action": action, **fields}, follow_redirects=False)
        assert response.status_code == 303 and "Menu+saved" in response.headers["location"]
        state = client.get(base + ".json").json()
        version = state["version"]
    assert state["menus"]["header"][0]["label"] == {"en": "Start", "et": "Kodu"}
    assert len(state["menus"]["header"]) == 2
    response = client.post(base + "/header", data={"version": version, "action": "publish"}, follow_redirects=False)
    assert response.status_code == 303 and "session+expired" in response.headers["location"]
    with SessionLocal() as db:
        membership = db.scalar(select(Membership).where(Membership.tenant_id == tenant_id, Membership.user_id == owner_id))
        membership.role = "editor"
        db.commit()
    payload = {"version": version, "items": items, "csrf_token": token, "action": "publish"}
    assert client.post(base + "/header/api", json=payload).status_code == 400
    with SessionLocal() as db:
        membership = db.scalar(select(Membership).where(Membership.tenant_id == tenant_id, Membership.user_id == owner_id))
        membership.role = "admin"
        db.commit()
    assert client.post(base + "/header/api", json=payload).status_code == 200
