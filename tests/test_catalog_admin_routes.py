"""Isolated role, CSRF, ownership and catalogue merchant route checks."""
from types import SimpleNamespace

import pytest
from fasthtml.common import Div, Input, fast_app
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import catalog_admin, catalog_admin_routes, content
from app.models import Base, Membership, Product, Stock, Tenant, User, Warehouse
from app.services import CommerceError


@pytest.fixture
def catalogue(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="catalogue@example.test", name="Merchant")
        outsider = User(email="catalogue-private@example.test", name="Private merchant")
        editor = User(email="catalogue-editor@example.test", name="Editor")
        db.add_all([owner, outsider, editor])
        db.flush()
        site = content.create_site(db, owner.id, "Catalogue shop", "catalogue-shop")
        other = content.create_site(db, outsider.id, "Private shop", "catalogue-private")
        db.get(Tenant, site.tenant_id).plan = "basic"
        db.add(Membership(tenant_id=site.tenant_id, user_id=editor.id, role="editor"))
        warehouse = Warehouse(tenant_id=site.tenant_id, code="MAIN", name="Main warehouse")
        db.add(warehouse)
        db.flush()
        for i in range(28):
            catalog_admin.create_product(db, site, {"name": f"Product {i:02}", "slug": f"product-{i}",
                "status": "active" if i % 2 else "draft", "vendor": "Acme" if i == 0 else "Other",
                "tags": ["featured"] if i == 0 else [], "options": []})
        secret = catalog_admin.create_product(db, other, {"name": "Private product", "slug": "private-product"})
        db.commit()
    monkeypatch.setattr(catalog_admin_routes, "SessionLocal", sessions)
    app, rt = fast_app(secret_key="catalogue-tests-only")
    current_actor = {"id": owner.id}

    def csrf(session):
        return Input(type="hidden", name="csrf_token", value="catalogue-test-csrf")

    def check_csrf(session, form):
        if form.get("csrf_token") != "catalogue-test-csrf":
            raise CommerceError("Invalid CSRF token.")

    catalog_admin_routes.register_catalog_admin_routes(rt, lambda session: current_actor["id"], csrf,
        check_csrf, lambda title, *children: Div(*children),
        lambda exc: Response(str(exc), status_code=400))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sessions=sessions, site=site, other=other,
                              owner=owner, editor=editor, secret=secret, warehouse=warehouse,
                              actor=current_actor, csrf="catalogue-test-csrf")
    engine.dispose()


def test_product_index_search_filters_and_pagination(catalogue):
    w = catalogue
    base = f"/admin/sites/{w.site.id}/products"
    response = w.client.get(base + "?sort=title")
    assert response.status_code == 200
    assert response.text.count('name="product_ids"') == 25
    assert "Private product" not in response.text
    assert 'Page 1 · 28 products' in response.text
    page = w.client.get(base + "?page=2&sort=title")
    assert page.text.count('name="product_ids"') == 3
    assert "Product 27" in page.text
    filtered = w.client.get(base + "?vendor=Acme&tag=featured&status=draft&q=Product")
    assert filtered.status_code == 200
    assert filtered.text.count('name="product_ids"') == 1
    assert "Product 00" in filtered.text
    assert w.client.get(base + "?q=no-matches").text.count('name="product_ids"') == 0


def test_all_catalogue_read_routes_deny_other_tenant(catalogue):
    w = catalogue
    for path in ("products", "products/new", "products/export", "products/import", "collections", "inventory"):
        assert w.client.get(f"/admin/sites/{w.other.id}/{path}").status_code == 400
    assert w.client.get(f"/admin/sites/{w.site.id}/products/{w.secret.id}").status_code == 400


def test_editor_cannot_mutate_catalogue_and_csrf_required(catalogue):
    w = catalogue
    base = f"/admin/sites/{w.site.id}"
    assert w.client.post(base + "/products/new", data={"name": "Rejected"}).status_code == 400
    assert w.client.post(base + "/products/bulk", data={"action": "draft"}).status_code == 400
    assert w.client.post(base + "/inventory", data={"quantity": 1}).status_code == 400
    assert w.client.post(base + "/collections/new", data={"title": "Rejected"}).status_code == 400
    w.actor["id"] = w.editor.id
    assert w.client.get(base + "/products").status_code == 200
    assert w.client.get(base + "/products/new").status_code == 400
    assert w.client.post(base + "/products/new", data={"csrf_token": w.csrf, "name": "Rejected"}).status_code == 400
    assert w.client.post(base + "/inventory/settings", data={"csrf_token": w.csrf, "threshold": 5}).status_code == 400


def test_create_product_update_version_bulk_and_csv_preview(catalogue):
    w = catalogue
    base = f"/admin/sites/{w.site.id}/products"
    form = {"csrf_token": w.csrf, "name": "Fresh product", "slug": "fresh-product", "status": "active",
            "published": "on", "price": "12.50", "sku": "FRESH", "options_json": "[]", "variants_json": "[]",
            "images_json": "[]", "tags": "new, featured", "description_html": '<p>Good <script>alert(1)</script></p>',
            "quantity_" + w.warehouse.id: 4}
    response = w.client.post(base + "/new", data=form, follow_redirects=False)
    assert response.status_code == 303, response.text
    with w.sessions() as db:
        product = db.scalar(select(Product).where(Product.slug == "fresh-product"))
        assert product and product.status == "active" and product.is_published
        assert "<script" not in product.description_html
        product_id, version = product.id, product.version
        assert db.scalar(select(Stock.quantity).where(Stock.variant_id == product.variants[0].id)) == 4
    edit = w.client.get(base + "/" + product_id)
    assert edit.status_code == 200
    assert 'aria-label="Description"' in edit.text
    assert 'data-catalog-editor' in edit.text
    form["version"] = version
    assert w.client.post(base + "/" + product_id, data=form, follow_redirects=False).status_code == 303
    assert w.client.post(base + "/" + product_id, data=form).status_code == 400
    assert w.client.post(base + "/bulk", data={"csrf_token": w.csrf, "product_ids": product_id, "action": "draft"}, follow_redirects=False).status_code == 303
    export = w.client.get(base + "/export?q=Fresh")
    assert export.status_code == 200 and "Variant Inventory Qty" in export.text
    preview = w.client.post(base + "/import", data={"csrf_token": w.csrf}, files={"file": ("products.csv", export.content, "text/csv")})
    assert preview.status_code == 200 and "Fresh product" in preview.text and "Import products" in preview.text
    with w.sessions() as db:
        assert db.scalar(select(Product).where(Product.id == product_id)).status == "draft"


def test_collection_rule_preview_and_inventory_history(catalogue):
    w = catalogue
    base = f"/admin/sites/{w.site.id}"
    preview = w.client.post(base + "/collections/preview", data={"csrf_token": w.csrf,
        "rules_json": '{"match":"all","rules":[{"field":"vendor","operator":"equals","value":"Acme"}]}'})
    assert preview.status_code == 200 and preview.json()["count"] == 1
    with w.sessions() as db:
        product = db.scalar(select(Product).where(Product.tenant_id == w.site.tenant_id, Product.slug == "product-0"))
        variant_id = product.variants[0].id
    response = w.client.post(base + "/inventory", data={"csrf_token": w.csrf, "variant_id": variant_id,
        "warehouse_id": w.warehouse.id, "quantity": 3, "reason": "received", "mode": "adjust", "note": "Route stock test"}, follow_redirects=False)
    assert response.status_code == 303
    history = w.client.get(base + "/inventory?q=Product+00")
    assert history.status_code == 200 and "Route stock test" in history.text and "3 available" in history.text
    assert w.client.post(base + "/inventory/settings", data={"csrf_token": w.csrf, "threshold": 10}, follow_redirects=False).status_code == 303
    assert w.client.get(base + "/inventory?low_stock=1").status_code == 200


def test_first_product_inventory_creates_a_default_location(catalogue):
    w = catalogue
    with w.sessions() as db:
        db.delete(db.get(Warehouse, w.warehouse.id))
        db.commit()
    base = f"/admin/sites/{w.site.id}/products"
    form = w.client.get(base + "/new")
    assert "Default location quantity" in form.text
    created = w.client.post(base + "/new", data={
        "csrf_token": w.csrf, "name": "Opening stock", "slug": "opening-stock",
        "status": "draft", "sku": "OPENING", "price": "12.50", "track_inventory": "on",
        "quantity_default": "6", "options_json": "[]", "variants_json": "[]", "images_json": "[]",
    }, follow_redirects=False)
    assert created.status_code == 303, created.text
    with w.sessions() as db:
        location = db.scalar(select(Warehouse).where(Warehouse.tenant_id == w.site.tenant_id))
        assert location.name == "Default location"
        product = db.scalar(select(Product).where(Product.tenant_id == w.site.tenant_id,
                                                  Product.slug == "opening-stock"))
        assert db.scalar(select(Stock.quantity).where(
            Stock.variant_id == product.variants[0].id, Stock.warehouse_id == location.id)) == 6


def test_retired_location_does_not_prevent_non_inventory_product_edits(catalogue):
    w = catalogue
    with w.sessions() as db:
        product = db.scalar(select(Product).where(Product.tenant_id == w.site.tenant_id))
        variant = product.variants[0]
        stock = Stock(variant_id=variant.id, warehouse_id=w.warehouse.id, quantity=9, allocated=0)
        db.add(stock)
        db.get(Warehouse, w.warehouse.id).is_active = False
        db.commit()
        product_id, version, slug = product.id, product.version, product.slug
        stock_id = stock.id
    base = f"/admin/sites/{w.site.id}/products/{product_id}"
    page = w.client.get(base)
    assert page.status_code == 200
    assert 'name="quantity_' + w.warehouse.id + '"' not in page.text
    result = w.client.post(base, data={"csrf_token": w.csrf, "version": version,
        "name": "Renamed with a retired location", "slug": slug, "status": "draft",
        "sku": variant.sku, "price": "12.00", "options_json": "[]",
        "variants_json": "[]", "images_json": "[]"}, follow_redirects=False)
    assert result.status_code == 303, result.text
    with w.sessions() as db:
        assert db.get(Product, product_id).name == "Renamed with a retired location"
        assert db.get(Stock, stock_id).quantity == 9
        assert len(list(db.scalars(select(Warehouse).where(Warehouse.tenant_id == w.site.tenant_id)))) == 1
