"""Product status is enforced at public catalogue and checkout boundaries."""

from types import SimpleNamespace

import pytest
from fasthtml.common import fast_app
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app import commerce, content, services
from app.models import (
    Base,
    Category,
    Product,
    ProductType,
    ProductVariant,
    User,
    VariantChannelListing,
)
from app.seed import seed
from app.services import CommerceError


@pytest.fixture
def catalogue(monkeypatch):
    from app import site_routes

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        seed(db)
        owner = User(email="visibility@example.test", name="Catalogue Merchant")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Catalogue visibility", "catalogue-visibility")
        site.status = "preview"
        kind = ProductType(tenant_id=site.tenant_id, slug="goods", name="Goods")
        category = Category(tenant_id=site.tenant_id, slug="goods", name="Goods")
        db.add_all([kind, category])
        db.flush()
        product = Product(tenant_id=site.tenant_id, product_type_id=kind.id,
                          category_id=category.id, slug="sample", name="Catalogue Sample",
                          is_published=True)
        db.add(product)
        db.flush()
        variant = ProductVariant(tenant_id=site.tenant_id, product_id=product.id,
                                 name="Default", sku="SAMPLE")
        db.add(variant)
        db.flush()
        db.add(VariantChannelListing(variant_id=variant.id, channel_id=site.channel_id,
                                    currency="USD", price_minor=1250))
        page = content.create_page(db, site, product.name, "/products/sample", "product",
                                   {"title": product.name, "sections": [{
                                       "type": "product", "heading": product.name}]})
        page.product_id = product.id
        page.published_json = page.draft_json
        config = commerce.settings_for(db, site, create=True)
        config.product_tax_codes_json = {product.id: "txcd_00000000"}
        db.commit()
    monkeypatch.setattr(site_routes, "SessionLocal", sessions)
    app, rt = fast_app(secret_key="catalogue-visibility-test")

    @rt("/test-session")
    def get(session):
        session["user_id"] = owner.id
        session["csrf_token"] = "visibility-csrf"
        return "OK"

    site_routes.register_site_routes(rt)
    with TestClient(app) as client:
        yield SimpleNamespace(sessions=sessions, client=client, site=site,
                              product=product, variant=variant, page=page)
    engine.dispose()


@pytest.mark.parametrize("status", ["draft", "archived"])
def test_site_public_product_hidden_and_draft_preview_available(catalogue, status):
    w = catalogue
    public_url = f"/sites/{w.site.slug}/products/sample"
    assert w.client.get(public_url).status_code == 200
    with w.sessions() as db:
        db.get(Product, w.product.id).status = status
        db.commit()
    assert w.client.get(public_url).status_code == 404
    preview_url = f"/admin/sites/{w.site.id}/preview/{w.page.id}"
    assert w.client.get(preview_url).status_code == 400
    w.client.get("/test-session")
    preview = w.client.get(preview_url)
    assert preview.status_code == 200
    assert "Catalogue Sample" in preview.text


@pytest.mark.parametrize("status", ["draft", "archived"])
def test_legacy_catalogue_api_and_cart_reject_non_active(catalogue, status):
    from app.api import get_product, list_products

    with catalogue.sessions() as db:
        card = services.products(db)[0]
        product, variant = card.product, card.product.variants[0]
        cart = services.get_or_create_cart(db, None)
        services.add_to_cart(db, cart, variant.id)
        product.status = status
        db.flush()
        assert services.product_by_slug(db, product.slug) is None
        assert product.id not in {item.product.id for item in services.products(db)}
        assert product.id not in {item.id for item in list_products(q="", category="", session=db)}
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as caught:
            get_product(product.slug, session=db)
        assert caught.value.status_code == 404
        with pytest.raises(CommerceError, match="unavailable"):
            services.add_to_cart(db, cart, variant.id)
        with pytest.raises(CommerceError, match="unavailable"):
            services.cart_summary(db, cart)


@pytest.mark.parametrize("status", ["draft", "archived"])
def test_site_quote_rejects_non_active(catalogue, status):
    w = catalogue
    with w.sessions() as db:
        site = content.owned_site(db, w.site.id,
                                  db.scalar(select(User.id).where(User.email == "visibility@example.test")))
        config = commerce.settings_for(db, site)
        selections = [{"variant_id": w.variant.id, "quantity": 1}]
        assert commerce.price_lines(db, site, config, selections)
        db.get(Product, w.product.id).status = status
        db.flush()
        with pytest.raises(CommerceError, match="unavailable"):
            commerce.price_lines(db, site, config, selections)


def test_api_product_fields_are_additive(catalogue):
    from app.api import get_product, list_products

    with catalogue.sessions() as db:
        card = services.products(db)[0]
        product = card.product
        product.vendor = "Sample supplier"
        product.tags_json = ["summer"]
        db.flush()
        detail = get_product(product.slug, session=db)
        assert {"id", "slug", "name", "description", "channel", "currency", "variants"} <= detail.keys()
        assert detail["handle"] == product.slug
        assert detail["vendor"] == "Sample supplier"
        assert detail["tags"] == ["summer"]
        assert {"barcode", "price_minor", "track_inventory", "image_id"} <= detail["variants"][0].keys()
        summary = next(row for row in list_products(q="", category="", session=db)
                       if row.id == product.id)
        assert summary.vendor == "Sample supplier"
        assert summary.tags == ["summer"]


def test_collection_storefront_uses_membership_and_hides_unpublished(catalogue):
    from app import catalog_admin

    w = catalogue
    with w.sessions() as db:
        included = db.get(Product, w.product.id)
        second = Product(tenant_id=w.site.tenant_id, product_type_id=included.product_type_id,
                         category_id=included.category_id, slug="excluded", name="Excluded Sample",
                         is_published=True)
        db.add(second)
        db.flush()
        page = content.create_page(db, w.site, second.name, "/products/excluded", "product")
        page.product_id, page.published_json = second.id, page.draft_json
        collection = catalog_admin.save_collection(db, w.site, {
            "title": "Curated collection", "handle": "curated", "is_published": True,
            "product_ids": [second.id, included.id]})
        collection_id = collection.id
        db.commit()
    ordered = w.client.get(f"/sites/{w.site.slug}/collections/curated")
    assert ordered.text.index("Excluded Sample") < ordered.text.index("Catalogue Sample")
    with w.sessions() as db:
        from app.models import Collection
        collection = db.get(Collection, collection_id)
        catalog_admin.save_collection(db, w.site, {"product_ids": [w.product.id]},
            collection_id=collection.id, expected_version=collection.version)
        db.commit()
    response = w.client.get(f"/sites/{w.site.slug}/collections/curated")
    assert response.status_code == 200
    assert "Catalogue Sample" in response.text
    assert "Excluded Sample" not in response.text
    with w.sessions() as db:
        db.get(Product, w.product.id).status = "draft"
        db.commit()
    response = w.client.get(f"/sites/{w.site.slug}/collections/curated")
    assert "Catalogue Sample" not in response.text
    with w.sessions() as db:
        from app.models import Collection
        db.get(Collection, collection_id).is_published = False
        db.commit()
    assert w.client.get(f"/sites/{w.site.slug}/collections/curated").status_code == 404
