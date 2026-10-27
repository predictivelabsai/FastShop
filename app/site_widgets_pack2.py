"""Progressively enhanced interactive widgets with useful server fallbacks."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from urllib.parse import quote

from fasthtml.common import A, Button, Div, NotStr, P, Section, Span, ft_hx

from app.site_widgets_pack2_definitions import countdown_end, pack_definitions

__all__ = ["pack_definitions", "render_pack_widget"]

# Application-owned drawings, selected by a validated platform key.
SOCIAL_PATHS = {
    "instagram": '<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><path d="M17 7h.01"/>',
    "facebook": '<path d="M14 22V13h3l1-4h-4V7c0-2 1-2 4-2V2h-3c-4 0-5 2-5 5v2H7v4h3v9"/>',
    "tiktok": '<path d="M14 3v13a4 4 0 1 1-4-4M14 3c1 4 3 6 7 6"/>',
    "youtube": '<rect x="2" y="5" width="20" height="14" rx="4"/><path d="m10 9 6 3-6 3Z"/>',
    "x": '<path d="m4 3 16 18h-4L4 3h4l12 18M20 3 4 21"/>',
    "linkedin": '<path d="M4 9v12M4 4v.01M10 21V9h4v2c5-5 7 0 7 3v7M14 11v10"/>',
    "pinterest": '<path d="m9 22 3-13M8 15C1 11 6 1 14 3c9 2 7 15 1 15-3 0-4-2-3-5"/>',
    "threads": '<path d="M19 7C16 0 4 2 4 12s13 14 16 6c3-8-12-10-11-3 1 4 8 3 7-4-1-6-7-4-7-4"/>',
    "whatsapp": '<path d="m3 21 1-5a9 9 0 1 1 4 4ZM8 7c0 5 4 9 9 9l1-3-3-1-1 2-4-4 2-1-1-3Z"/>',
    "email": '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m2 5 10 8L22 5"/>',
    "website": '<circle cx="12" cy="12" r="10"/><ellipse cx="12" cy="12" rx="4" ry="10"/><path d="M2 12h20"/>',
}
PLATFORM_LABELS = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "linkedin": "LinkedIn",
    "whatsapp": "WhatsApp",
    "x": "X",
}


def icon(name):
    return NotStr(
        '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        + SOCIAL_PATHS.get(name, SOCIAL_PATHS["website"])
        + "</svg>"
    )


def notice_icon(style):
    paths = {
        "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
        "success": '<circle cx="12" cy="12" r="9"/><path d="m7 12 3 3 7-7"/>',
        "warning": '<path d="m12 3 10 18H2ZM12 9v5M12 17h.01"/>',
        "neutral": '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    }
    return NotStr(
        '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        + paths.get(style, paths["info"])
        + "</svg>"
    )


def render_pack_widget(block, base="", preview=False, *, site_id="", now=None):
    """Render a validated block; ``now`` accepts an aware datetime or clock callable."""
    from app.site_widgets import sanitise_html

    kind = block["type"]
    uid = "fs-pack2-" + re.sub(r"[^a-zA-Z0-9_-]", "", str(block.get("id", "widget")))
    items = block.get("items", [])

    def field(key, field_kind="text"):
        return {"data_field": key, "data_field_type": field_kind} if preview else {}

    def heading(value, key="heading", level="h2"):
        return ft_hx(level, value, **field(key)) if value else None

    attrs, children = {}, []
    if kind == "tabs":
        attrs = {"data_tabs": "", "data_layout": block.get("layout", "horizontal")}
        children = [
            Div(
                *[
                    Button(
                        item["label"],
                        id=f"{uid}-tab-{i}",
                        type="button",
                        data_tab=str(i),
                        **field(f"items.{i}.label"),
                    )
                    for i, item in enumerate(items)
                ],
                cls="fs-pack2-tab-list",
                hidden=True,
                data_tab_list="",
            ),
            Div(
                *[
                    Div(
                        heading(item["label"], f"items.{i}.label", "h3"),
                        Div(
                            NotStr(sanitise_html(item["body"])),
                            **field(f"items.{i}.body", "richtext"),
                        ),
                        id=f"{uid}-panel-{i}",
                        data_tab_panel=str(i),
                        cls="fs-pack2-tab-panel",
                    )
                    for i, item in enumerate(items)
                ],
                cls="fs-pack2-tab-panels",
            ),
        ]
    elif kind == "countdown":
        end = countdown_end(block)
        current = now() if callable(now) else now or datetime.now(UTC)
        remaining = max(0, math.ceil((end - current).total_seconds()))
        expired = remaining == 0
        units = [
            ("days", remaining // 86400),
            ("hours", remaining // 3600 % 24),
            ("minutes", remaining // 60 % 60),
            ("seconds", remaining % 60),
        ]
        attrs = {
            "data_countdown": "",
            "data_end_at": end.isoformat(),
            "data_expired_behaviour": block.get("expired_behaviour", "message"),
            "hidden": expired and block.get("expired_behaviour") == "hide",
        }
        summary = (
            f"Ends {end.isoformat()}. "
            + ", ".join(f"{value} {unit}" for unit, value in units)
            + " remaining."
        )
        visible_units = []
        remainder = remaining
        for unit, divisor in (("days", 86400), ("hours", 3600), ("minutes", 60), ("seconds", 1)):
            if block.get(f"show_{unit}", True):
                value, remainder = divmod(remainder, divisor)
                visible_units.append((unit, value))
        children = [
            heading(block.get("heading", "")),
            Div(
                *[
                    Div(
                        Span(str(value).zfill(2), data_countdown_unit=unit, aria_hidden="true"),
                        Span(block.get(f"{unit}_label", unit.title()), **field(f"{unit}_label")),
                        cls="fs-pack2-time-unit",
                    )
                    for unit, value in visible_units
                ],
                cls="fs-pack2-time",
                data_countdown_digits="",
                aria_live="off",
                hidden=expired,
            ),
            P(
                summary,
                cls="fs-pack2-sr",
                data_countdown_summary="",
                aria_live="off",
                hidden=expired,
            ),
            P(
                block.get("expired_message", "This countdown has ended."),
                data_countdown_expired="",
                hidden=not expired,
                **field("expired_message"),
            ),
        ]
    elif kind == "stats":
        attrs = {"data_stats": "", "data_animate": str(block.get("animate", False)).lower()}
        children = [
            Div(
                *[
                    Div(
                        Span(
                            item["value"],
                            cls="fs-pack2-stat-value",
                            data_stat_value="",
                            **field(f"items.{i}.value"),
                        ),
                        P(item["label"], **field(f"items.{i}.label")),
                        P(
                            item.get("source", ""),
                            cls="fs-pack2-source",
                            **field(f"items.{i}.source"),
                        )
                        if item.get("source")
                        else None,
                        cls="fs-pack2-stat",
                    )
                    for i, item in enumerate(items)
                ],
                cls="fs-pack2-stats",
            )
        ]
    elif kind == "progress_bars":
        children = [
            Div(
                *[
                    Div(
                        Div(
                            Span(item["label"], id=f"{uid}-label-{i}", **field(f"items.{i}.label")),
                            Span(f"{item['percentage']}%")
                            if block.get("show_percentage", True)
                            else None,
                            cls="fs-pack2-progress-label",
                        ),
                        Div(
                            Div(style=f"width:{item['percentage']}%", cls="fs-pack2-progress-fill"),
                            role="progressbar",
                            aria_labelledby=f"{uid}-label-{i}",
                            aria_valuemin="0",
                            aria_valuemax="100",
                            aria_valuenow=str(item["percentage"]),
                            cls="fs-pack2-progress-track",
                        ),
                        cls="fs-pack2-progress",
                    )
                    for i, item in enumerate(items)
                ],
                cls="fs-pack2-bars",
            )
        ]
    elif kind == "alert":
        attrs = {
            "data_alert": "",
            "data_notice_style": block.get("notice_style", "info"),
            "data_storage_key": f"fastshop:alert:{site_id}:{block.get('id', '')}",
            "data_preview": str(preview).lower(),
        }
        href = block.get("link", "")
        children = [
            Span(
                notice_icon(block.get("notice_style", "info")),
                cls="fs-pack2-notice-icon",
                aria_hidden="true",
            )
            if block.get("show_icon", True)
            else None,
            Div(
                heading(block.get("heading", "")),
                P(block.get("body", ""), **field("body")),
                A(
                    block.get("link_label", "Read more"),
                    href=base + href if href.startswith("/") else href,
                    **field("link_label"),
                )
                if href
                else None,
                cls="fs-pack2-notice-copy",
            ),
            Button(
                NotStr(
                    '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg>'
                ),
                type="button",
                aria_label="Dismiss notice",
                data_alert_dismiss="",
                hidden=True,
            )
            if block.get("dismissible")
            else None,
        ]
    elif kind == "table":
        columns = block.get("columns", 2)
        rows = []
        for i, item in enumerate(items):
            rows.append(
                ft_hx(
                    "tr",
                    *[
                        ft_hx(
                            "th" if j == 1 and block.get("first_column_header") else "td",
                            item.get(f"cell_{j}", ""),
                            **(
                                {"scope": "row"}
                                if j == 1 and block.get("first_column_header")
                                else {}
                            ),
                            **field(f"items.{i}.cell_{j}"),
                        )
                        for j in range(1, columns + 1)
                    ],
                )
            )
        children = [
            Div(
                ft_hx(
                    "table",
                    ft_hx("caption", block["caption"], **field("caption"))
                    if block.get("caption")
                    else None,
                    ft_hx(
                        "thead",
                        ft_hx(
                            "tr",
                            *[
                                ft_hx(
                                    "th",
                                    block.get(f"header_{j}", ""),
                                    scope="col",
                                    **field(f"header_{j}"),
                                )
                                for j in range(1, columns + 1)
                            ],
                        ),
                    ),
                    ft_hx("tbody", *rows),
                    data_striped=str(block.get("striped", False)).lower(),
                ),
                cls="fs-pack2-table-scroll",
                tabindex="0",
                role="region",
                aria_label=block.get("caption") or "Data table, scroll horizontally to see more",
            )
        ]
    elif kind == "map":
        url = block.get("embed_url", "")
        fallback = (
            "https://www.openstreetmap.org/search?query="
            if "openstreetmap.org" in url
            else "https://www.google.com/maps/search/?api=1&query="
        )
        attrs = {
            "data_map": "",
            "data_embed_url": url,
            "data_map_title": block.get("heading") or "Map",
        }
        children = [
            heading(block.get("heading", "")),
            P(block.get("address", ""), **field("address")),
            Div(
                P(
                    "The map is provided by a third party. Load it to share your connection details with the map provider."
                ),
                Button("Load map", type="button", data_map_load="", hidden=True)
                if url
                else P("Choose a map embed URL."),
                cls="fs-pack2-map-placeholder",
                data_map_placeholder="",
            ),
            Div(cls="fs-pack2-map-frame", data_map_frame=""),
            A(
                "Open in maps",
                href=fallback + quote(block.get("address", "")),
                target="_blank",
                rel="noopener noreferrer",
                aria_label="Open in maps, opens in new tab",
            ),
        ]
    elif kind == "social_links":
        attrs = {
            "data_link_style": block.get("link_style", "icons"),
            "data_size": block.get("size", "medium"),
            "data_alignment": block.get("alignment", "left"),
        }
        links = []
        for i, item in enumerate(items):
            platform = item["platform"]
            label = item.get("label") or PLATFORM_LABELS.get(platform, platform.capitalize())
            external = not item["url"].startswith("mailto:")
            links.append(
                A(
                    icon(platform),
                    Span(label, cls="fs-pack2-social-label", **field(f"items.{i}.label")),
                    href=item["url"],
                    target="_blank" if external else None,
                    rel="noopener noreferrer",
                    aria_label=label + (", opens in new tab" if external else ""),
                )
            )
        children = [Div(*links, cls="fs-pack2-social")]
    else:
        return None
    return Section(*children, cls="fs-widget fs-pack2 fs-widget-" + kind, **attrs)
