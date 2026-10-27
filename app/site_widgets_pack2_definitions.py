"""Interactive widget metadata and server-side input boundaries."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.services import CommerceError


def map_embed_url(value):
    """Accept only the two documented HTTPS map embed endpoints."""
    try:
        if not isinstance(value, str) or not value or re.search(r"[\s\\\x00-\x1f\x7f]", value):
            raise ValueError
        url = urlsplit(value)
        if (url.scheme != "https" or url.username or url.password
                or url.port not in {None, 443} or url.fragment
                or (url.hostname, url.path) not in {
                    ("www.google.com", "/maps/embed"),
                    ("www.openstreetmap.org", "/export/embed.html"),
                }):
            raise ValueError
    except ValueError:
        raise CommerceError("Choose a Google Maps or OpenStreetMap HTTPS embed URL.") from None
    return value


def countdown_end(block):
    """Resolve one merchant-set deadline, never a visitor-relative timer."""
    value, zone = block.get("end_at", ""), block.get("timezone", "")
    try:
        if not isinstance(zone, str) or not isinstance(value, str):
            raise ValueError
        tz = ZoneInfo(zone) if zone else None
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?", value):
            raise ValueError
        end = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if end.tzinfo is None:
            if tz is None:
                raise ValueError
            end = end.replace(tzinfo=tz)
            # Reject nonexistent local times during daylight-saving transitions.
            if end.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != end.replace(tzinfo=None):
                raise ValueError
        return end
    except (ValueError, ZoneInfoNotFoundError, OverflowError):
        raise CommerceError("Use an ISO 8601 date and time with an offset, or supply a valid IANA timezone.") from None


def _social_url(value, platform):
    try:
        if not isinstance(value, str) or not value or re.search(r"[\s\\\x00-\x1f\x7f]", value):
            raise ValueError
        url = urlsplit(value)
        if platform == "email" and url.scheme == "mailto":
            if not re.fullmatch(r"[^@/?#]+@[^@/?#]+\.[^@/?#]+", url.path) or url.query or url.fragment:
                raise ValueError
        elif url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError
        else:
            _ = url.port
    except ValueError:
        raise CommerceError("Social links must use HTTPS; email links may use mailto.") from None


def validate_pack_widget(block):
    """Validate relationships that cannot be expressed as individual fields."""
    kind = block["type"]
    if kind == "countdown":
        countdown_end(block)
        if not any(block.get("show_" + unit, True) for unit in ("days", "hours", "minutes", "seconds")):
            raise CommerceError("Show at least one countdown unit.")
    elif kind == "social_links":
        for item in block.get("items", []):
            _social_url(item.get("url"), item.get("platform"))
    elif kind == "map":
        map_embed_url(block.get("embed_url"))
    return block


def pack_definitions(definition):
    """Declare picker, insertion and editor constraints together."""
    def text(kind="text", **extra):
        return {"kind": kind, "max_length": 10000 if kind == "richtext" else 4000 if kind == "textarea" else 2048 if kind == "url" else 240, **extra}

    def choice(*values):
        return {"kind": "select", "options": values}

    def number(low, high, unit=""):
        return {"kind": "range", "min": low, "max": high, "unit": unit}

    def items(defaults, fields, low, high, required=()):
        return {"defaults": defaults, "fields": fields, "min": low, "max": high, "required": required}

    toggle = {"kind": "toggle"}
    tab = {"label": "Your tab", "body": "<p>Add your tab content.</p>"}
    stat = {"value": "00", "label": "Your figure", "source": ""}
    bar = {"label": "Your progress", "percentage": 0}
    row = {f"cell_{i}": "Your value" if i <= 2 else "" for i in range(1, 7)}
    social = {"platform": "website", "url": "https://example.com", "label": ""}
    return {
        "tabs": definition("Tabs", "Interactive", {"layout": "horizontal", "items": [tab.copy(), {"label": "Another tab", "body": "<p>Add more content.</p>"}]},
            {"layout": choice("horizontal", "vertical")}, items(tab, {"label": text(), "body": text("richtext")}, 2, 8, ("label", "body")),
            icon="tabs", description="Organise content in keyboard-accessible tabs"),
        "countdown": definition("Countdown", "Marketing", {
            "heading": "Your event countdown", "end_at": "2030-01-01T00:00:00+00:00", "timezone": "",
            "days_label": "Days", "hours_label": "Hours", "minutes_label": "Minutes", "seconds_label": "Seconds",
            "show_days": True, "show_hours": True, "show_minutes": True, "show_seconds": True,
            "expired_behaviour": "message", "expired_message": "This countdown has ended."},
            {"heading": text(), "end_at": text(help="Use ISO 8601 with an offset, for example 2030-01-01T00:00:00+00:00. A local date and time requires an IANA timezone."),
             "timezone": text(help="Optional IANA timezone, for example Europe/London."),
             **{unit + "_label": text() for unit in ("days", "hours", "minutes", "seconds")},
             **{"show_" + unit: toggle for unit in ("days", "hours", "minutes", "seconds")},
             "expired_behaviour": choice("hide", "message"), "expired_message": text()},
            icon="countdown", description="Count down to a fixed date set by you"),
        "stats": definition("Statistics", "Content", {"animate": False, "items": [stat.copy()]}, {"animate": toggle},
            items(stat, {"value": text(), "label": text(), "source": text("textarea")}, 1, 6, ("value", "label")),
            icon="stats", description="Present your figures with optional source notes"),
        "progress_bars": definition("Progress bars", "Content", {"show_percentage": True, "items": [bar.copy()]}, {"show_percentage": toggle},
            items(bar, {"label": text(), "percentage": number(0, 100, "%")}, 1, 8, ("label", "percentage")),
            icon="progress-bars", description="Show labelled progress as percentages"),
        "alert": definition("Notice", "Content", {"notice_style": "info", "show_icon": True, "heading": "Your notice", "body": "Add a useful notice for visitors.", "link": "", "link_label": "Read more", "dismissible": False},
            {"notice_style": choice("info", "success", "warning", "neutral"), "show_icon": toggle, "heading": text(), "body": text("textarea"), "link": text("url"), "link_label": text(), "dismissible": toggle},
            icon="alert", description="Share a useful notice with an optional link"),
        "table": definition("Table", "Content", {"columns": 2, "caption": "Your table", **{f"header_{i}": f"Column {i}" if i <= 2 else "" for i in range(1, 7)}, "items": [row.copy()], "striped": True, "first_column_header": False},
            {"columns": number(1, 6), "caption": text(), **{f"header_{i}": text() for i in range(1, 7)}, "striped": toggle, "first_column_header": toggle},
            items(row, {f"cell_{i}": text("textarea", max_length=2000) for i in range(1, 7)}, 0, 50),
            icon="table", description="Compare information in a scrollable data table"),
        "map": definition("Map", "Content", {"heading": "Find us", "address": "Add your address", "embed_url": "https://www.openstreetmap.org/export/embed.html"},
            {"heading": text(), "address": text("textarea"), "embed_url": text("url", https=True, validate=map_embed_url, help="Google Maps /maps/embed or OpenStreetMap /export/embed.html HTTPS URL. Visitors choose when to load it.")},
            icon="map", description="Let visitors choose when to load a third-party map"),
        "social_links": definition("Social links", "Content", {"link_style": "icons", "size": "medium", "alignment": "left", "items": [social.copy()]},
            {"link_style": choice("icons", "labels"), "size": choice("small", "medium", "large"), "alignment": choice("left", "centre", "right")},
            items(social, {"platform": choice("instagram", "facebook", "tiktok", "youtube", "x", "linkedin", "pinterest", "threads", "whatsapp", "email", "website"), "url": text("url"), "label": text()}, 0, 12, ("platform", "url")),
            icon="social-links", description="Link to your profiles with accessible social icons"),
    }
