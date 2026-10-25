"""Site-owned discount rules and deterministic integer allocations.

Application order is product, order, shipping, then creation time and ID. Both
rules must permit the other category to combine. Checkout holds the site lock.
"""
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import func, select, update

from app.models import (
    CheckoutAttempt,
    CommerceQuote,
    Discount,
    DiscountCode,
    DiscountRedemption,
    Voucher,
)
from app.services import CommerceError

TYPES = {
    "amount_off_products": "Amount off products", "amount_off_order": "Amount off order",
    "buy_x_get_y": "Buy X get Y", "free_shipping": "Free shipping",
}


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def status(rule, now=None):
    now = now or datetime.now(UTC)
    if not rule.is_active:
        return "Inactive"
    if rule.ends_at and utc(rule.ends_at) <= now:
        return "Expired"
    if rule.starts_at and utc(rule.starts_at) > now:
        return "Scheduled"
    return "Active"


def category(rule):
    return "shipping" if rule.type == "free_shipping" else "orders" if rule.type == "amount_off_order" else "products"


def compatible(left, right):
    return (getattr(left, "combines_" + category(right)) and
            getattr(right, "combines_" + category(left)))


def usage(db, rule):
    count = db.scalar(select(func.count()).select_from(DiscountRedemption).where(
        DiscountRedemption.discount_id == rule.id,
        DiscountRedemption.site_id == rule.site_id,
        DiscountRedemption.tenant_id == rule.tenant_id)) or 0
    if rule.voucher_id:
        voucher = db.get(Voucher, rule.voucher_id)
        # Bridged completions also update the legacy counter. Do not count the
        # same new redemption twice when legacy and site checkouts coexist.
        count = max(count, voucher.used_count if voucher else 0)
    return count


def code_rule(db, site, code):
    return db.scalar(select(Discount).join(DiscountCode,
        DiscountCode.discount_id == Discount.id).where(
        Discount.site_id == site.id, Discount.tenant_id == site.tenant_id,
        DiscountCode.site_id == site.id, DiscountCode.tenant_id == site.tenant_id,
        DiscountCode.code == code.strip().upper()))


def bridge_vouchers(db, site):
    """Expose tenant vouchers per site without changing legacy voucher checkout."""
    for voucher in db.scalars(select(Voucher).where(Voucher.tenant_id == site.tenant_id)):
        if db.scalar(select(Discount.id).where(Discount.site_id == site.id,
                Discount.tenant_id == site.tenant_id, Discount.voucher_id == voucher.id)):
            continue
        if code_rule(db, site, voucher.code):
            continue
        rule = Discount(tenant_id=site.tenant_id, site_id=site.id, title=voucher.code,
            type="amount_off_order", method="code", value_type=(
                "percentage" if voucher.kind == "percentage" else "fixed"), value=voucher.value,
            minimum_type="amount" if voucher.minimum_minor else "none",
            minimum_value=voucher.minimum_minor, usage_limit=voucher.usage_limit,
            is_active=voucher.is_active, voucher_id=voucher.id)
        db.add(rule)
        db.flush()
        db.add(DiscountCode(tenant_id=site.tenant_id, site_id=site.id,
            discount_id=rule.id, code=voucher.code.upper()))
        db.flush()


def pending_attempts(db, site, rule, *, now=None):
    """Unsettled checkouts consume limits and keep their rule available to settle."""
    now = now or datetime.now(UTC)
    held = []
    for attempt, quote in db.execute(select(CheckoutAttempt, CommerceQuote).join(
            CommerceQuote, CommerceQuote.id == CheckoutAttempt.quote_id).where(
            CheckoutAttempt.site_id == site.id, CheckoutAttempt.tenant_id == site.tenant_id,
            CheckoutAttempt.state.in_(["prepared", "creating", "open"]),
            CommerceQuote.site_id == site.id, CommerceQuote.tenant_id == site.tenant_id)):
        if attempt.state == "prepared" and utc(attempt.expires_at) <= now:
            continue
        if any(row.get("discount_id") == rule.id for row in quote.snapshot_json.get("discounts", [])):
            held.append(attempt)
    return held


def eligible(db, site, rule, lines, email, now):
    if status(rule, now) != "Active":
        return False
    if rule.voucher_id:
        voucher = db.get(Voucher, rule.voucher_id)
        if not voucher or not voucher.is_active:
            return False
    if rule.eligibility == "emails" and email.lower() not in rule.emails_json:
        return False
    if rule.minimum_type == "amount" and sum(line.amount_minor for line in lines) < rule.minimum_value:
        return False
    if rule.minimum_type == "quantity" and sum(line.quantity for line in lines) < rule.minimum_value:
        return False
    held = pending_attempts(db, site, rule, now=now)
    if rule.usage_limit is not None and usage(db, rule) + len(held) >= rule.usage_limit:
        return False
    if rule.once_per_customer and (not email or db.scalar(select(DiscountRedemption.id).where(
            DiscountRedemption.discount_id == rule.id, DiscountRedemption.site_id == site.id,
            DiscountRedemption.tenant_id == site.tenant_id,
            DiscountRedemption.customer_email == email.lower()))):
        return False
    if rule.once_per_customer and held:
        from app.models import ShopCustomer
        customer_ids = set(db.scalars(select(ShopCustomer.id).where(ShopCustomer.site_id == site.id,
            ShopCustomer.tenant_id == site.tenant_id, func.lower(ShopCustomer.email) == email.lower())))
        if any(attempt.customer_id in customer_ids for attempt in held):
            return False
    return True


def allocate(lines, indices, saving):
    total = sum(lines[i].amount_minor for i in indices)
    saving = min(max(0, saving), total)
    amounts = {i: saving * lines[i].amount_minor // (total or 1) for i in indices}
    ordered = sorted(indices, key=lambda i: (
        -(saving * lines[i].amount_minor % (total or 1)), lines[i].reference))
    for i in ordered[:saving - sum(amounts.values())]:
        amounts[i] += 1
    return [replace(line, amount_minor=line.amount_minor - amounts.get(i, 0))
            for i, line in enumerate(lines)]


def apply(db, site, lines, shipping, *, code="", email="", now=None, preview=False,
          shipping_for_lines=None, existing_order_discount=False):
    """Return lines, shipping, redemption snapshots. No usage is consumed by quotes."""
    now = now or datetime.now(UTC)
    rules = list(db.scalars(select(Discount).where(Discount.site_id == site.id,
        Discount.tenant_id == site.tenant_id, Discount.method == "automatic")))
    explicit = code_rule(db, site, code) if code else None
    if code and not explicit:
        raise CommerceError("This discount code is unavailable.")
    if explicit:
        rules.append(explicit)
    if existing_order_discount:
        rules = [rule for rule in rules if rule.combines_orders]
    # Explicit codes take precedence over incompatible automatic rules.
    if explicit:
        rules = [r for r in rules if r.id == explicit.id or compatible(explicit, r)]
    rules.sort(key=lambda r: ({"products": 0, "orders": 1, "shipping": 2}[category(r)],
                              utc(r.created_at or now), r.id))
    applied, snapshots = [], []
    shipping_calculated = False
    for rule in rules:
        if category(rule) == "shipping" and shipping_for_lines and not shipping_calculated:
            shipping = shipping_for_lines(lines)
            shipping_calculated = True
        if not eligible(db, site, rule, lines, email, now):
            if explicit and rule.id == explicit.id and not preview:
                raise CommerceError("This discount is not eligible for this purchase.")
            continue
        if any(not compatible(rule, prior) for prior in applied):
            continue
        product_ids = set(rule.product_ids_json)
        if rule.applies_to == "collections":
            from app.catalog_admin import collection_products
            from app.models import Collection
            for collection in db.scalars(select(Collection).where(Collection.tenant_id == site.tenant_id,
                    Collection.id.in_(rule.collection_ids_json))):
                product_ids.update(p.id for p in collection_products(db, site, collection))
        indices = [i for i, line in enumerate(lines)
                   if rule.applies_to == "all" or line.product_id in product_ids]
        if rule.type == "amount_off_order":
            indices = list(range(len(lines)))
        before = sum(line.amount_minor for line in lines) + shipping
        total = sum(lines[i].amount_minor for i in indices)
        if rule.type == "free_shipping":
            shipping = 0
        elif rule.type == "buy_x_get_y":
            quantity = sum(lines[i].quantity for i in indices)
            free_units = quantity // (rule.buy_quantity + rule.get_quantity) * rule.get_quantity
            # Cheapest qualifying units receive the benefit, with stable ties.
            amounts = {}
            for i in sorted(indices, key=lambda i: (lines[i].amount_minor // lines[i].quantity,
                                                    lines[i].reference)):
                units = min(free_units, lines[i].quantity)
                free_units -= units
                base = lines[i].amount_minor * units // lines[i].quantity
                amounts[i] = min(base, (base * rule.value + 50) // 100
                                 if rule.value_type == "percentage" else rule.value * units)
            lines = [replace(line, amount_minor=line.amount_minor - amounts.get(i, 0))
                     for i, line in enumerate(lines)]
        else:
            saving = (total * rule.value + 50) // 100 if rule.value_type == "percentage" else rule.value
            lines = allocate(lines, indices, saving)
        saving = before - sum(line.amount_minor for line in lines) - shipping
        if saving:
            applied.append(rule)
            snapshots.append({"discount_id": rule.id, "title": rule.title,
                              "type": rule.type, "amount_minor": saving})
    if shipping_for_lines and not shipping_calculated:
        shipping = shipping_for_lines(lines)
    return lines, shipping, snapshots


def redeem(db, site, order, snapshots):
    """Called inside the locked, idempotent paid-order transaction."""
    if order.tenant_id != site.tenant_id:
        raise CommerceError("Order does not belong to this store.")
    for row in sorted(snapshots, key=lambda r: r["discount_id"]):
        rule = db.scalar(select(Discount).where(Discount.id == row["discount_id"],
            Discount.site_id == site.id, Discount.tenant_id == site.tenant_id).with_for_update())
        if not rule:
            raise CommerceError("Discount reconciliation is required.")
        existing = db.scalar(select(DiscountRedemption.id).where(
            DiscountRedemption.discount_id == rule.id, DiscountRedemption.order_id == order.id))
        if not existing:
            db.add(DiscountRedemption(tenant_id=site.tenant_id, site_id=site.id,
                discount_id=rule.id, order_id=order.id, customer_email=order.email.lower(),
                amount_minor=row["amount_minor"]))
            if rule.voucher_id:
                db.execute(update(Voucher).where(Voucher.id == rule.voucher_id,
                    Voucher.tenant_id == site.tenant_id).values(used_count=Voucher.used_count + 1))
            db.flush()
