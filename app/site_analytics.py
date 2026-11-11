"""Compatibility helpers for the unified per-site tracking settings."""

from app.services import CommerceError


def validate_measurement_id(value):
    """Keep the previous GA4 helper API on the shared validation rules."""
    from app.site_tracking import validate_tracking_ids

    return validate_tracking_ids({"ga4_measurement_id": value})["ga4_measurement_id"]


def measurement_id(site, *, preview=False):
    if preview or site.status != "published":
        return ""
    try:
        settings = site.published_settings_json or {}
        tracking = settings.get("tracking")
        value = (
            tracking.get("ga4_measurement_id", settings.get("ga4_measurement_id", ""))
            if isinstance(tracking, dict)
            else settings.get("ga4_measurement_id", "")
        )
        return validate_measurement_id(value)
    except CommerceError:
        return ""  # Old/imported settings fail closed rather than injecting a tag URL.
