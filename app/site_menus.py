"""Site-owned navigation, ordered localized items, and legacy adaptation."""

import copy
import re
from typing import NotRequired, TypedDict

from sqlalchemy import select

from app import content
from app.models import SiteMenu, new_id
from app.services import CommerceError
from app.site_blocks import LocalizedText, _text, default_locale, normalize_document, resolve_text

MAX_ITEMS = 40


class MenuItem(TypedDict):
    id: str
    label: LocalizedText
    kind: str
    path: NotRequired[str]
    block_id: NotRequired[str]
    url: NotRequired[str]


def menu_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", name):
        raise CommerceError("Menu names use lowercase letters, numbers and hyphens (up to 40 characters).")
    return name


def validate_items(items) -> list[MenuItem]:
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise CommerceError("A menu accepts up to 40 items.")
    result, ids = copy.deepcopy(items), set()
    for item in result:
        if not isinstance(item, dict):
            raise CommerceError("Menu items must be objects.")
        kind = item.get("kind")
        fields = {"page": {"path"}, "anchor": {"path", "block_id"}, "external": {"url"}}
        if not isinstance(kind, str) or kind not in fields or set(item) != {"id", "label", "kind"} | fields[kind]:
            raise CommerceError("Choose a page, block anchor or external URL target.")
        if not isinstance(item["id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", item["id"]) or item["id"] in ids:
            raise CommerceError("Menu items need unique identifiers.")
        ids.add(item["id"])
        _text(item["label"], "label", 200)
        if kind == "external":
            if not isinstance(item["url"], str) or len(item["url"]) > 2048:
                raise CommerceError("Use an external URL under 2,049 characters.")
            item["url"] = content.safe_url(item["url"])
            if not item["url"].startswith(("https://", "mailto:")):
                raise CommerceError("External targets need an HTTPS or mailto URL.")
        else:
            if not isinstance(item["path"], str) or len(item["path"]) > 240:
                raise CommerceError("Choose a page path.")
            item["path"] = content.page_path(item["path"])
            if kind == "anchor" and (not isinstance(item["block_id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", item["block_id"])):
                raise CommerceError("Choose a block identifier.")
    return result


def validate_targets(db, site, items, *, published=False):
    items = validate_items(items)
    pages = {page.path: page for page in content.site_pages(db, site)}
    for item in items:
        if not resolve_text(item["label"], default_locale(site, preview=not published)).strip():
            raise CommerceError("Give each item a label in the default locale.")
        if item["kind"] == "external":
            continue
        page = pages.get(item["path"])
        if page is None:
            raise CommerceError("Choose a page belonging to this site.")
        if item["kind"] == "anchor":
            document = page.published_json if published else page.draft_json
            if not document or item["block_id"] not in {b["id"] for b in normalize_document(document)["blocks"] if not b.get("hidden")}:
                raise CommerceError("Choose a visible block on the target page (published before publishing the menu).")
    return items


def menus_for(db, site):
    return list(db.scalars(select(SiteMenu).where(
        SiteMenu.site_id == site.id, SiteMenu.tenant_id == site.tenant_id).order_by(SiteMenu.name)))


def get_menu(db, site, name):
    return db.scalar(select(SiteMenu).where(SiteMenu.site_id == site.id,
        SiteMenu.tenant_id == site.tenant_id, SiteMenu.name == menu_name(name)))


def put_menu(db, site, name, items, *, publish=False):
    name = menu_name(name)
    items = validate_targets(db, site, items)
    if publish:
        validate_targets(db, site, items, published=True)
    menu = get_menu(db, site, name)
    if menu is None:
        menu = SiteMenu(tenant_id=site.tenant_id, site_id=site.id, name=name)
        db.add(menu)
    menu.items_json = items
    if publish:
        menu.published_items_json = copy.deepcopy(items)
    db.flush()
    return menu


def items_from_settings(config):
    result = []
    for item in config.get("navigation", []):
        path = content.safe_url(item["path"])
        target, separator, block_id = path.partition("#")
        if path.startswith("/"):
            fields = {"kind": "anchor", "path": target, "block_id": block_id} if separator else {"kind": "page", "path": path}
        else:
            fields = {"kind": "external", "url": path}
        result.append({"id": new_id(), "label": copy.deepcopy(item["label"]), **fields})
    return result


def adapt_navigation(db, site):
    """Idempotently import both snapshots; invalid legacy links retain read fallback."""
    existing = {menu.name for menu in menus_for(db, site)}
    if {"header", "footer"} <= existing:
        return True
    try:
        draft = validate_targets(db, site, items_from_settings(site.settings_json))
        published = validate_targets(db, site, items_from_settings(site.published_settings_json), published=True)
    except (CommerceError, KeyError, TypeError):
        return False
    for name in ("header", "footer"):
        if name not in existing:
            db.add(SiteMenu(tenant_id=site.tenant_id, site_id=site.id, name=name,
                items_json=draft, published_items_json=published))
    db.flush()
    return True


def rendered_navigation(db, site, name, *, preview=False):
    menu = get_menu(db, site, name)
    items = (menu.items_json if preview else menu.published_items_json) if menu else None
    if items is None:
        config = site.settings_json if preview else site.published_settings_json
        return copy.deepcopy(config.get("navigation", []))
    return [{"label": resolve_text(item["label"], default_locale(site, preview=preview)),
             "path": item["url"] if item["kind"] == "external" else item["path"] + (
                 "#block-" + item["block_id"] if item["kind"] == "anchor" else ""),
             "kind": item["kind"], "block_id": item.get("block_id", "")}
            for item in items]


def snapshot(db, site):
    return {menu.name: copy.deepcopy(menu.items_json) for menu in menus_for(db, site)}
