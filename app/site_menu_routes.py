"""Merchant menu forms and session-authenticated JSON API."""

import copy
from urllib.parse import urlencode

from fasthtml.common import (
    H2,
    H3,
    A,
    Button,
    Details,
    Div,
    Form,
    Input,
    Label,
    Option,
    P,
    Select,
    Summary,
)
from starlette.responses import JSONResponse, RedirectResponse

from app import content
from app import site_builder_services as builder
from app import site_menus as menus
from app.db import SessionLocal
from app.models import new_id
from app.services import CommerceError
from app.site_blocks import default_locale, merge_localized, resolve_text


def register_menu_routes(rt, actor, csrf, check_csrf, shell, error):
    @rt("/admin/sites/{site_id}/menus", methods=["GET"])
    def get(session, site_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                base = f"/admin/sites/{site.id}/menus"
                locale = default_locale(site)
                pages = content.site_pages(db, site)

                def hidden():
                    return csrf(session), Input(type="hidden", name="version", value=site.version)

                def fields(item):
                    return (
                        Label(f"Label ({locale})", Input(name="label", value=resolve_text(item.get("label", ""), locale), maxlength=200, required=True)),
                        Label("Target type", Select(*[Option(label, value=value, selected=item.get("kind", "page") == value)
                            for value, label in [("page", "Page"), ("anchor", "Block anchor"), ("external", "External URL")]], name="kind", aria_label="Target type")),
                        Label("Page", Select(*[Option(page.title + " — " + page.path, value=page.path, selected=item.get("path", "/") == page.path)
                            for page in pages], name="path", aria_label="Page")),
                        Label("Block (for block anchors)", Select(Option("Choose a block", value=""), *[
                            Option(page.path + " — " + (resolve_text(block.get("heading", ""), locale) or block["type"])[:80],
                                   value=block["id"], selected=item.get("block_id") == block["id"])
                            for page in pages for block in page.draft_json["blocks"] if not block.get("hidden")], name="block_id", aria_label="Block (for block anchors)")),
                        Label("External URL", Input(name="url", value=item.get("url", ""), placeholder="https://example.com", maxlength=2048)),
                    )

                cards = []
                for menu in menus.menus_for(db, site):
                    rows = []
                    for index, item in enumerate(menu.items_json):
                        rows.append(Details(Summary(f"{index + 1}. {resolve_text(item['label'], locale)}"),
                            Form(*hidden(), Input(type="hidden", name="item_id", value=item["id"]), *fields(item),
                                Div(Button("Save item", name="action", value="edit", cls="e-button"),
                                    Button("Move up", name="action", value="up", disabled=index == 0),
                                    Button("Move down", name="action", value="down", disabled=index == len(menu.items_json) - 1),
                                    Button("Remove item", name="action", value="remove", formnovalidate=True), cls="e-actions"),
                                method="post", action=base + "/" + menu.name, cls="e-form"), cls="e-section"))
                    cards.append(Div(H2(menu.name.title()),
                        P(f"{len(menu.items_json)} of 40 items. " + ("Published menu available." if menu.published_items_json is not None else "Unpublished; visitors see legacy links.")),
                        *rows, P("This menu is empty.") if not rows else None,
                        Details(Summary("Add an item"), Form(*hidden(), *fields({}),
                            Button("Add item", name="action", value="add", cls="e-button", disabled=len(menu.items_json) >= menus.MAX_ITEMS),
                            method="post", action=base + "/" + menu.name, cls="e-form")),
                        Form(*hidden(), Button("Publish menu", name="action", value="publish", cls="e-button"),
                            method="post", action=base + "/" + menu.name), cls="e-card"))
                return shell("Menus — " + site.name,
                    Div(A("Back to site", href=f"/admin/sites/{site.id}"), A("Builder", href=f"/admin/sites/{site.id}/build"), cls="e-actions"),
                    P("Edit labels and destinations, then publish each menu when ready. Header and footer appear on the storefront. Other named menus are available through the API."),
                    P("Labels use the site's default locale. Editing preserves other translations."),
                    P(notice, role="alert", cls="e-note") if notice else None,
                    *cards,
                    H3("Create a menu"), Form(*hidden(), Label("Menu name", Input(name="name", placeholder="header or footer", required=True, maxlength=40)),
                        Button("Create menu", cls="e-button"), method="post", action=base, cls="e-form"),
                    Form(*hidden(), Input(type="hidden", name="action", value="import"), Button("Import legacy navigation", cls="e-button"), method="post", action=base))
        except CommerceError as exc:
            return error(exc)

    def mutate(db, session, site_id, data, name, *, api=False):
        check_csrf(session, data)
        user_id = actor(session)
        action = data.get("action", "replace" if api else "create")
        if not isinstance(action, str):
            raise CommerceError("Choose a supported menu action.")
        site = content.owned_site(db, site_id, user_id, publish=action == "publish")
        try:
            version = int(data.get("version", -1))
        except (ValueError, TypeError):
            raise CommerceError("Reload the menu before saving.") from None
        site = builder.lock_site(db, site.id, user_id, version)
        before = builder.snapshot(db, site)
        if action == "import":
            if not menus.adapt_navigation(db, site):
                raise CommerceError("Legacy navigation contains invalid targets. Create a menu and select this site's pages.")
        else:
            menu = menus.get_menu(db, site, name)
            items = copy.deepcopy(menu.items_json) if menu else []
            if api:
                if action not in {"replace", "publish"}:
                    raise CommerceError("Choose replace or publish.")
                items = data.get("items")
            elif action == "create":
                if menu:
                    raise CommerceError("This menu already exists.")
            elif menu is None:
                raise CommerceError("Menu not found.")
            elif action in {"add", "edit"}:
                previous = next((item for item in items if item["id"] == data.get("item_id")), None)
                if action == "edit" and previous is None:
                    raise CommerceError("Menu item not found.")
                kind = str(data.get("kind", ""))
                item = {"id": previous["id"] if previous else new_id(), "kind": kind,
                        "label": merge_localized(previous["label"] if previous else "", str(data.get("label", "")), default_locale(site))}
                if kind == "external":
                    item["url"] = str(data.get("url", ""))
                else:
                    item["path"] = str(data.get("path", ""))
                    if kind == "anchor":
                        item["block_id"] = str(data.get("block_id", ""))
                items = [item if row["id"] == item["id"] else row for row in items] if previous else items + [item]
            elif action in {"remove", "up", "down"}:
                index = next((i for i, row in enumerate(items) if row["id"] == data.get("item_id")), None)
                if index is None:
                    raise CommerceError("Menu item not found.")
                if action == "remove":
                    items.pop(index)
                else:
                    destination = index + (-1 if action == "up" else 1)
                    if not 0 <= destination < len(items):
                        raise CommerceError("This item is already at the edge of the menu.")
                    items[index], items[destination] = items[destination], items[index]
            elif action != "publish":
                raise CommerceError("Choose a supported menu action.")
            menus.put_menu(db, site, name, items, publish=action == "publish")
        site.version += 1
        builder.record_change(db, site, user_id, before, "classical", "Navigation menu: " + name)
        db.commit()
        return site.version

    async def form_post(session, request, site_id, name=""):
        data = dict(await request.form())
        try:
            with SessionLocal() as db:
                mutate(db, session, site_id, data, name or str(data.get("name", "")))
            notice = "Menu saved."
        except CommerceError as exc:
            notice = str(exc)
        return RedirectResponse(f"/admin/sites/{site_id}/menus?" + urlencode({"notice": notice}), status_code=303)

    @rt("/admin/sites/{site_id}/menus", methods=["POST"])
    async def post(session, request, site_id: str):
        return await form_post(session, request, site_id)

    @rt("/admin/sites/{site_id}/menus/{name}", methods=["POST"])
    async def post(session, request, site_id: str, name: str):
        return await form_post(session, request, site_id, name)

    @rt("/admin/sites/{site_id}/menus.json", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                return JSONResponse({"version": site.version, "menus": menus.snapshot(db, site)})
        except CommerceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @rt("/admin/sites/{site_id}/menus/{name}/api", methods=["POST"])
    async def post(session, request, site_id: str, name: str):
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise CommerceError("Send a menu object.")
            with SessionLocal() as db:
                version = mutate(db, session, site_id, data, name, api=True)
            return JSONResponse({"version": version})
        except (CommerceError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
