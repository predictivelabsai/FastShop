"""Scoped routes for locations, audit records and per-user list preferences."""

import json

from fasthtml.common import A, Button, Form
from starlette.responses import RedirectResponse

from app import admin_gap_ui as ui
from app import admin_views as views
from app import content, locations
from app.admin_activity import activities_for, activity_context
from app.admin_ui import card, empty_state, index_table, page_header
from app.db import SessionLocal
from app.services import CommerceError


def register_admin_gap_routes(rt, actor, csrf, check_csrf, shell, error):
    async def mutate_view(session, request, site_id: str, resource: str, operation: str = ""):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id)
                views.resource_name(resource)
                if operation == "columns":
                    views.save_columns(db, site, user_id, resource, form.getlist("columns"))
                    destination = f"/admin/sites/{site.id}/{resource}"
                elif operation == "manage":
                    row = views.view_for(db, site, user_id, resource, str(form.get("view_id", "")))
                    if form.get("action") == "delete":
                        db.delete(row)
                        destination = f"/admin/sites/{site.id}/{resource}"
                    elif form.get("action") == "rename":
                        row = views.save_view(db, site, user_id, resource, form.get("name", ""),
                            row.filters_json, row.sort, row.columns_json, row.id)
                        destination = views.view_url(site, row)
                    else:
                        raise CommerceError("Unknown view action.")
                elif not operation:
                    filters = json.loads(str(form.get("filters", "{}")))
                    if not isinstance(filters, dict):
                        raise CommerceError("Invalid view filters.")
                    row = views.save_view(db, site, user_id, resource, form.get("name", ""),
                        filters, form.get("sort", ""), form.getlist("columns"))
                    destination = views.view_url(site, row)
                else:
                    raise CommerceError("Unknown view action.")
                db.commit()
            return RedirectResponse(destination, status_code=303)
        except (CommerceError, ValueError, TypeError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/views/{resource}", methods=["POST"])
    async def post(session, request, site_id: str, resource: str):
        return await mutate_view(session, request, site_id, resource)

    @rt("/admin/sites/{site_id}/views/{resource}/{operation}", methods=["POST"])
    async def post(session, request, site_id: str, resource: str, operation: str):
        return await mutate_view(session, request, site_id, resource, operation)

    @rt("/admin/sites/{site_id}/activity", methods=["GET"])
    def get(session, request, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                filters = {key: request.query_params.get(key, "") for key in ("object_type", "object_id", "actor_id", "action")}
                return shell("Activity", ui.activity_page(activities_for(db, site, **filters), site, filters))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/locations", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                rows = locations.locations_for(db, site)
                base = f"/admin/sites/{site.id}/settings/locations"
                return shell("Locations", page_header("Locations", back_href=f"/admin/sites/{site.id}/settings"), index_table(["Location", "Address", "Status", "Online orders", "Default"], [
                    [A(r.name, href=base + "/" + r.id), ", ".join(v for v in (r.address_json or {}).values() if v),
                     "Active" if r.is_active else "Inactive", "Yes" if r.fulfils_online_orders else "No", "Yes" if r.is_default else ""] for r in rows], search="Search locations") if rows else
                    empty_state("Add your first location", "Manage where you store stock and fulfil online orders."),
                    card(ui.location_form(site, None, csrf(session)), title="Add location"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/locations/{location_id}", methods=["GET"])
    def get(session, site_id: str, location_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                row = locations.location_for(db, site, location_id)
                return shell("Edit location", page_header("Edit location", back_href=f"/admin/sites/{site.id}/settings/locations"), card(ui.location_form(site, row, csrf(session))),
                    card(Form(csrf(session), Button("Delete location"), method="post",
                         action=f"/admin/sites/{site.id}/settings/locations/{row.id}/delete")),
                    ui.activity_card(db, site, "location", row.id))
        except CommerceError as exc:
            return error(exc)

    async def mutate_location(session, request, site_id: str, location_id: str = "", operation: str = ""):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=True)
                with activity_context(db, site, user_id):
                    if operation == "delete":
                        locations.delete_location(db, site, location_id)
                    elif not operation:
                        data = dict(form)
                        data.update({key: form.get(key) == "on" for key in ("is_active", "fulfils_online_orders", "is_default")})
                        locations.save_location(db, site, data, location_id or None)
                    else:
                        raise CommerceError("Unknown location action.")
                    db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/settings/locations", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/locations", methods=["POST"])
    async def post(session, request, site_id: str):
        return await mutate_location(session, request, site_id)

    @rt("/admin/sites/{site_id}/settings/locations/{location_id}", methods=["POST"])
    async def post(session, request, site_id: str, location_id: str):
        return await mutate_location(session, request, site_id, location_id)

    @rt("/admin/sites/{site_id}/settings/locations/{location_id}/{operation}", methods=["POST"])
    async def post(session, request, site_id: str, location_id: str, operation: str):
        return await mutate_location(session, request, site_id, location_id, operation)
