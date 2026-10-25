"""Additive merchant controls for personal views, locations and activity."""

import json
from datetime import UTC

from fasthtml.common import (
    A,
    Button,
    Details,
    Div,
    Form,
    Input,
    Label,
    Nav,
    P,
    Script,
    Small,
    Summary,
)
from sqlalchemy import select

from app import admin_views as views
from app.admin_activity import activities_for
from app.admin_ui import card, empty_state, index_table, page_header, timeline
from app.models import Membership, User


def view_controls(db, site, user_id, resource, params, csrf):
    columns = views.columns_for(db, site, user_id, resource, params.get("saved_view", ""))
    base = f"/admin/sites/{site.id}/views/{resource}"
    rows = views.views_for(db, site, user_id, resource)
    def column_inputs():
        return [Label(Input(type="checkbox", name="columns", value=c, checked=c in columns), c, cls="a-check-option")
                for c in views.COLUMNS[resource]]
    return Div(
        Nav(A("All", href=f"/admin/sites/{site.id}/{resource}", data_saved_view_link="",
              cls="is-active" if not params.get("saved_view") else None), *[A(row.name, href=views.view_url(site, row), data_saved_view_link="", cls="is-active" if row.id == params.get("saved_view") else None)
              for row in rows], aria_label="Saved views", cls="a-tabs"),
        Details(Summary("Save view", cls="a-button"), Form(csrf,
            Input(type="hidden", name="filters", value=json.dumps(dict(params))),
            Input(type="hidden", name="sort", value=params.get("sort", "")),
            Label("View name", Input(name="name", maxlength="80", required=True)),
            *column_inputs(), Button("Save view", cls="e-button"), action=base, method="post", cls="a-filter-panel e-form"), cls="a-view-popover"),
        Details(Summary("Columns", cls="a-button"), Form(csrf, *column_inputs(),
            Button("Save columns", cls="e-button"), action=base + "/columns", method="post", cls="a-filter-panel e-form"), cls="a-view-popover"),
        Details(Summary("Manage saved views", cls="a-button"), Div(*[Form(csrf,
            Input(type="hidden", name="view_id", value=row.id),
            Label("Rename " + row.name, Input(name="name", value=row.name, required=True)),
            Button("Rename", name="action", value="rename"),
            Button("Delete", name="action", value="delete"), action=base + "/manage", method="post", cls="e-form") for row in rows], cls="a-filter-panel"), cls="a-view-popover") if rows else None,
        Script(src="/static/admin/saved-views.js", defer=True),
        cls="a-view-controls", data_visible_columns=json.dumps(columns),
        data_available_columns=json.dumps(views.COLUMNS[resource]))


def _activity_actors(db, site, rows):
    identifiers = {row.actor_id for row in rows if row.actor_id}
    if not identifiers:
        return {}
    users = db.scalars(select(User).join(Membership, Membership.user_id == User.id).where(
        Membership.tenant_id == site.tenant_id, User.id.in_(identifiers)))
    return {user.id: user.name.strip() or user.email for user in users}


def _activity_actor(row, actors):
    return actors.get(row.actor_id, "Former team member") if row.actor_id else "System"


def _activity_date(value):
    if value.tzinfo:
        value = value.astimezone(UTC)
    return value.strftime("%d %b %Y at %H:%M UTC")


def activity_card(db, site, object_type="", object_id=""):
    rows = activities_for(db, site, object_type=object_type, object_id=object_id)[:5]
    actors = _activity_actors(db, site, rows)
    return card(timeline([Div(P(row.summary.replace("_", " ")),
        Small(_activity_actor(row, actors) + " · " + _activity_date(row.created_at)))
        for row in rows]) if rows else P("Changes made by your team appear here."),
        A("View activity", href=f"/admin/sites/{site.id}/activity?object_type={object_type}&object_id={object_id}"), title="Activity")


def activity_page(db, rows, site, params):
    actors = _activity_actors(db, site, rows)
    return Div(page_header("Activity", back_href=f"/admin/sites/{site.id}/settings"),
        card(Form(*[Label(label, Input(name=key, value=params.get(key, "")))
        for key, label in (("object_type", "Object type"), ("object_id", "Object ID"),
                           ("actor_id", "Actor ID"), ("action", "Action"))],
        Button("Filter activity"), action=f"/admin/sites/{site.id}/activity", method="get", cls="e-form a-filter-bar")),
        index_table(["When", "Actor", "Object", "Action", "Summary"], [
            [_activity_date(r.created_at), _activity_actor(r, actors),
             r.object_type.replace("_", " ").capitalize(),
             r.action.replace("_", " ").capitalize(), r.summary.replace("_", " ")]
            for r in rows]) if rows else
        empty_state("No activity found", "Changes made by your team appear here. Try clearing the filters to see all activity."))


def location_form(site, location, csrf):
    data = location.address_json or {} if location else {}
    return Form(csrf,
        *[Label(label, Input(name=key, value=getattr(location, key, default) if key in {"name", "country_code"} else data.get(key, ""), required=key in {"name", "country_code"}))
          for key, label, default in (("name", "Location name", ""), ("address", "Address", ""),
             ("city", "City", ""), ("region", "Region", ""), ("postal_code", "Postal code", ""),
             ("country_code", "Country code", "US"))],
        *[Label(Input(type="checkbox", name=key, checked=getattr(location, key, default)), label)
          for key, label, default in (("is_active", "Active", True),
            ("fulfils_online_orders", "Fulfils online orders", True), ("is_default", "Default location", False))],
        Button("Save location", cls="e-button"), method="post",
        action=f"/admin/sites/{site.id}/settings/locations" + ("/" + location.id if location else ""), cls="e-form")
