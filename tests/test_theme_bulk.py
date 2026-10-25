"""Bulk editing and theme promotion exercise reusable commands and real routes."""
import copy
from types import SimpleNamespace

import pytest
from fasthtml.common import Input, fast_app
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import (
    bulk_edit,
    catalog_admin,
    content,
    site_editor_routes,
    theme_bulk_routes,
    theme_library,
)
from app.models import Base, Membership, Product, Site, SitePage, Stock, User, Warehouse
from app.services import CommerceError
from app.theme_bulk_models import ThemeSnapshot


@pytest.fixture
def space(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="themes@example.test", name="Merchant")
        stranger = User(email="other-themes@example.test", name="Other merchant")
        db.add_all([owner, stranger])
        db.flush()
        site = content.create_site(db, owner.id, "Themes store", "themes-store")
        other = content.create_site(db, stranger.id, "Other themes", "other-themes")
        warehouse = Warehouse(tenant_id=site.tenant_id, code="main", name="Main")
        db.add(warehouse)
        db.flush()
        product = catalog_admin.create_product(db, site, {"name": "First product", "variants": [{
            "sku": "FIRST", "price_minor": 1000, "quantities": {warehouse.id: 8}}]})
        second = catalog_admin.create_product(db, site, {"name": "Second product"})
        foreign = catalog_admin.create_product(db, other, {"name": "Foreign product"})
        page = content.site_pages(db, site)[0]
        theme = theme_library.duplicate(db, site, owner.id)
        foreign_theme = theme_library.duplicate(db, other, stranger.id)
        db.commit()
    state = SimpleNamespace(user=owner.id)
    monkeypatch.setattr(theme_bulk_routes, "SessionLocal", sessions)
    monkeypatch.setattr(site_editor_routes, "SessionLocal", sessions)
    app, rt = fast_app(secret_key="theme-bulk-test-only")

    def actor(session):
        return state.user

    def csrf(session):
        session["csrf_token"] = "bulk-csrf"
        return Input(name="csrf_token", value="bulk-csrf")

    def check_csrf(session, form):
        if form.get("csrf_token") != "bulk-csrf":
            raise CommerceError("Your session expired.")

    def error(exc):
        return Response(str(exc), status_code=400)

    for register in (theme_bulk_routes.register_theme_bulk_routes,
                     site_editor_routes.register_site_editor_routes):
        register(rt, actor, csrf, check_csrf, lambda *args: args, error)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sessions=sessions, owner=owner, site=site,
            other=other, state=state, product=product, second=second, foreign=foreign,
            warehouse=warehouse, page=page, theme=theme, foreign_theme=foreign_theme,
            base=f"/admin/sites/{site.id}", headers={"X-CSRF-Token": "bulk-csrf"})
    engine.dispose()


def test_bulk_minor_units_stock_status_and_atomic_group_errors(space):
    w = space
    with w.sessions() as db:
        rows, _ = bulk_edit.rows_for(db, w.site, [w.product.id, w.second.id])
        first = next(r for r in rows if r["product_id"] == w.product.id)
        second = next(r for r in rows if r["product_id"] == w.second.id)
        first.update(price_minor=2345, compare_at_minor=3000, cost_minor=123,
            barcode="123456", track_inventory=False, status="active")
        first["quantities"][w.warehouse.id] = 10
        second["sku"] = "FIRST"  # Invalid row does not discard the valid product.
        result = bulk_edit.save_rows(db, w.site, [first, second], w.owner.id)
        assert result["saved"] == [first["id"]]
        assert second["id"] in result["errors"]
        saved = next(r for r in result["rows"] if r["id"] == first["id"])
        assert saved["price_minor"] == 2345 and saved["cost_minor"] == 123
        assert saved["quantities"][w.warehouse.id] == 10
        assert saved["barcode"] == "123456" and not saved["track_inventory"]
        assert db.get(Product, w.second.id).version == second["product_version"]
        db.commit()


def test_bulk_conflicts_cover_stock_and_tenant_isolation(space):
    w = space
    with w.sessions() as db:
        rows, _ = bulk_edit.rows_for(db, w.site, [w.product.id])
        row = rows[0]
        stock = db.scalar(select(Stock).where(Stock.variant_id == row["id"]))
        stock.quantity += 1
        db.flush()
        row["price_minor"] = 999
        result = bulk_edit.save_rows(db, w.site, [row], w.owner.id)
        assert "changed" in result["errors"][row["id"]]
        foreign_rows, _ = bulk_edit.rows_for(db, w.other, [w.foreign.id])
        rejected = bulk_edit.save_rows(db, w.site, foreign_rows, w.owner.id)
        assert not rejected["saved"] and "not found" in next(iter(rejected["errors"].values()))


def test_bulk_rejects_allocated_stock_and_malformed_values(space):
    w = space
    with w.sessions() as db:
        stock = db.scalar(select(Stock).where(Stock.variant_id == w.product.variants[0].id))
        stock.allocated = 4
        db.flush()
        rows, _ = bulk_edit.rows_for(db, w.site, [w.product.id])
        rows[0]["quantities"][w.warehouse.id] = 2
        result = bulk_edit.save_rows(db, w.site, rows, w.owner.id)
        assert "committed" in next(iter(result["errors"].values()))
        assert stock.quantity == 8
        rows[0]["quantities"] = "bad"
        assert bulk_edit.save_rows(db, w.site, rows, w.owner.id)["errors"]
        with pytest.raises(CommerceError):
            bulk_edit.save_rows(db, w.site, rows * 2, w.owner.id)


def test_bulk_routes_csrf_role_and_selected_grid(space):
    w = space
    page = w.client.get(w.base + "/products/bulk-edit", params={"ids": w.product.id})
    assert page.status_code == 200 and "bulk-editor.js" in page.text and "FIRST" in page.text
    assert "Foreign product" not in page.text and "Second product" not in page.text
    with w.sessions() as db:
        rows, _ = bulk_edit.rows_for(db, w.site, [w.product.id])
    path = w.base + "/products/bulk-edit"
    assert w.client.post(path, json={"rows": rows}).status_code == 403
    assert w.client.post(path, json={"rows": rows}, headers=w.headers).json()["saved"]
    with w.sessions() as db:
        membership = db.scalar(select(Membership).where(Membership.tenant_id == w.site.tenant_id))
        membership.role = "editor"
        db.commit()
    assert w.client.get(path, params={"ids": w.product.id}).status_code == 400
    assert w.client.post(path, json={"rows": rows}, headers=w.headers).status_code == 422
    assert w.client.get(f"/admin/sites/{w.other.id}/products/bulk-edit").status_code == 400


def test_theme_isolated_edit_publish_and_full_rollback(space):
    w = space
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        page = db.get(SitePage, w.page.id)
        original = copy.deepcopy(page.draft_json)
        content.save_page(db, site, page.id, w.owner.id, original, page.version, "publish")
        theme = theme_library.theme_for(db, site, w.theme.id)
        changed = copy.deepcopy(original)
        changed["title"] = "Library title"
        theme = theme_library.edit(db, site, w.owner.id, theme.id, page.id, changed, theme.version)
        preview_site, preview_page = theme_library.context(db, site, theme.id, page.id)
        assert preview_page.draft_json["title"] == "Library title"
        assert page.draft_json["title"] != "Library title"
        preview_site.settings_json["name"] = "Detached change"
        assert site.settings_json["name"] != "Detached change"
        previous = theme_library.publish(db, site, w.owner.id, theme.id, theme.version)
        assert db.get(SitePage, page.id).published_json["title"] == "Library title"
        theme_library.publish(db, site, w.owner.id, previous.id, previous.version)
        assert db.get(SitePage, page.id).published_json["title"] == original["title"]
        assert len(theme_library.themes_for(db, site)) == 3
        db.commit()


def test_theme_editor_context_csrf_version_and_preview(space):
    w = space
    query = {"theme": w.theme.id, "page": w.page.id}
    api = w.base + "/editor/api"
    loaded = w.client.get(api + "/document", params=query).json()
    assert loaded["version"] == 1 and loaded["pages"]
    assert w.client.get(w.base + "/editor", params=query).status_code == 200
    document = loaded["document"]
    document["title"] = "Snapshot only"
    values = {"document": document, "version": loaded["version"]}
    assert w.client.put(api + "/draft", params=query, json=values).status_code == 403
    saved = w.client.put(api + "/draft", params=query, json=values, headers=w.headers)
    assert saved.status_code == 200, saved.text
    assert w.client.put(api + "/draft", params=query, json=values, headers=w.headers).status_code == 409
    preview = w.client.get(w.base + f"/themes/{w.theme.id}/preview", params={"page": w.page.id})
    assert preview.status_code == 200 and "Snapshot only" in preview.text
    assert w.client.get(api + "/document", params={"theme": w.foreign_theme.id}).status_code == 404
    with w.sessions() as db:
        assert db.get(SitePage, w.page.id).draft_json["title"] != "Snapshot only"


def test_theme_publish_keeps_page_identity_after_a_handle_change(space):
    w = space
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        page = db.scalar(select(SitePage).where(SitePage.product_id == w.product.id,
                                              SitePage.site_id == site.id))
        original_id = page.id
        catalog_admin.update_product(db, site, w.product.id, {"handle": "new-handle"},
                                     expected_version=w.product.version)
        theme = theme_library.theme_for(db, site, w.theme.id)
        theme_library.publish(db, site, w.owner.id, theme.id, theme.version)
        matches = list(db.scalars(select(SitePage).where(SitePage.product_id == w.product.id,
                                                        SitePage.site_id == site.id)))
        assert len(matches) == 1 and matches[0].id == original_id
        assert matches[0].path == "/products/new-handle"


def test_theme_settings_save_uses_snapshot_version_without_live_changes(space):
    w = space
    api = w.base + "/editor/api/theme-settings"
    query = {"theme": w.theme.id}
    loaded = w.client.get(api, params=query).json()
    loaded["theme_settings"]["accent"] = "#1765b3"
    saved = w.client.put(api, params=query, json=loaded, headers=w.headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == loaded["version"] + 1
    assert w.client.put(api, params=query, json=loaded, headers=w.headers).status_code == 409
    with w.sessions() as db:
        assert db.get(ThemeSnapshot, w.theme.id).snapshot_json["settings"]["design"]["accent"] == "#1765b3"
        assert (db.get(Site, w.site.id).settings_json.get("design") or {}).get("accent") != "#1765b3"


def test_theme_library_routes_roles_rename_delete_and_atomic_rejection(space):
    w = space
    assert "Current theme" in w.client.get(w.base + "/themes").text
    path = w.base + f"/themes/{w.theme.id}"
    assert w.client.post(path + "/rename", data={"name": "Renamed", "version": 1}).status_code == 400
    renamed = w.client.post(path + "/rename", data={"csrf_token": "bulk-csrf", "name": "Renamed", "version": 1})
    assert renamed.status_code == 200 and "Renamed" in renamed.text
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        theme = db.get(ThemeSnapshot, w.theme.id)
        bad = copy.deepcopy(theme.snapshot_json["pages"][0]["document"])
        bad["blocks"] = [{"id": "bad", "type": "text", "heading": "A miracle cure", "body": "Bad"}]
        theme_library.edit(db, site, w.owner.id, theme.id, theme.snapshot_json["pages"][0]["id"], bad, theme.version)
        before = site.version
        with pytest.raises(CommerceError):
            theme_library.publish(db, site, w.owner.id, theme.id, theme.version)
        assert site.version == before
        assert len(theme_library.themes_for(db, site)) == 1
        db.rollback()
        membership = db.scalar(select(Membership).where(Membership.tenant_id == w.site.tenant_id))
        membership.role = "editor"
        db.commit()
    assert w.client.post(path + "/publish", data={"csrf_token": "bulk-csrf", "version": 2}).status_code == 400
    assert w.client.post(path + "/delete", data={"csrf_token": "bulk-csrf", "version": 2}).status_code == 400
    assert w.client.post(w.base + "/themes/duplicate", data={"csrf_token": "bulk-csrf", "name": "Another"}).status_code == 200
