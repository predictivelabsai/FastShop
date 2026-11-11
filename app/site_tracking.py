"""Validated tracking IDs shared by merchant settings and storefronts."""

from __future__ import annotations

import json
import re
from decimal import Decimal

from fasthtml.common import NotStr, Script
from sqlalchemy import select

from app.content_gap_models import SitePreferences
from app.services import CommerceError

TRACKING_FIELDS = (
    "ga4_measurement_id",
    "google_ads_conversion_id",
    "google_ads_purchase_label",
    "meta_pixel_id",
    "tiktok_pixel_id",
)

_PATTERNS = {
    "ga4_measurement_id": re.compile(r"G-[A-Z0-9]{6,20}"),
    "google_ads_conversion_id": re.compile(r"AW-[0-9]{6,20}"),
    "google_ads_purchase_label": re.compile(r"[A-Za-z0-9_-]{1,100}"),
    "meta_pixel_id": re.compile(r"[0-9]{5,30}"),
    "tiktok_pixel_id": re.compile(r"C[A-Z0-9]{8,30}"),
}

_LABELS = {
    "ga4_measurement_id": "GA4 measurement ID",
    "google_ads_conversion_id": "Google Ads conversion ID",
    "google_ads_purchase_label": "Google Ads purchase conversion label",
    "meta_pixel_id": "Meta pixel ID",
    "tiktok_pixel_id": "TikTok pixel ID",
}


def validate_tracking_ids(values):
    """Return normalised provider IDs, rejecting values outside provider formats."""
    result = {}
    for field in TRACKING_FIELDS:
        value = str((values or {}).get(field, "") or "").strip()
        if field in {"ga4_measurement_id", "google_ads_conversion_id", "tiktok_pixel_id"}:
            value = value.upper()
        if value and not _PATTERNS[field].fullmatch(value):
            examples = {
                "ga4_measurement_id": "G-ABC1234567",
                "google_ads_conversion_id": "AW-1234567890",
                "google_ads_purchase_label": "AbCdEfGhIjKlMnOp",
                "meta_pixel_id": "123456789012345",
                "tiktok_pixel_id": "C1234567890123456789",
            }
            raise CommerceError(f"Enter a valid {_LABELS[field]} such as {examples[field]}, or leave it empty.")
        result[field] = value
    if result["google_ads_purchase_label"] and not result["google_ads_conversion_id"]:
        raise CommerceError("Add a Google Ads conversion ID before setting its purchase conversion label.")
    return result


def legacy_tracking_ids(db, site, config=None):
    """Read the old settings and preference fields for one-time migration fallback."""
    config = config if isinstance(config, dict) else (site.settings_json or {})
    preferences = db.scalar(select(SitePreferences).where(
        SitePreferences.site_id == site.id,
        SitePreferences.tenant_id == site.tenant_id,
    ))
    # The dedicated site setting was the more recently added GA4 field. Use it
    # first when both legacy GA4 locations contain a value.
    return {
        "ga4_measurement_id": config.get("ga4_measurement_id")
        or getattr(preferences, "analytics_id", ""),
        "google_ads_conversion_id": "",
        "google_ads_purchase_label": "",
        "meta_pixel_id": getattr(preferences, "meta_pixel_id", ""),
        "tiktok_pixel_id": "",
    }


def tracking_ids(db, site, config=None):
    """Read canonical IDs, falling back to legacy storage for fields not migrated."""
    settings = config if isinstance(config, dict) else (site.settings_json or {})
    canonical = settings.get("tracking")
    canonical = canonical if isinstance(canonical, dict) else {}
    legacy = legacy_tracking_ids(db, site, settings)
    values = {
        field: canonical.get(field, legacy.get(field, ""))
        for field in TRACKING_FIELDS
    }
    # Imported IDs can predate strict validation. Fail closed for storefront use
    # and show only values that the new form can safely preserve.
    try:
        return validate_tracking_ids(values)
    except CommerceError:
        cleaned = {}
        for field, value in values.items():
            value = str(value or "").strip()
            if field in {"ga4_measurement_id", "google_ads_conversion_id", "tiktok_pixel_id"}:
                value = value.upper()
            cleaned[field] = value if not value or _PATTERNS[field].fullmatch(value) else ""
        if not cleaned["google_ads_conversion_id"]:
            cleaned["google_ads_purchase_label"] = ""
        return cleaned
_ZERO_DECIMAL_CURRENCIES = frozenset(
    "BIF CLP DJF GNF JPY KMF KRW MGA PYG RWF UGX VND VUV XAF XOF XPF".split()
)
_THREE_DECIMAL_CURRENCIES = frozenset("BHD IQD JOD KWD LYD OMR TND".split())


def major_units(minor, currency):
    currency = str(currency or "USD").upper()
    precision = 0 if currency in _ZERO_DECIMAL_CURRENCIES else 3 if currency in _THREE_DECIMAL_CURRENCIES else 2
    return float(Decimal(int(minor)).scaleb(-precision))


def product_item(db, site, page):
    if getattr(page, "kind", "") != "product" or not getattr(page, "product_id", None):
        return None
    from app.models import Product, ProductVariant, VariantChannelListing

    product = db.scalar(select(Product).where(
        Product.id == page.product_id,
        Product.tenant_id == site.tenant_id,
        Product.is_published.is_(True),
        Product.status == "active",
    ))
    variants = list(db.scalars(select(ProductVariant).where(
        ProductVariant.product_id == page.product_id,
        ProductVariant.tenant_id == site.tenant_id,
        ProductVariant.is_active.is_(True),
    ).order_by(ProductVariant.sort_order)))
    listings = {
        listing.variant_id: listing
        for listing in db.scalars(select(VariantChannelListing).where(
            VariantChannelListing.variant_id.in_([variant.id for variant in variants]),
            VariantChannelListing.channel_id == site.channel_id,
        ))
    } if variants else {}
    available = [(variant, listings[variant.id]) for variant in variants if variant.id in listings]
    if not product or not available:
        return None

    def item(variant, listing):
        return {
            "item_id": product.slug,
            "item_name": product.name,
            "item_variant": variant.name,
            "price": major_units(listing.price_minor, listing.currency),
            "currency": listing.currency.upper(),
        }

    selected_variant, selected_listing = available[0]
    return {
        "item": item(selected_variant, selected_listing),
        "variants": {variant.id: item(variant, listing) for variant, listing in available},
    }


def event_items(lines, currency):
    items = []
    for line in lines or []:
        line = line if isinstance(line, dict) else vars(line)
        quantity = max(1, int(line.get("quantity", 1)))
        amount_minor = line.get("amount_minor")
        unit_minor = line.get("unit_minor", line.get("price_minor", 0))
        price = (major_units(amount_minor, currency) / quantity
                 if amount_minor is not None else major_units(unit_minor, currency))
        item = {
            "item_id": str(line.get("product_id") or line.get("variant_id") or ""),
            "item_name": str(line.get("name") or line.get("product_name") or "")[:300],
            "price": price,
            "quantity": quantity,
        }
        variant = str(line.get("variant_name") or "")[:160]
        if variant:
            item["item_variant"] = variant
        items.append(item)
    return items


def tracking_nodes(db, site, event=None, *, preview=False):
    """Render JSON event data and the local tracker only on published storefront pages."""
    if preview or site.status != "published":
        return []
    providers = tracking_ids(db, site, site.published_settings_json or {})
    if not any(providers.values()):
        return []
    payload = {"site_id": site.id, "providers": providers, "event": event or {}}
    serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    serialized = serialized.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    from app.ui import static_asset

    return [
        Script(NotStr(serialized), type="application/json", id="fastshop-tracking-config"),
        Script(src=static_asset("store-tracking.js"), defer=True, data_site=site.id),
    ]
