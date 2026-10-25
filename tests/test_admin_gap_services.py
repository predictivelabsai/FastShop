"""Personal views, append-only audit and safe tenant location management."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.orm import Session

from app import admin_views, catalog_admin, content, locations
from app.admin_activity import activities_for, activity_context
from app.admin_gap_models import AdminActivity
from app.models import Base, ProductVariant, Stock, User
from app.services import CommerceError


@pytest.fixture
def shop():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="views@example.test")
        other_user = User(email="private@example.test")
        db.add_all([user, other_user])
        db.flush()
        site = content.create_site(db, user.id, "Views", "views")
        other = content.create_site(db, other_user.id, "Private", "private")
        db.commit()
        yield SimpleNamespace(db=db, site=site, other=other, user=user, other_user=other_user)
    engine.dispose()


def test_personal_views_filters_sort_columns_and_isolation(shop):
    w = shop
    view = admin_views.save_view(w.db, w.site, w.user.id, "products", "Draft products",
        {"status": "draft", "secret": "never saved"}, "title", ["Product", "Status"])
    assert view.filters_json == {"status": "draft"}
    assert "status=draft" in admin_views.view_url(w.site, view)
    assert "sort=title" in admin_views.view_url(w.site, view)
    assert admin_views.columns_for(w.db, w.site, w.user.id, "products", view.id) == ["Product", "Status"]
    assert admin_views.views_for(w.db, w.site, w.other_user.id, "products") == []
    for site, user_id in ((w.other, w.user.id), (w.site, w.other_user.id)):
        with pytest.raises(CommerceError, match="not found"):
            admin_views.view_for(w.db, site, user_id, "products", view.id)
    admin_views.save_columns(w.db, w.site, w.user.id, "products", ["Product"])
    assert admin_views.columns_for(w.db, w.site, w.user.id, "products") == ["Product"]
    assert admin_views.columns_for(w.db, w.site, w.other_user.id, "products") == admin_views.COLUMNS["products"]
    with pytest.raises(CommerceError, match="already exists"):
        admin_views.save_view(w.db, w.site, w.user.id, "products", "Draft products", {}, "", ["Product"])
    with pytest.raises(CommerceError, match="at least one"):
        admin_views.save_columns(w.db, w.site, w.user.id, "products", ["invalid"])


def test_location_flags_default_and_committed_stock(shop):
    w = shop
    row = locations.save_location(w.db, w.site, dict(name="Main", country_code="US",
        is_active=True, fulfils_online_orders=True, is_default=True, address="Test Street"))
    product = catalog_admin.create_product(w.db, w.site, {"name": "Tote", "slug": "tote"})
    variant = w.db.scalar(select(ProductVariant).where(ProductVariant.product_id == product.id))
    stock = Stock(variant_id=variant.id, warehouse_id=row.id, quantity=10, allocated=3)
    w.db.add(stock)
    w.db.commit()
    with pytest.raises(CommerceError, match="committed"):
        locations.save_location(w.db, w.site, dict(name="Main", country_code="US", is_active=False), row.id)
    w.db.rollback()
    secondary = locations.save_location(w.db, w.site, dict(name="Secondary", country_code="US",
        is_active=True, fulfils_online_orders=True, is_default=True))
    assert secondary.is_default and not row.is_default
    assert catalog_admin.inventory_quantity(w.db, w.site, product) == 7
    stock.allocated = 0
    w.db.flush()
    locations.save_location(w.db, w.site, dict(name="Main", country_code="US", is_active=False), row.id)
    assert catalog_admin.inventory_quantity(w.db, w.site, product) == 0
    with pytest.raises(CommerceError, match="location or variant"):
        catalog_admin.adjust_inventory(w.db, w.site, variant.id, row.id, 1)
    with pytest.raises(CommerceError, match="not found"):
        locations.location_for(w.db, w.other, row.id)
    with pytest.raises(CommerceError, match="history"):
        locations.delete_location(w.db, w.site, row.id)


def test_audit_is_transactional_small_scoped_and_immutable(shop):
    w = shop
    with activity_context(w.db, w.site, w.user.id):
        product = catalog_admin.create_product(w.db, w.site, {"name": "Secret product title", "slug": "audit"})
        w.db.commit()
    rows = activities_for(w.db, w.site, object_type="product", object_id=product.id)
    assert len(rows) == 1 and rows[0].actor_id == w.user.id
    assert rows[0].summary == "Product created."
    assert "Secret" not in rows[0].summary
    assert activities_for(w.db, w.other) == []
    with activity_context(w.db, w.site, w.user.id):
        product.name = "Changed"
        w.db.flush()
        w.db.rollback()
    assert len(activities_for(w.db, w.site, object_type="product")) == 1
    record = w.db.get(AdminActivity, rows[0].id)
    record.summary = "Tampered"
    with pytest.raises(CommerceError, match="append-only"):
        w.db.flush()
    w.db.rollback()
    for statement in (delete(AdminActivity), update(AdminActivity).values(summary="Tampered")):
        with pytest.raises(CommerceError, match="append-only"):
            w.db.execute(statement)
    w.db.delete(w.db.get(AdminActivity, rows[0].id))
    with pytest.raises(CommerceError, match="append-only"):
        w.db.flush()
    w.db.rollback()
