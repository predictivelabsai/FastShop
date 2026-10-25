from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import commerce, discounts
from app.discount_routes import save
from app.models import Discount, DiscountCode, DiscountRedemption, Voucher
from app.services import CommerceError
from tests.test_checkout_services import open_session, prepare, ready  # noqa: F401
from tests.test_us_commerce import DESTINATION, shop  # noqa: F401


def rule(db, site, **kwargs):
    row = Discount(tenant_id=site.tenant_id, site_id=site.id, title="Test discount",
        **({"type": "amount_off_order", "value": 10, "value_type": "percentage",
           "method": "automatic"} | kwargs))
    db.add(row)
    db.flush()
    return row


def lines(shop, quantity=3):
    db, site, config, variant = shop
    return commerce.price_lines(db, site, config, [{"variant_id": variant.id, "quantity": quantity}])


@pytest.mark.parametrize("kind,value_type,value,expected", [
    ("amount_off_order", "percentage", 10, 899),
    ("amount_off_order", "fixed", 500, 500),
    ("amount_off_products", "percentage", 25, 2246),
    ("amount_off_products", "fixed", 100000, 8985),
    ("buy_x_get_y", "percentage", 100, 2995),
    ("buy_x_get_y", "fixed", 1000, 1000),
    ("free_shipping", "percentage", 0, 1000),
])
def test_each_type_integer_math(shop, kind, value_type, value, expected):
    db, site, _, _ = shop
    rule(db, site, type=kind, value_type=value_type, value=value, buy_quantity=2, get_quantity=1)
    original = lines(shop)
    result, shipping, applied = discounts.apply(db, site, original, 1000)
    assert sum(row["amount_minor"] for row in applied) == expected
    assert sum(line.amount_minor for line in result) + shipping == 9985 - expected
    assert all(type(line.amount_minor) is int for line in result)


@pytest.mark.parametrize("left,right", [
    ("amount_off_products", "amount_off_order"),
    ("amount_off_products", "free_shipping"),
    ("amount_off_order", "free_shipping"),
    ("amount_off_products", "buy_x_get_y"),
    ("amount_off_order", "amount_off_order"),
])
@pytest.mark.parametrize("left_allows,right_allows,count", [(False, False, 1), (True, False, 1), (False, True, 1), (True, True, 2)])
def test_combinations_require_both_rules(shop, left, right, left_allows, right_allows, count):
    db, site, _, _ = shop
    first = rule(db, site, type=left)
    second = rule(db, site, type=right)
    setattr(first, "combines_" + discounts.category(second), left_allows)
    setattr(second, "combines_" + discounts.category(first), right_allows)
    _, _, applied = discounts.apply(db, site, lines(shop), 1000)
    assert len(applied) == count


def test_explicit_code_precedes_automatic_and_is_site_scoped(shop):
    db, site, _, _ = shop
    rule(db, site, value=90)
    explicit = rule(db, site, value=5, method="code")
    db.add(DiscountCode(tenant_id=site.tenant_id, site_id=site.id, discount_id=explicit.id, code="FIVE"))
    db.flush()
    _, _, applied = discounts.apply(db, site, lines(shop), 1000, code="five")
    assert [r["discount_id"] for r in applied] == [explicit.id]
    with pytest.raises(CommerceError, match="unavailable"):
        discounts.apply(db, site, lines(shop), 1000, code="UNKNOWN")
    explicit.site_id = "foreign"
    db.flush()
    with pytest.raises(CommerceError, match="unavailable"):
        discounts.apply(db, site, lines(shop), 1000, code="FIVE")


def test_schedule_customer_minimum_and_limits(shop):
    db, site, _, _ = shop
    now = datetime.now(UTC)
    row = rule(db, site, starts_at=now + timedelta(days=1), ends_at=now + timedelta(days=2))
    assert discounts.status(row, now) == "Scheduled"
    assert discounts.apply(db, site, lines(shop), 1000, now=now)[2] == []
    assert discounts.status(row, now + timedelta(days=3)) == "Expired"
    row.starts_at, row.ends_at = None, None
    row.eligibility, row.emails_json = "emails", ["member@example.test"]
    assert not discounts.apply(db, site, lines(shop), 1000)[2]
    row.minimum_type, row.minimum_value = "quantity", 4
    assert not discounts.apply(db, site, lines(shop), 1000, email="member@example.test")[2]
    row.minimum_type, row.minimum_value = "amount", 9000
    assert not discounts.apply(db, site, lines(shop), 1000, email="member@example.test")[2]
    row.minimum_value = 0
    row.usage_limit = 1
    db.add(DiscountRedemption(tenant_id=site.tenant_id, site_id=site.id,
        discount_id=row.id, order_id="test-completed", customer_email="other@example.test", amount_minor=10))
    db.flush()
    assert not discounts.apply(db, site, lines(shop), 1000, email="member@example.test")[2]
    row.usage_limit = None
    row.once_per_customer = True
    assert not discounts.apply(db, site, lines(shop), 1000, email="other@example.test")[2]
    assert discounts.apply(db, site, lines(shop), 1000, email="member@example.test")[2]


def test_targeted_products_no_foreign_benefit(shop):
    db, site, _, variant = shop
    row = rule(db, site, type="amount_off_products", applies_to="products", product_ids_json=["foreign"])
    assert not discounts.apply(db, site, lines(shop), 1000)[2]
    row.product_ids_json = [variant.product_id]
    assert discounts.apply(db, site, lines(shop), 1000)[2]


def test_voucher_bridge_is_repeatable_preserves_legacy_count(shop):
    db, site, _, _ = shop
    voucher = Voucher(tenant_id=site.tenant_id, code="OLD", kind="percentage",
        value=20, usage_limit=3, used_count=2)
    db.add(voucher)
    db.flush()
    discounts.bridge_vouchers(db, site)
    discounts.bridge_vouchers(db, site)
    row = discounts.code_rule(db, site, "OLD")
    assert row.voucher_id == voucher.id
    assert discounts.usage(db, row) == 2
    assert db.scalar(select(func.count()).select_from(Discount)) == 1
    voucher.is_active = False
    assert not discounts.apply(db, site, lines(shop), 1000, code="OLD", preview=True)[2]


def test_checkout_redeems_once(ready):  # noqa: F811
    from app import checkout_services
    from app.models import CommerceQuote, Order
    row = rule(ready.db, ready.site, method="code", value=10)
    ready.db.add(DiscountCode(tenant_id=ready.site.tenant_id, site_id=ready.site.id,
        discount_id=row.id, code="TEN"))
    ready.db.commit()
    attempt = prepare(ready, code="TEN")
    quote = ready.db.get(CommerceQuote, attempt.quote_id)
    assert quote.discount_minor == 599
    assert not ready.db.scalar(select(DiscountRedemption.id))
    open_session(ready, attempt)
    checkout_services.reconcile(ready.db, ready.site, attempt.id, ready.gateway)
    ready.db.commit()
    order = ready.db.get(Order, attempt.order_id)
    discounts.redeem(ready.db, ready.site, order, quote.snapshot_json["discounts"])
    ready.db.commit()
    assert ready.db.scalar(select(func.count()).select_from(DiscountRedemption)) == 1


def test_discount_form_rejects_invalid_rules_and_duplicate_codes(shop):
    db, site, _, _ = shop
    data = {"title": "Welcome", "type": "amount_off_order", "method": "code",
        "value_type": "percentage", "value": "10", "applies_to": "all",
        "eligibility": "all", "minimum_type": "none", "codes": "WELCOME", "is_active": "on"}
    row = save(db, site, data)
    assert discounts.code_rule(db, site, "WELCOME").id == row.id
    with pytest.raises(CommerceError, match="already in use"):
        save(db, site, data)
    with pytest.raises(CommerceError, match="Percentages"):
        save(db, site, data | {"codes": "NEW", "value": "101"})


def test_bridged_completion_updates_legacy_counter_once(ready):  # noqa: F811
    from app import checkout_services
    from app.models import CommerceQuote, Order
    voucher = Voucher(tenant_id=ready.site.tenant_id, code="LEGACY", kind="percentage",
        value=20, usage_limit=3, used_count=2)
    ready.db.add(voucher)
    ready.db.commit()
    attempt = prepare(ready, code="LEGACY")
    quote = ready.db.get(CommerceQuote, attempt.quote_id)
    open_session(ready, attempt)
    checkout_services.reconcile(ready.db, ready.site, attempt.id, ready.gateway)
    ready.db.commit()
    order = ready.db.get(Order, attempt.order_id)
    discounts.redeem(ready.db, ready.site, order, quote.snapshot_json["discounts"])
    ready.db.commit()
    ready.db.refresh(voucher)
    assert voucher.used_count == 3
    assert discounts.usage(ready.db, discounts.code_rule(ready.db, ready.site, "LEGACY")) == 3


def test_minimal_form_uses_explicit_safe_defaults(shop):
    db, site, _, _ = shop
    row = save(db, site, {"title": "Default rule", "codes": "DEFAULT", "value": "10"})
    assert (row.type, row.method, row.value_type, row.applies_to) == (
        "amount_off_order", "code", "percentage", "all")
