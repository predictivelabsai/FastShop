import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import commerce
from app.content import create_site
from app.integrations.stripe_commerce import StripeGateway, TaxResult, credentials, verify_webhook
from app.models import (
    Base,
    Category,
    Product,
    ProductType,
    ProductVariant,
    User,
    VariantChannelListing,
)
from app.services import CommerceError


@pytest.fixture
def shop(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'commerce-test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="merchant@example.test", name="Merchant")
        db.add(owner)
        db.flush()
        site = create_site(db, owner.id, "Test brand", "us-test-brand")
        config = commerce.settings_for(db, site, create=True)
        config.mode = "sandbox"
        config.origin_json = {"country": "EE", "line1": "Test warehouse", "city": "Tallinn", "postal_code": "10111"}
        config.shipping_minor = 1000  # Test fixture, not the merchant's chosen shipping fee.
        config.tax_registration_reviewed = True
        category = Category(tenant_id=site.tenant_id, slug="tablets", name="Tablets")
        kind = ProductType(tenant_id=site.tenant_id, slug="physical", name="Physical")
        db.add_all([category, kind])
        db.flush()
        product = Product(tenant_id=site.tenant_id, category_id=category.id, product_type_id=kind.id,
            name="Test tablets", slug="tablets", is_published=True)
        db.add(product)
        db.flush()
        variant = ProductVariant(tenant_id=site.tenant_id, product_id=product.id, sku="TEST", name="Original")
        db.add(variant)
        db.flush()
        db.add(VariantChannelListing(channel_id=site.channel_id, variant_id=variant.id, currency="USD", price_minor=2995))
        config.product_tax_codes_json = {product.id: "txcd_00000000"}
        config.subscription_product_ids_json = [product.id]
        db.commit()
        yield db, site, config, variant


DESTINATION = {"line1": "123 Test Street", "city": "Los Angeles", "state": "CA", "postal_code": "90001", "country": "US"}


def test_discount_stacking_and_renewal_prices(shop):
    db, site, config, variant = shop
    selection = [{"variant_id": variant.id, "quantity": 1, "subscription": True}]
    first = commerce.price_lines(db, site, config, selection, first_order_discount=True)[0]
    renewal = commerce.price_lines(db, site, config, selection)[0]
    assert (first.amount_minor, renewal.amount_minor) == (2426, 2696)
    assert commerce.discounted(commerce.discounted(10000, 10), 10) == 8100
    assert commerce.shipping_price(config, 7499) == 1000
    assert commerce.shipping_price(config, 7500) == 0
    config.shipping_minor = None
    with pytest.raises(CommerceError, match="shipping fee"):
        commerce.shipping_price(config, 10000)


@pytest.mark.parametrize("overrides", [{"country": "CA"}, {"state": "PR"}, {"postal_code": "abc"}, {"line1": ""}])
def test_non_us_and_incomplete_addresses_rejected(overrides):
    with pytest.raises(CommerceError):
        commerce.address(DESTINATION | overrides)


def test_foreign_catalog_and_subscription_ineligibility(shop):
    db, site, config, variant = shop
    config.subscription_product_ids_json = []
    with pytest.raises(CommerceError, match="not eligible"):
        commerce.price_lines(db, site, config, [{"variant_id": variant.id, "quantity": 1, "subscription": True}])
    variant.tenant_id = "foreign-tenant"
    db.flush()
    with pytest.raises(CommerceError, match="unavailable"):
        commerce.price_lines(db, site, config, [{"variant_id": variant.id, "quantity": 1}])


def test_quote_retains_provider_breakdown_and_no_hardcoded_rate(shop):
    db, site, config, variant = shop
    class Gateway:
        def calculate_tax(self, lines, destination, origin, shipping_minor, **kwargs):
            assert origin["country"] == "EE"
            assert destination["state"] == "CA"
            total = sum(line.amount_minor for line in lines) + shipping_minor
            return TaxResult("taxcalc_fixture", "USD", 321, total + 321,
                datetime.now(UTC) + timedelta(hours=1), [{"amount": 321, "taxability_reason": "fixture_only"}])
    quote = commerce.quote_order(db, site, config, [{"variant_id": variant.id, "quantity": 1}], DESTINATION, Gateway())
    assert quote.tax_minor == 321
    assert quote.total_minor == 4316
    assert quote.snapshot_json["tax_breakdown"][0]["amount"] == 321
    assert quote.expires_at <= datetime.now(UTC) + timedelta(minutes=15)


def test_disabled_state_and_unreviewed_tax_configuration(shop):
    db, site, config, variant = shop
    args = (db, site, config, [{"variant_id": variant.id, "quantity": 1}], DESTINATION, None)
    config.tax_registration_reviewed = False
    with pytest.raises(CommerceError, match="registrations"):
        commerce.quote_order(*args)
    config.tax_registration_reviewed = True
    config.allowed_states_json = ["NY"]
    with pytest.raises(CommerceError, match="not enabled for this state"):
        commerce.quote_order(*args)


def test_stripe_form_contract_and_provider_failure(monkeypatch):
    site = SimpleNamespace(id="testsite", slug="h24you")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fixture")
    line = SimpleNamespace(reference="variant", amount_minor=2696, quantity=1, tax_code="txcd_00000000")
    def reply(request):
        fields = parse_qs(request.content.decode())
        assert str(request.url) == "https://api.stripe.com/v1/tax/calculations"
        assert fields["customer_details[address][state]"] == ["CA"]
        assert fields["ship_from_details[address][country]"] == ["EE"]
        assert fields["line_items[0][amount]"] == ["2696"]
        assert request.headers["Idempotency-Key"] == "tax-test"
        return httpx.Response(200, json={"id": "taxcalc_test", "currency": "usd", "livemode": False,
            "tax_amount_exclusive": 0, "amount_total": 3696, "expires_at": 2000000000,
            "tax_breakdown": [{"amount": 0, "taxability_reason": "not_collecting"}]})
    gateway = StripeGateway(site, transport=httpx.MockTransport(reply))
    result = gateway.calculate_tax([line], DESTINATION, {"country": "EE"}, 1000, idempotency_key="tax-test")
    assert result.tax_minor == 0
    gateway.transport = httpx.MockTransport(lambda request: httpx.Response(503))
    with pytest.raises(CommerceError, match="could not complete"):
        gateway.calculate_tax([line], DESTINATION, {"country": "EE"}, 1000, idempotency_key="tax-test")


def test_credentials_do_not_cross_merchants_or_allow_live_keys(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fixture")
    assert credentials(SimpleNamespace(id="another", slug="another-brand"))[0] == ""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_fixture")
    with pytest.raises(CommerceError, match="Live keys"):
        StripeGateway(SimpleNamespace(id="h24", slug="h24you"))


def test_signature_checks_raw_body_timestamp_and_multiple_signatures():
    payload = json.dumps({"id": "evt_test", "type": "checkout.session.completed"}).encode()
    secret, stamp = "whsec_fixture", 1900000000
    digest = hmac.new(secret.encode(), str(stamp).encode() + b"." + payload, hashlib.sha256).hexdigest()
    assert verify_webhook(payload, f"t={stamp},v1=old,v1={digest}", secret, now=stamp)["id"] == "evt_test"
    for body, clock in [(payload + b" ", stamp), (payload, stamp + 301)]:
        with pytest.raises(CommerceError):
            verify_webhook(body, f"t={stamp},v1={digest}", secret, now=clock)
