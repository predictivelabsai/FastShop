"""Custom data, preferences and URL redirects in the merchant shell."""
import json
from urllib.parse import urlencode

from fasthtml.common import (
    H2,
    A,
    Button,
    Div,
    Form,
    Input,
    Label,
    Link,
    Option,
    P,
    Select,
    Table,
    Tbody,
    Td,
    Textarea,
    Th,
    Thead,
    Tr,
)
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app import content, custom_data, store_preferences, url_redirects
from app.admin_ui import card, empty_state, page_header
from app.auth import password_attempt_allowed
from app.content_gap_models import CustomDataValue
from app.db import SessionLocal
from app.models import Site
from app.services import CommerceError
from app.site_media import media_for


def scroll_table(*children):
    return Div(Table(*children), cls="a-table-wrap cg-table")


def custom_data_card(db, site, owner_type, owner_id, csrf_input):
    if not owner_id:
        return None
    defs = custom_data.definitions(db, site, owner_type)
    saved = custom_data.values(db, site, owner_type, owner_id)
    fields = []
    for definition in defs:
        key = definition.namespace + "." + definition.key
        value = saved.get(key, "")
        kind = definition.value_type
        rules = definition.validation_json or {}
        if kind == "boolean":
            control = Select(Option("Not set", value=""), Option("Yes", value="true", selected=value is True),
                             Option("No", value="false", selected=value is False), name=key)
        elif rules.get("choices"):
            control = Select(Option("Choose value", value=""), *[Option(str(v), value=str(v), selected=value == v)
                for v in rules["choices"]], name=key, required=rules.get("required", False))
        elif kind in {"file_reference", "image_reference"}:
            control = Select(Option("Choose file", value=""), *[Option(m.title or m.id,
                value=m.id, selected=value == m.id) for m in media_for(db, site)
                if kind != "image_reference" or m.content_type.startswith("image/")], name=key)
        elif kind == "product_reference":
            from sqlalchemy import select

            from app.models import Product, SitePage
            products = db.scalars(select(Product).join(SitePage, SitePage.product_id == Product.id).where(
                SitePage.site_id == site.id, Product.tenant_id == site.tenant_id))
            control = Select(Option("Choose product", value=""), *[Option(p.name, value=p.id,
                selected=value == p.id) for p in products], name=key)
        elif kind in {"json", "rich_text", "multi_line_text"}:
            control = Textarea(json.dumps(value) if kind == "json" and value != "" else str(value),
                               name=key, rows=4, required=rules.get("required", False))
        else:
            control = Input(name=key, value=value, required=rules.get("required", False),
                type={"integer": "number", "decimal": "number", "date": "date",
                      "url": "url", "colour": "color"}.get(kind, "text"),
                step="any" if kind == "decimal" else None)
        fields.append(Label(definition.name, control))
    title = "Custom data"
    if owner_type == "variant":
        variant = custom_data.owner(db, site, owner_type, owner_id)
        title += ": " + (variant.name or variant.sku)
    return card(Link(rel="stylesheet", href="/static/content-gaps.css"), H2(title), Form(csrf_input, *fields,
        Button("Save custom data", cls="a-button a-button-primary") if defs else P("Add a definition to start storing custom data on this resource."),
        method="post", cls="e-form cg-form", action=f"/admin/sites/{site.id}/custom-data/{owner_type}/{owner_id}"),
        A("Manage definitions", href=f"/admin/sites/{site.id}/settings/custom-data"))


def register_content_gap_routes(rt, actor, csrf, check_csrf, shell, error):
    render_shell = shell

    def shell(title, *children):
        return render_shell(title, Link(rel="stylesheet", href="/static/content-gaps.css"), *children)

    def base(site_id):
        return f"/admin/sites/{site_id}"

    def redirect(path, notice="Saved"):
        return RedirectResponse(path + "?" + urlencode({"notice": notice}), 303)

    @rt("/admin/sites/{site_id}/settings/custom-data", methods=["GET"])
    def definitions_get(session, site_id: str, definition: str = "", notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                defs = custom_data.definitions(db, site)
                selected = next((d for d in defs if d.id == definition), None)
                def value(key, default=""):
                    return getattr(selected, key, default) if selected else default
                action = base(site.id) + "/settings/custom-data"
                return shell("Custom data", page_header("Custom data", back_href=base(site.id) + "/settings"), P(notice, role="status"),
                    card(scroll_table(Thead(Tr(*[Th(x) for x in ["Name", "Owner", "Namespace.key", "Type", "Order", "Actions"]])),
                        Tbody(*[Tr(Td(d.name), Td(d.owner_type.title()), Td(d.namespace + "." + d.key),
                            Td(d.value_type.replace("_", " ")), Td(d.pinned_order),
                            Td(A("Edit", href=action + "?definition=" + d.id),
                               Form(csrf(session), Input(name="definition_id", value=d.id, type="hidden"),
                                    Button("Delete", name="action", value="delete"), method="post", action=action))) for d in defs]))) if defs else empty_state("Add your first definition", "Define reusable fields for products, variants, collections and pages."),
                    card(H2("Edit definition" if selected else "Add definition"), Form(csrf(session),
                        Input(name="definition_id", value=value("id"), type="hidden"),
                        Label("Name", Input(name="name", value=value("name"), required=True, maxlength=160)),
                        Label("Owner type", Select(*[Option(t.title(), value=t, selected=t == value("owner_type"))
                            for t in custom_data.OWNER_TYPES], name="owner_type")),
                        Label("Namespace", Input(name="namespace", value=value("namespace", "custom"), required=True)),
                        Label("Key", Input(name="key", value=value("key"), required=True)),
                        Label("Type", Select(*[Option(t.replace("_", " ").capitalize(), value=t,
                            selected=t == value("value_type")) for t in custom_data.VALUE_TYPES], name="value_type")),
                        Label("Validation rules (JSON)", Textarea(json.dumps(value("validation_json", {})), name="validation")),
                        P('Use required, min, max and choices. Example: {"required": true, "max": 120}'),
                        Label("Pinned order", Input(type="number", name="pinned_order", value=value("pinned_order", 0))),
                        Button("Save definition", cls="a-button a-button-primary"), method="post", action=action, cls="e-form cg-form")))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/custom-data", methods=["POST"])
    async def definitions_post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                if form.get("action") == "delete":
                    row = next((d for d in custom_data.definitions(db, site) if d.id == form.get("definition_id")), None)
                    if not row:
                        raise CommerceError("Definition not found.")
                    from sqlalchemy import delete
                    db.execute(delete(CustomDataValue).where(CustomDataValue.definition_id == row.id,
                        CustomDataValue.site_id == site.id, CustomDataValue.tenant_id == site.tenant_id))
                    db.delete(row)
                else:
                    data = dict(form)
                    data["validation"] = json.loads(str(form.get("validation", "{}")))
                    custom_data.save_definition(db, site, data, str(form.get("definition_id", "")))
                db.commit()
                return redirect(base(site.id) + "/settings/custom-data")
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/custom-data/{owner_type}/{owner_id}", methods=["GET"])
    def values_get(session, site_id: str, owner_type: str, owner_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                custom_data.owner(db, site, owner_type, owner_id)
                return shell("Custom data", page_header("Custom data"),
                             custom_data_card(db, site, owner_type, owner_id, csrf(session)))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/custom-data/{owner_type}/{owner_id}", methods=["POST"])
    async def values_post(session, request, site_id: str, owner_type: str, owner_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                custom_data.save_values(db, site, owner_type, owner_id,
                    {k: v for k, v in form.items() if k != "csrf_token"})
                db.commit()
                return redirect(base(site.id) + f"/custom-data/{owner_type}/{owner_id}")
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/preferences", methods=["GET"])
    def preferences_get(session, site_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                row = store_preferences.get_preferences(db, site)
                media = [m for m in media_for(db, site) if m.content_type.startswith("image/")]
                def value(key):
                    return getattr(row, key, "") if row else ""
                return shell("Preferences", page_header("Online Store preferences", back_href=base(site.id) + "/editor"), P(notice, role="status"),
                    card(Form(csrf(session), H2("Search engine listing"),
                        Label("Homepage title", Input(name="homepage_title", value=value("homepage_title"), maxlength=240)),
                        Label("Meta description", Textarea(value("meta_description"), name="meta_description", maxlength=500)),
                        *[Label(label, Select(Option("Default", value=""), *[Option(m.title or m.id, value=m.id,
                            selected=m.id == value(field)) for m in media], name=field)) for field, label in [
                            ("social_image_id", "Social sharing image"), ("favicon_id", "Favicon")]],
                        H2("Password protection"), Label(Input(name="password_enabled", type="checkbox",
                            checked=bool(value("password_enabled"))), " Require a storefront password"),
                        Label("New password", Input(name="password", type="password", minlength=8, maxlength=256,
                            autocomplete="new-password")), P("Leave blank to keep the current password. Merchant previews bypass protection."),
                        H2("Analytics and marketing"), P("Tags load only after the relevant visitor consent."),
                        Label("Google Analytics measurement ID", Input(name="analytics_id", value=value("analytics_id"))),
                        Label("Meta pixel ID", Input(name="meta_pixel_id", value=value("meta_pixel_id"))),
                        Button("Save preferences", cls="a-button a-button-primary"), method="post",
                        action=base(site.id) + "/preferences", cls="e-form cg-form")))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/preferences", methods=["POST"])
    async def preferences_post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                store_preferences.save_preferences(db, site, dict(form))
                db.commit()
                return redirect(base(site.id) + "/preferences")
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/redirects", methods=["GET"])
    def redirects_get(session, site_id: str, q: str = "", edit: str = "", notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                path = base(site.id) + "/redirects"
                rows = url_redirects.redirects(db, site, q)
                selected = next((r for r in url_redirects.redirects(db, site) if r.id == edit), None)
                return shell("URL redirects", page_header("URL redirects", secondary_actions=[A("Export CSV", href=path + "/export", cls="a-button")]), P(notice, role="status"),
                    card(Form(Label("Search redirects", Input(name="q", value=q, placeholder="Search redirects")), Button("Search"), method="get", action=path, cls="a-filter-bar"),
                        scroll_table(Thead(Tr(Th("From"), Th("To"), Th("Actions"))), Tbody(*[
                            Tr(Td(r.source), Td(r.target), Td(A("Edit", href=path + "?edit=" + r.id),
                                Form(csrf(session), Input(name="redirect_id", value=r.id, type="hidden"),
                                    Button("Delete", name="action", value="delete"), action=path, method="post"))) for r in rows]))) if rows else card(Form(Label("Search redirects", Input(name="q", value=q, placeholder="Search redirects")), Button("Search"), method="get", action=path, cls="a-filter-bar"), empty_state("No redirects found" if q else "Add your first redirect", "Keep old links working by redirecting visitors to the current page.")),
                    card(H2("Edit redirect" if selected else "Create redirect"), Form(csrf(session),
                        Input(name="redirect_id", value=selected.id if selected else "", type="hidden"),
                        Label("Redirect from", Input(name="source", value=selected.source if selected else "", required=True, placeholder="/old-path")),
                        Label("Redirect to", Input(name="target", value=selected.target if selected else "", required=True, placeholder="/new-path")),
                        Button("Save redirect", cls="a-button a-button-primary"), action=path, method="post", cls="e-form cg-form")),
                    card(H2("Import CSV"), Form(csrf(session), Label("CSV file", Input(name="file", type="file", accept=".csv", required=True)),
                        P("Columns: Redirect from, Redirect to. Loops and chains are rejected."),
                        Button("Import redirects"), method="post", action=path + "/import", enctype="multipart/form-data", cls="e-form cg-form")))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/redirects", methods=["POST"])
    async def redirects_post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                if form.get("action") == "delete":
                    row = next((r for r in url_redirects.redirects(db, site) if r.id == form.get("redirect_id")), None)
                    if not row:
                        raise CommerceError("Redirect not found.")
                    db.delete(row)
                else:
                    url_redirects.save_redirect(db, site, form.get("source", ""), form.get("target", ""), str(form.get("redirect_id", "")))
                db.commit()
                return redirect(base(site.id) + "/redirects")
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/redirects/export", methods=["GET"])
    def redirects_export(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                return Response(url_redirects.export_csv(db, site), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="redirects.csv"'})
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/redirects/import", methods=["POST"])
    async def redirects_import(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            upload = form.get("file")
            if not upload or not hasattr(upload, "read"):
                raise CommerceError("Choose a CSV file.")
            raw = await upload.read(2_000_001)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                results = url_redirects.import_csv(db, site, raw.decode("utf-8-sig"))
                db.commit()
                failures = [f"Row {r['row']}: {r['error']}" for r in results if "error" in r]
                return shell("Redirect import", page_header("Redirect import"),
                    P(f"Imported {len(results) - len(failures)} redirects."), *[P(f) for f in failures],
                    A("Back to redirects", href=base(site.id) + "/redirects"))
        except (CommerceError, UnicodeError, ValueError) as exc:
            return error(exc)

    @rt("/sites/{slug}/password", methods=["GET", "POST"])
    async def storefront_password(session, request, slug: str):
        from html import escape

        from sqlalchemy import select
        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.slug == slug, Site.status.in_(["published", "preview"])))
            if not site:
                return Response("Store not found", 404)
            preferences = store_preferences.get_preferences(db, site)
            site_base = request.scope.get("site_base", f"/sites/{site.slug}")
            message = ""
            if request.method == "POST":
                form = await request.form()
                try:
                    check_csrf(session, form)
                    if not password_attempt_allowed():
                        return Response("Too many attempts. Try again shortly.", 429)
                    if not store_preferences.password_matches(form.get("password", ""), preferences):
                        raise CommerceError("That password was not recognised.")
                    response = RedirectResponse(site_base + "/", 303)
                    response.set_cookie("fastshop_store_" + site.id, store_preferences.access_token(site, preferences),
                        max_age=86400, httponly=True, secure=request.url.scheme == "https", samesite="lax",
                        path=site_base or "/")
                    return response
                except CommerceError as exc:
                    message = str(exc)
            csrf(session)
            markup = ('<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1">'
                '<meta name="robots" content="noindex,nofollow"><title>Store password</title>'
                '<link rel="icon" href="/static/favicon.svg">'
                '<link rel="stylesheet" href="/static/store-password.css"></head><body><main>'
                f'<p class="eyebrow">Coming soon</p><h1>{escape(site.name)}</h1><p>Enter the store password to continue.</p>'
                f'<p role="alert">{escape(message)}</p><form method="post" action="{escape(site_base)}/password">'
                f'<input type="hidden" name="csrf_token" value="{escape(session["csrf_token"])}">'
                '<label for="password">Store password</label><input id="password" name="password" type="password" required autocomplete="current-password">'
                '<button>Enter store</button></form><a href="/login">Merchant sign in</a></main></body></html>')
            return HTMLResponse(markup, headers={"Cache-Control": "no-store"})
