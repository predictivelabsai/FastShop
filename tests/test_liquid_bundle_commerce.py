"""Bundle prices and purchase selections use the real site cart price boundary."""

import re

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app import commerce, site_routes, store_checkout_routes
from app.models import Product, ProductVariant, SiteCart, Stock, VariantChannelListing
from app.services import CommerceError
from app.site_seed_liquid import ASSETS, upgrade_liquid_assets_and_bundles
from tests.test_checkout_services import Gateway
from tests.test_site_seed_liquid import liquid  # noqa: F401
from tests.test_us_commerce import DESTINATION


def variants_for(db, site, slug):
    return list(db.scalars(select(ProductVariant).join(Product).where(
        Product.tenant_id == site.tenant_id, Product.slug == slug,
        ProductVariant.tenant_id == site.tenant_id,
    ).order_by(ProductVariant.sort_order)))


@pytest.mark.parametrize("tier,quantity,unit,total", [
    (0, 1, 25000, 25000), (1, 2, 22500, 45000),
    (2, 3, 21250, 63750), (3, 4, 20000, 80000),
    (3, 7, 20000, 140000), (3, 25, 20000, 500000),
])
def test_bottle_panel_bundle_matches_server_price(liquid, tier, quantity, unit, total):
    db, site, _ = liquid
    variant = variants_for(db, site, "h2-4-you-one")[tier]
    line = commerce.price_lines(db, site, commerce.settings_for(db, site), [{
        "variant_id": variant.id, "quantity": quantity, "subscription": False,
        "unit_minor": 1,  # Shopper-supplied price has no authority.
    }])[0]
    assert (line.unit_minor, line.amount_minor) == (unit, total)
    assert line.variant_id == variant.id


@pytest.mark.parametrize("tier,quantity", [(1, 1), (2, 1), (2, 2), (3, 3)])
def test_bottle_tier_cannot_be_bought_below_minimum(liquid, tier, quantity):
    db, site, _ = liquid
    variant = variants_for(db, site, "h2-4-you-one")[tier]
    with pytest.raises(CommerceError, match="at least"):
        commerce.price_lines(db, site, commerce.settings_for(db, site), [{
            "variant_id": variant.id, "quantity": quantity, "subscription": False,
        }])


@pytest.mark.parametrize("flavour", range(4))
@pytest.mark.parametrize("subscription,unit", [(False, 2800), (True, 2520)])
def test_all_flavour_purchase_modes_match_tablet_pricing(liquid, flavour, subscription, unit):
    db, site, _ = liquid
    variant = variants_for(db, site, "hydrogen-tablets")[flavour]
    line = commerce.price_lines(db, site, commerce.settings_for(db, site), [{
        "variant_id": variant.id, "quantity": 3, "subscription": subscription,
    }])[0]
    assert (line.unit_minor, line.amount_minor, line.variant_name) == (unit, unit * 3, variant.name)


def test_bundle_uses_live_channel_price_and_is_not_subscribable(liquid):
    db, site, _ = liquid
    variant = variants_for(db, site, "h2-4-you-one")[1]
    listing = db.scalar(select(VariantChannelListing).where(
        VariantChannelListing.variant_id == variant.id,
        VariantChannelListing.channel_id == site.channel_id,
    ))
    listing.price_minor = 22499
    db.flush()
    selection = {"variant_id": variant.id, "quantity": 2, "subscription": False}
    line = commerce.price_lines(db, site, commerce.settings_for(db, site), [selection])[0]
    assert line.amount_minor == 44998
    with pytest.raises(CommerceError, match="not eligible"):
        commerce.price_lines(db, site, commerce.settings_for(db, site), [selection | {
            "subscription": True,
        }])


def test_fixed_range_set_price(liquid):
    db, site, _ = liquid
    variant = variants_for(db, site, "taste-the-range")[0]
    line = commerce.price_lines(db, site, commerce.settings_for(db, site), [{
        "variant_id": variant.id, "quantity": 1, "subscription": False,
    }])[0]
    assert line.amount_minor == 9520


@pytest.mark.parametrize("tier,quantity,formatted", [
    (0, 1, "$250.00"), (1, 2, "$450.00"), (2, 3, "$637.50"), (3, 6, "$1,200.00"),
])
def test_bundle_form_posts_to_real_site_cart(liquid, monkeypatch, tier, quantity, formatted):
    from app.main import app

    db, site, _ = liquid
    sessions = sessionmaker(bind=db.bind, expire_on_commit=False)
    monkeypatch.setattr(site_routes, "SessionLocal", sessions)
    monkeypatch.setattr(store_checkout_routes, "SessionLocal", sessions)
    client = TestClient(app)
    response = client.get("/sites/h24you-liquid/")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
    variant = variants_for(db, site, "h2-4-you-one")[tier]
    response = client.post("/sites/h24you-liquid/cart/add", data={
        "csrf_token": csrf, "variant_id": variant.id, "quantity": str(quantity),
        "subscription": "off", "unit_minor": "1",
    })
    assert response.status_code == 200 and formatted in response.text
    cart = db.scalar(select(SiteCart).where(
        SiteCart.tenant_id == site.tenant_id, SiteCart.site_id == site.id,
    ))
    assert cart.lines_json == [{
        "variant_id": variant.id, "quantity": quantity, "subscription": False,
    }]


def test_m1_upgrade_preserves_merchant_settings_and_catalogue_prices(liquid):
    db, site, _ = liquid
    site.settings_json = site.settings_json | {
        "logo": ASSETS + "h24you_must.svg", "email": "merchant@example.test",
    }
    variant = variants_for(db, site, "h2-4-you-one")[1]
    listing = db.scalar(select(VariantChannelListing).where(
        VariantChannelListing.variant_id == variant.id,
        VariantChannelListing.channel_id == site.channel_id,
    ))
    listing.price_minor = 22123
    db.flush()
    upgrade_liquid_assets_and_bundles(db, site)
    assert site.settings_json["logo"] == ASSETS + "logo-clean-dark.png"
    assert site.settings_json["email"] == "merchant@example.test"
    assert listing.price_minor == 22123
    assert len(variants_for(db, site, "h2-4-you-one")) == 4


def test_m1_upgrade_adds_missing_bundle_variants_and_stock(liquid):
    db, site, _ = liquid
    for variant in variants_for(db, site, "h2-4-you-one")[1:]:
        db.execute(delete(Stock).where(Stock.variant_id == variant.id))
        db.execute(delete(VariantChannelListing).where(
            VariantChannelListing.variant_id == variant.id,
            VariantChannelListing.channel_id == site.channel_id,
        ))
        db.delete(variant)
    db.flush()
    upgrade_liquid_assets_and_bundles(db, site)
    variants = variants_for(db, site, "h2-4-you-one")
    assert len(variants) == 4
    assert [v.attributes_json.get("minimum_quantity", 1) for v in variants] == [1, 2, 3, 4]
    for variant in variants[1:]:
        assert db.scalar(select(Stock.quantity).where(Stock.variant_id == variant.id)) == 100


@pytest.mark.parametrize("slug,tier,quantity,subscription,total", [
    ("h2-4-you-one", 1, 2, False, 45000),
    ("h2-4-you-one", 2, 3, False, 63750),
    ("h2-4-you-one", 3, 7, False, 140000),
    ("hydrogen-tablets", 3, 3, True, 7560),
])
def test_checkout_quote_preserves_panel_merchandise_total(
    liquid, slug, tier, quantity, subscription, total,
):
    db, site, _ = liquid
    variant = variants_for(db, site, slug)[tier]
    quote = commerce.quote_order(db, site, commerce.settings_for(db, site), [{
        "variant_id": variant.id, "quantity": quantity, "subscription": subscription,
    }], DESTINATION, Gateway())
    assert quote.subtotal_minor - quote.discount_minor == total
    assert quote.total_minor == total + quote.shipping_minor + quote.tax_minor
    assert quote.snapshot_json["lines"][0]["amount_minor"] == total
