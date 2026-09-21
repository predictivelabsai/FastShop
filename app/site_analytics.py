"""Per-site GA4 configuration; previews never initialize analytics."""

import re

from app.services import CommerceError


def validate_measurement_id(value):
    value = str(value or "").strip().upper()
    if value and not re.fullmatch(r"G-[A-Z0-9]{6,20}", value):
        raise CommerceError("Enter a GA4 measurement ID such as G-ABC1234567, or leave it empty to disable analytics.")
    return value


def measurement_id(site, *, preview=False):
    if preview or site.status != "published":
        return ""
    try:
        return validate_measurement_id(site.published_settings_json.get("ga4_measurement_id", ""))
    except CommerceError:
        return ""  # Old/imported settings fail closed rather than injecting a tag URL.
