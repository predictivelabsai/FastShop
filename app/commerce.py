"""US-only commerce policies, independent of the legacy demo checkout.

Amounts are integer USD cents. Quotes are not payments or evidence of payment.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import (
    CommerceQuote,
    Product,
    ProductVariant,
    SiteCommerceSettings,
    VariantChannelListing,
    new_id,
)
from app.services import CommerceError

US_STATES = dict(item.split(":", 1) for item in (
    "AL:Alabama|AK:Alaska|AZ:Arizona|AR:Arkansas|CA:California|CO:Colorado|CT:Connecticut|DE:Delaware|"
    "DC:District of Columbia|FL:Florida|GA:Georgia|HI:Hawaii|ID:Idaho|IL:Illinois|IN:Indiana|IA:Iowa|"
    "KS:Kansas|KY:Kentucky|LA:Louisiana|ME:Maine|MD:Maryland|MA:Massachusetts|MI:Michigan|MN:Minnesota|"
    "MS:Mississippi|MO:Missouri|MT:Montana|NE:Nebraska|NV:Nevada|NH:New Hampshire|NJ:New Jersey|"
    "NM:New Mexico|NY:New York|NC:North Carolina|ND:North Dakota|OH:Ohio|OK:Oklahoma|OR:Oregon|"
    "PA:Pennsylvania|RI:Rhode Island|SC:South Carolina|SD:South Dakota|TN:Tennessee|TX:Texas|UT:Utah|"
    "VT:Vermont|VA:Virginia|WA:Washington|WV:West Virginia|WI:Wisconsin|WY:Wyoming"
).split("|"))
EU_COUNTRIES = frozenset("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split())


def address(values, *, origin=False):
    result = {key: str(values.get(key, "")).strip()[:200] for key in ("line1", "line2", "city", "state", "postal_code", "country")}
    result["country"] = result["country"].upper()
    result["state"] = result["state"].upper()
    if not all(result[key] for key in ("line1", "city", "postal_code", "country")):
        raise CommerceError("Enter a complete street address, city, postal code and country.")
    if origin:
        if result["country"] not in EU_COUNTRIES:
            raise CommerceError("Choose the actual fulfilment origin within the EU.")
    elif result["country"] != "US" or result["state"] not in US_STATES:
        raise CommerceError("Delivery is available to configured US states and Washington, DC only.")
    elif not re.fullmatch(r"\d{5}(?:-\d{4})?", result["postal_code"]):
        raise CommerceError("Enter a valid US ZIP or ZIP+4 code.")
    return result


def settings_for(db, site, *, create=False):
    config = db.scalar(select(SiteCommerceSettings).where(
        SiteCommerceSettings.site_id == site.id, SiteCommerceSettings.tenant_id == site.tenant_id))
    if not config and create:
        config = SiteCommerceSettings(tenant_id=site.tenant_id, site_id=site.id,
            allowed_states_json=list(US_STATES), origin_json={"country": "EE"})
        db.add(config)
        db.flush()
    return config


def discounted(amount: int, percent: int) -> int:
    """Round the resulting price half-up to cents; no binary floating point."""
    if type(amount) is not int or amount < 0 or type(percent) is not int or not 0 <= percent <= 100:
        raise CommerceError("Invalid discount amount or percentage.")
    return (amount * (100 - percent) + 50) // 100


@dataclass(frozen=True)
class PricedLine:
    reference: str
    product_id: str
    variant_id: str
    name: str
    quantity: int
    unit_minor: int
    subtotal_minor: int
    amount_minor: int
    tax_code: str
    subscription: bool
    sku: str = ""
    variant_name: str = ""


def price_lines(db, site, config, selections, *, first_order_discount=False):
    """Resolve all catalog ownership server-side; callers cannot supply prices.

    first_order_discount is a trusted service decision, never a raw form field.
    Redemption/first-order eligibility must be checked before calling this function.
    """
    if config.site_id != site.id or config.tenant_id != site.tenant_id:
        raise CommerceError("Commerce settings do not belong to this site.")
    if not selections or len(selections) > 50:
        raise CommerceError("Choose between one and fifty cart lines.")
    lines, seen = [], set()
    for selection in selections:
        variant_id = str(selection.get("variant_id", ""))
        subscription = selection.get("subscription", False)
        quantity = selection.get("quantity")
        if type(quantity) is not int or not 1 <= quantity <= 25 or type(subscription) is not bool:
            raise CommerceError("Choose a valid quantity and purchase option.")
        reference = variant_id + ("-monthly" if subscription else "-once")
        if reference in seen:
            raise CommerceError("Combine duplicate cart lines before quoting.")
        seen.add(reference)
        row = db.execute(select(ProductVariant, Product, VariantChannelListing).join(
            Product, Product.id == ProductVariant.product_id).join(VariantChannelListing,
            VariantChannelListing.variant_id == ProductVariant.id).where(
                ProductVariant.id == variant_id, ProductVariant.tenant_id == site.tenant_id,
                Product.tenant_id == site.tenant_id, Product.is_published.is_(True),
                ProductVariant.is_active.is_(True), VariantChannelListing.channel_id == site.channel_id,
                VariantChannelListing.currency == "USD")).first()
        if not row:
            raise CommerceError("This product option is unavailable in this site's US catalog.")
        variant, product, listing = row
        if subscription and product.id not in config.subscription_product_ids_json:
            raise CommerceError("This product is not eligible for subscriptions.")
        tax_code = config.product_tax_codes_json.get(product.id, "")
        if not re.fullmatch(r"txcd_\d+", tax_code):
            raise CommerceError(f"A reviewed Stripe tax category is required for {product.name}.")
        unit = discounted(listing.price_minor, 10) if subscription else listing.price_minor
        lines.append(PricedLine(reference, product.id, variant.id, f"{product.name} — {variant.name}",
            quantity, unit, listing.price_minor * quantity, unit * quantity, tax_code, subscription, variant.sku, variant.name))
    # Allocate the rounded first-order saving proportionally, using largest remainders.
    # A single cart-level rounding avoids depending on how the customer split lines.
    if first_order_discount:
        from dataclasses import replace
        subtotal = sum(line.amount_minor for line in lines)
        saving = subtotal - discounted(subtotal, 10)
        allocations = [saving * line.amount_minor // subtotal if subtotal else 0 for line in lines]
        remaining = saving - sum(allocations)
        ordered = sorted(range(len(lines)), key=lambda i: (-(saving * lines[i].amount_minor % (subtotal or 1)), lines[i].reference))
        for index in ordered[:remaining]:
            allocations[index] += 1
        lines = [replace(line, amount_minor=line.amount_minor - allocations[i]) for i, line in enumerate(lines)]
    return lines


def shipping_price(config, subtotal_after_discounts):
    if config.shipping_minor is None:
        raise CommerceError("The merchant must configure a US shipping fee before checkout opens.")
    return 0 if subtotal_after_discounts >= config.free_shipping_threshold_minor else config.shipping_minor


def quote_order(db, site, config, selections, destination, gateway, *, first_order_discount=False):
    from dataclasses import asdict
    if config.site_id != site.id or config.tenant_id != site.tenant_id:
        raise CommerceError("Commerce settings do not belong to this site.")
    if config.mode != "sandbox":
        raise CommerceError("Sandbox commerce must be configured first; live payments are not enabled.")
    if not config.tax_registration_reviewed:
        raise CommerceError("Review the merchant's Stripe Tax registrations before quoting.")
    destination = address(destination)
    if destination["state"] not in config.allowed_states_json:
        raise CommerceError("Shipping is not enabled for this state.")
    origin = address(config.origin_json, origin=True)
    lines = price_lines(db, site, config, selections, first_order_discount=first_order_discount)
    subtotal = sum(line.subtotal_minor for line in lines)
    discounted_subtotal = sum(line.amount_minor for line in lines)
    shipping = shipping_price(config, discounted_subtotal)
    snapshot = {"lines": [asdict(line) for line in lines], "destination": destination, "origin": origin,
        "settings_version": config.version, "shipping_minor": shipping, "first_order_discount": first_order_discount}
    fingerprint = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    now = datetime.now(UTC)
    quote_id = new_id()
    tax = gateway.calculate_tax(lines, destination, origin, shipping,
        idempotency_key=f"tax-{site.id}-{quote_id}")
    if tax.currency != "USD" or tax.total_minor != discounted_subtotal + shipping + tax.tax_minor:
        raise CommerceError("The tax provider returned inconsistent totals; checkout is unavailable.")
    if tax.expires_at <= now:
        raise CommerceError("The tax quote expired. Please request a fresh quote.")
    snapshot["tax_breakdown"] = tax.breakdown
    quote = CommerceQuote(id=quote_id, tenant_id=site.tenant_id, site_id=site.id, fingerprint=fingerprint,
        provider_id=tax.provider_id, currency="USD", subtotal_minor=subtotal,
        discount_minor=subtotal - discounted_subtotal, shipping_minor=shipping, tax_minor=tax.tax_minor,
        total_minor=tax.total_minor, expires_at=min(tax.expires_at, now + timedelta(minutes=15)), snapshot_json=snapshot)
    db.add(quote)
    db.flush()
    return quote
