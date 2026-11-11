"""Validated tracking IDs shared by merchant settings and storefronts."""

from __future__ import annotations

import re

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
