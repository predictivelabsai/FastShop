"""Trusted merchant snippets with atomic draft and published snapshots."""

from __future__ import annotations

import copy
import re
from html.parser import HTMLParser

from sqlalchemy import select

from app import content
from app.models import SiteSnippet
from app.services import CommerceError
from app.site_blocks import _text

PLACEMENTS = ("head", "pre-footer", "foot")
MAX_CONTENT = 50_000
MAX_NOTE = 400
_URL_ATTRIBUTES = {"src", "href", "action", "poster"}
_MARKETING_MARKERS = re.compile(
    r"facebook|connect\.facebook|\bfbq\b|doubleclick|googleadservices|\baw-[a-z0-9]+|"
    r"remarketing|pinterest|snapchat|tiktok|linkedin\.com/insight|\blintrk\b|adsbygoogle|marketing",
    re.IGNORECASE,
)


def validate_placement(value: str) -> str:
    if not isinstance(value, str) or value not in PLACEMENTS:
        raise CommerceError("Choose head, pre-footer or foot placement.")
    return value


class _SnippetInspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.executable_script = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"html", "head", "body", "base"}:
            raise CommerceError("Snippets cannot replace document structure or the base URL.")
        attributes = {str(key).lower(): value for key, value in attrs}
        if "srcdoc" in attributes:
            raise CommerceError("Snippet iframe srcdoc is not supported.")
        for key, value in attributes.items():
            if key.startswith("on") or (isinstance(value, str) and value.strip().lower().startswith("javascript:")):
                raise CommerceError("Use a script tag for executable snippet code.")
            if key in _URL_ATTRIBUTES and value:
                cleaned = content.safe_url(value)
                if cleaned.startswith("mailto:") and tag == "a" and key == "href":
                    continue
                if not cleaned.startswith("https://"):
                    raise CommerceError("Snippet URLs must use HTTPS; mailto is allowed only on links.")
        if tag == "script":
            script_type = str(attributes.get("type") or "").strip().lower()
            if script_type not in {"application/json", "application/ld+json"}:
                self.executable_script = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag.lower() in {"html", "head", "body", "base"}:
            raise CommerceError("Snippets cannot replace document structure or the base URL.")


def inspect_content(value: str) -> str:
    """Return the consent class: none, analytics, or marketing."""
    if not isinstance(value, str) or len(value) > MAX_CONTENT:
        raise CommerceError(f"Snippet markup must be text under {MAX_CONTENT + 1} characters.")
    parser = _SnippetInspector()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, TypeError) as exc:
        raise CommerceError("Enter valid snippet markup.") from exc
    if not parser.executable_script:
        return "none"
    return "marketing" if _MARKETING_MARKERS.search(value) else "analytics"


def validate_snapshot(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - {"content", "enabled", "note"}:
        raise CommerceError("Use supported snippet fields.")
    content_value = value.get("content", "")
    enabled = value.get("enabled", False)
    if type(enabled) is not bool:
        raise CommerceError("Snippet enabled must be true or false.")
    inspect_content(content_value)
    result = {"content": content_value, "enabled": enabled}
    note = value.get("note", "")
    result["note"] = _text(note, "note", limit=MAX_NOTE)
    return copy.deepcopy(result)


def snippets_for(db, site):
    return list(db.scalars(select(SiteSnippet).where(
        SiteSnippet.site_id == site.id,
        SiteSnippet.tenant_id == site.tenant_id,
    ).order_by(SiteSnippet.placement)))


def get_snippet(db, site, placement):
    placement = validate_placement(placement)
    return db.scalar(select(SiteSnippet).where(
        SiteSnippet.site_id == site.id,
        SiteSnippet.tenant_id == site.tenant_id,
        SiteSnippet.placement == placement,
    ))


def put_snippet(db, site, placement, snapshot, *, publish=False):
    placement = validate_placement(placement)
    snapshot = validate_snapshot(snapshot)
    snippet = get_snippet(db, site, placement)
    if snippet is None:
        snippet = SiteSnippet(
            tenant_id=site.tenant_id,
            site_id=site.id,
            placement=placement,
            draft_json=snapshot,
        )
        db.add(snippet)
    else:
        snippet.draft_json = snapshot
    if publish:
        snippet.published_json = copy.deepcopy(snapshot)
    db.flush()
    return snippet


def published_snippets(db, site, *, preview=False):
    """Return enabled public snapshots only; previews and preview-status sites fail closed."""
    result = {placement: [] for placement in PLACEMENTS}
    if preview or site.status != "published":
        return result
    for snippet in snippets_for(db, site):
        snapshot = snippet.published_json
        if snapshot and snapshot["enabled"] and snapshot["content"].strip():
            result[snippet.placement].append({
                "content": snapshot["content"],
                "consent": inspect_content(snapshot["content"]),
            })
    return result
