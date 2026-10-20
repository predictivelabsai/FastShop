"""Liquid seed, canonical blocks, isolation and real site commerce regression tests."""

import copy
import re

import pytest
from fastcore.xml import to_xml
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient

from app import commerce, site_routes, store_checkout_routes
from app.models import (
    Base,
    Channel,
    Product,
    ProductVariant,
    Site,
    SiteCart,
    SiteMenu,
    SitePage,
    Stock,
    Tenant,
    User,
    VariantChannelListing,
)
from app.services import CommerceError
from app.site_blocks import BLOCK_TYPES, normalize_document, patch_block, validate_block
from app.site_seed import seed_h24you
from app.site_seed_liquid import CONTENT, documents, seed_h24you_liquid
from app.site_theme import theme_pack
from app.site_ui import storefront


@pytest.fixture
def liquid(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'liquid.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="liquid@example.test", name="Liquid fixture")
        db.add(owner)
        db.flush()
        site = seed_h24you_liquid(db, owner.email)
        db.commit()
        yield db, site, owner
    engine.dispose()


def html(db, site, path="/", preview=False):
    page = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.path == path))
    return "".join(
        str(to_xml(n))
        for n in storefront(
            db,
            site,
            page,
            "/sites/" + site.slug,
            "test-csrf",
            "https://example.test" + path,
            preview=preview,
        )
        if n is not None
    )


def test_seed_is_idempotent_and_preserves_edits(liquid):
    db, site, owner = liquid
    counts = {
        m: db.scalar(select(func.count()).select_from(m))
        for m in (Tenant, Site, Product, ProductVariant, SitePage, SiteMenu, Stock)
    }
    page = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.path == "/"))
    page.draft_json = patch_block(page.draft_json, "hero", {"heading": "Merchant heading"})
    site.settings_json = site.settings_json | {"email": "merchant@example.test"}
    config = commerce.settings_for(db, site)
    config.shipping_minor = 1200
    db.commit()
    assert seed_h24you_liquid(db, owner.email).id == site.id
    assert counts == {m: db.scalar(select(func.count()).select_from(m)) for m in counts}
    assert page.draft_json["blocks"][0]["heading"] == "Merchant heading"
    assert site.settings_json["email"] == "merchant@example.test"
    assert config.shipping_minor == 1200


def test_catalog_and_sandbox_prices(liquid):
    db, site, _ = liquid
    channel = db.get(Channel, site.channel_id)
    assert (site.slug, site.name, site.status) == ("h24you-liquid", "H2 | 4 YOU", "preview")
    assert (channel.country_code, channel.currency, channel.locale) == ("US", "USD", "en")
    assert site.settings_json["default_locale"] == "en"
    config = commerce.settings_for(db, site)
    assert config.mode == "sandbox" and config.free_shipping_threshold_minor == 7500
    products = {
        p.slug: p for p in db.scalars(select(Product).where(Product.tenant_id == site.tenant_id))
    }
    assert len(products) == 3
    assert config.subscription_product_ids_json == [products["hydrogen-tablets"].id]
    for slug, prices in [
        ("hydrogen-tablets", [2800] * 4),
        ("h2-4-you-one", [25000]),
        ("taste-the-range", [9520]),
    ]:
        variants = list(
            db.scalars(
                select(ProductVariant)
                .where(ProductVariant.product_id == products[slug].id)
                .order_by(ProductVariant.sort_order)
            )
        )
        assert [
            db.scalar(
                select(VariantChannelListing.price_minor).where(
                    VariantChannelListing.variant_id == v.id,
                    VariantChannelListing.channel_id == site.channel_id,
                )
            )
            for v in variants
        ] == prices
        if slug == "hydrogen-tablets":
            assert [v.name for v in variants] == ["Unflavored", "Raspberry", "Pineapple", "Peach"]
            line = commerce.price_lines(
                db,
                site,
                config,
                [{"variant_id": variants[-1].id, "quantity": 1, "subscription": True}],
            )[0]
            assert line.unit_minor == 2520


@pytest.mark.parametrize(
    "path,preview", [("/", False), ("/shop", False), ("/", True), ("/shop", True)]
)
def test_pages_render_canonical_copy_and_disclaimer(liquid, path, preview):
    db, site, _ = liquid
    markup = html(db, site, path, preview)
    assert CONTENT["fdaDisclaimer"] in markup
    assert 'data-pack="liquid"' in markup and "/static/site-theme-liquid.css" in markup
    assert ("Your water, upgraded." if path == "/" else CONTENT["shopContent"]["intro"]) in markup
    assert "h24you_valge.svg" in markup and "/sites/h24you-liquid/shop" in markup
    assert 'name="csrf_token" value="test-csrf"' in markup
    assert ("data-cart-open" in markup) is (not preview)
    if path == "/":
        assert "Sample reviews" in markup
        assert markup.count('data-sample="true"') == 16
        assert (
            markup.index('id="science"')
            < markup.index('id="products"')
            < markup.index('id="why"')
            < markup.index('id="reviews"')
            < markup.index('id="learn"')
        )
        for r in CONTENT["metalReviews"]:
            assert r["text"].replace("&", "&amp;") in markup
    for p in db.scalars(select(SitePage).where(SitePage.site_id == site.id)):
        assert p.draft_json["version"] == 1 and "sections" not in p.draft_json
        normalize_document(p.draft_json)


@pytest.mark.parametrize(
    "config,expected",
    [
        ({}, ""),
        ({"theme_pack": "liquid"}, "liquid"),
        ({"theme_pack": "https://evil.test/style.css"}, ""),
        ({"theme_pack": {}}, ""),
        ({"theme_pack": ["liquid"]}, ""),
        ({"theme_pack": "LIQUID"}, ""),
    ],
)
def test_theme_pack_closed_allowlist(config, expected):
    assert theme_pack(config) == expected


def test_old_fixture_not_changed_by_liquid_seed(liquid):
    db, liquid_site, owner = liquid
    legacy = seed_h24you(db, owner.email)
    before = html(db, legacy)
    seed_h24you_liquid(db, owner.email)
    assert html(db, legacy) == before
    assert "site-theme-liquid" not in before
    assert legacy.tenant_id != liquid_site.tenant_id
    for invalid in ("unknown", "https://evil.test/style.css", {"liquid": True}):
        legacy.published_settings_json = legacy.published_settings_json | {"theme_pack": invalid}
        assert html(db, legacy) == before


LIQUID_BLOCKS = [
    b for _, _, _, blocks in documents() for b in blocks if b["type"].startswith("liquid_")
]


@pytest.mark.parametrize("block", LIQUID_BLOCKS, ids=lambda b: b["type"])
def test_new_blocks_validate_and_reject_unsafe_fields(block):
    assert block["type"] in BLOCK_TYPES
    assert validate_block(block) == block
    with pytest.raises(CommerceError):
        validate_block(block | {"merchant_css": "body{}"})
    if block.get("items"):
        unsafe = copy.deepcopy(block)
        unsafe["items"][0]["url"] = "javascript:alert(1)"
        with pytest.raises(CommerceError):
            validate_block(unsafe)
        incomplete = copy.deepcopy(block)
        del incomplete["items"][0][BLOCK_TYPES[block["type"]].required_item_fields[0]]
        with pytest.raises(CommerceError):
            validate_block(incomplete)


@pytest.mark.parametrize(
    "kind,item",
    [
        (
            "liquid_reviews",
            {"heading": "Sample", "body": "Sample", "label": "Sample", "value": "6"},
        ),
        ("liquid_collection", {"theme": "unsupported"}),
        ("liquid_collection", {"theme": "product", "heading": "Incomplete"}),
        ("liquid_benefits", {"heading": "Sample", "body": "Sample", "theme": "<script>"}),
    ],
)
def test_liquid_semantic_validation(kind, item):
    with pytest.raises(CommerceError):
        validate_block({"id": "test", "type": kind, "items": [item]})


def test_http_pages_and_peach_subscription_use_site_cart(liquid, monkeypatch):
    from app.main import app

    db, site, _ = liquid
    sessions = sessionmaker(bind=db.bind, expire_on_commit=False)
    monkeypatch.setattr(site_routes, "SessionLocal", sessions)
    monkeypatch.setattr(store_checkout_routes, "SessionLocal", sessions)
    client = TestClient(app)
    for path in ("/", "/shop"):
        response = client.get("/sites/h24you-liquid" + path)
        assert response.status_code == 200 and CONTENT["fdaDisclaimer"] in response.text
    home = client.get("/sites/h24you-liquid/")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', home.text).group(1)
    peach = db.scalar(
        select(ProductVariant).where(
            ProductVariant.tenant_id == site.tenant_id, ProductVariant.name == "Peach"
        )
    )
    response = client.post(
        "/sites/h24you-liquid/cart/add",
        data={"csrf_token": csrf, "variant_id": peach.id, "quantity": "1", "subscription": "on"},
    )
    assert response.status_code == 200 and "$25.20" in response.text and "Peach" in response.text
    assert client.get("/sites/h24you-liquid/cart/summary").json()["count"] == 1
    cart = db.scalar(select(SiteCart).where(SiteCart.site_id == site.id))
    assert cart.lines_json == [{"variant_id": peach.id, "quantity": 1, "subscription": True}]
    rejected = client.post(
        "/sites/h24you-liquid/cart/add",
        data={"csrf_token": "wrong", "variant_id": peach.id, "quantity": "1"},
    )
    assert "session expired" in rejected.text.lower()
    assert client.get("/sites/h24you-liquid/products/h2-4-you-one").status_code == 404


def test_display_prices_follow_catalogue_changes(liquid):
    db, site, _ = liquid
    product = db.scalar(
        select(Product).where(Product.tenant_id == site.tenant_id, Product.slug == "h2-4-you-one")
    )
    listing = db.scalar(
        select(VariantChannelListing)
        .join(ProductVariant)
        .where(
            ProductVariant.product_id == product.id,
            VariantChannelListing.channel_id == site.channel_id,
        )
    )
    listing.price_minor = 24900
    db.flush()
    assert "$249" in html(db, site)
    assert "$249" in html(db, site, "/shop")
    assert "$250" not in html(db, site, "/shop")


def test_liquid_cart_rejects_foreign_tenant_variant(liquid):
    db, site, owner = liquid
    other = seed_h24you(db, owner.email)
    foreign = db.scalar(select(ProductVariant).where(ProductVariant.tenant_id == other.tenant_id))
    with pytest.raises(CommerceError, match="unavailable"):
        commerce.price_lines(
            db,
            site,
            commerce.settings_for(db, site),
            [{"variant_id": foreign.id, "quantity": 1, "subscription": False}],
        )
