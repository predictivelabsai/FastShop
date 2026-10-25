"""Merchant spreadsheet and theme library surfaces in the P1 shell."""
import json

from fasthtml.common import H2, A, Button, Div, Form, Input, Label, NotStr, P, Script
from starlette.responses import JSONResponse, RedirectResponse, Response

from app import bulk_edit, content, theme_library
from app.admin_ui import card, empty_state, page_header
from app.db import SessionLocal
from app.services import CommerceError


def register_theme_bulk_routes(rt, actor, csrf, check_csrf, shell, error):
    @rt("/admin/sites/{site_id}/products/bulk-edit", methods=["GET"])
    def bulk_page(session, site_id: str, ids: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                rows, locations = bulk_edit.rows_for(db, site, ids.split(",") if ids else [])
                csrf(session)
                config = json.dumps({"site": site.id, "csrf": session["csrf_token"],
                    "rows": rows, "locations": locations}).replace("<", "\\u003c")
                return shell("Bulk editor", page_header("Bulk editor", back_href=f"/admin/sites/{site.id}/products"), P("Edit prices in minor currency units. Stock is on-hand stock."),
                    card(Div(id="bulk-grid"),
                        NotStr('<script type="application/json" id="bulk-grid-data">' + config + '</script>'),
                        Script(src="/static/admin/bulk-editor.js", defer=True)))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/products/bulk-edit", methods=["POST"])
    async def bulk_save(session, request, site_id: str):
        try:
            check_csrf(session, {"csrf_token": request.headers.get("X-CSRF-Token", "")})
            if len(await request.body()) > 1_000_000:
                raise CommerceError("This bulk edit is too large.")
            data = await request.json()
            if not isinstance(data, dict):
                raise CommerceError("Send a JSON object.")
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=True)
                result = bulk_edit.save_rows(db, site, data.get("rows"), user_id)
                db.commit()
                return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except (CommerceError, ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=403 if "session" in str(exc) else 422)

    @rt("/admin/sites/{site_id}/themes", methods=["GET"])
    def themes(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                base = f"/admin/sites/{site.id}/themes"

                def form(theme, action, label, extra=None):
                    return Form(csrf(session), Input(type="hidden", name="version", value=theme.version),
                        extra, Button(label, cls="a-button"), method="post",
                        action=f"{base}/{theme.id}/{action}", cls="e-actions")

                items = []
                for theme in theme_library.themes_for(db, site):
                    items.append(card(H2(theme.name), P("Previous live theme" if theme.kind == "revision" else "Draft theme"),
                        P(theme.created_at.strftime("Saved %d %B %Y at %H:%M:%S UTC")),
                        Div(A("Preview", href=f"{base}/{theme.id}/preview", cls="a-button"),
                            A("Edit theme", href=f"/admin/sites/{site.id}/editor?theme={theme.id}",
                              cls="a-button") if theme.kind == "draft" else None, cls="e-actions"),
                        form(theme, "publish", "Roll back" if theme.kind == "revision" else "Publish"),
                        form(theme, "rename", "Rename", Label("Theme name", Input(name="name", value=theme.name,
                            maxlength=160, required=True))), form(theme, "delete", "Delete")))
                return shell("Themes", page_header("Themes", primary_action=A("Customise current theme", href=f"/admin/sites/{site.id}/editor", cls="a-button")), card(H2("Current theme"), P("Published design and page drafts"),
                    A("Customise", href=f"/admin/sites/{site.id}/editor", cls="a-button"),
                    Form(csrf(session), Label("Copy name", Input(name="name", value="Theme copy",
                        maxlength=160, required=True)), Button("Duplicate current theme", cls="a-button"),
                        method="post", action=base + "/duplicate", cls="e-actions")), *(items or [empty_state("Build your theme library", "Duplicate the current theme to try changes before publishing.")]))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/themes/duplicate", methods=["POST"])
    async def theme_duplicate(session, request, site_id: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id)
                theme_library.duplicate(db, site, user_id, form.get("name", "Theme copy"))
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/themes", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/themes/{theme_id}/{action}", methods=["POST"])
    async def theme_action(session, request, site_id: str, theme_id: str, action: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=action in {"publish", "delete"})
                version = int(form.get("version", "0"))
                if action == "publish":
                    theme_library.publish(db, site, user_id, theme_id, version)
                elif action == "rename":
                    theme_library.rename(db, site, user_id, theme_id, form.get("name", ""), version)
                elif action == "delete":
                    theme_library.remove(db, site, user_id, theme_id, version)
                else:
                    raise CommerceError("Unknown theme action.")
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/themes", status_code=303)
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/themes/{theme_id}/preview", methods=["GET"])
    def theme_preview(session, site_id: str, theme_id: str, page: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                preview_site, preview_page = theme_library.context(db, site, theme_id, page)
                from app.site_editor_routes import render_document
                return Response(render_document(db, preview_site, preview_page,
                    preview_page.draft_json, ""), media_type="text/html",
                    headers={"Cache-Control": "no-store"})
        except CommerceError as exc:
            return error(exc)
