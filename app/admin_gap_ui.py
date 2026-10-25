"""Additive merchant controls for personal views, locations and activity."""

import json

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
    Summary,
)

from app import admin_views as views
from app.admin_activity import activities_for
from app.admin_ui import card, empty_state, index_table


def view_controls(db, site, user_id, resource, params, csrf):
    columns = views.columns_for(db, site, user_id, resource, params.get("saved_view", ""))
    base = f"/admin/sites/{site.id}/views/{resource}"
    rows = views.views_for(db, site, user_id, resource)
    def column_inputs():
        return [Label(Input(type="checkbox", name="columns", value=c, checked=c in columns), c)
                for c in views.COLUMNS[resource]]
    return Div(
        Nav(*[A(row.name, href=views.view_url(site, row), cls="is-active" if row.id == params.get("saved_view") else None)
              for row in rows], aria_label="Saved views", cls="a-tabs"),
        Details(Summary("Save view"), Form(csrf,
            Input(type="hidden", name="filters", value=json.dumps(dict(params))),
            Input(type="hidden", name="sort", value=params.get("sort", "")),
            Label("View name", Input(name="name", maxlength="80", required=True)),
            *column_inputs(), Button("Save view", cls="e-button"), action=base, method="post")),
        Details(Summary("Columns"), Form(csrf, *column_inputs(),
            Button("Save columns", cls="e-button"), action=base + "/columns", method="post")),
        Details(Summary("Manage saved views"), *[Form(csrf,
            Input(type="hidden", name="view_id", value=row.id),
            Label("Rename " + row.name, Input(name="name", value=row.name, required=True)),
            Button("Rename", name="action", value="rename"),
            Button("Delete", name="action", value="delete"), action=base + "/manage", method="post") for row in rows]) if rows else None,
        Script(src="/static/admin/saved-views.js", defer=True),
        cls="a-card a-view-controls", data_visible_columns=json.dumps(columns),
        data_available_columns=json.dumps(views.COLUMNS[resource]))


def activity_card(db, site, object_type="", object_id=""):
    rows = activities_for(db, site, object_type=object_type, object_id=object_id)
    return card(index_table(["When", "Actor", "Action"], [
        [str(r.created_at), r.actor_id or "System", r.summary] for r in rows[:20]]) if rows else
        P("Changes made by your team appear here."),
        A("View activity", href=f"/admin/sites/{site.id}/activity?object_type={object_type}&object_id={object_id}"), title="Activity")


def activity_page(rows, site, params):
    return Div(card(Form(*[Label(label, Input(name=key, value=params.get(key, "")))
        for key, label in (("object_type", "Object type"), ("object_id", "Object ID"),
                           ("actor_id", "Actor ID"), ("action", "Action"))],
        Button("Filter activity"), action=f"/admin/sites/{site.id}/activity", method="get", cls="e-form")),
        index_table(["When", "Actor", "Object", "Action", "Summary"], [
            [str(r.created_at), r.actor_id or "System", r.object_type + " " + r.object_id, r.action, r.summary] for r in rows]) if rows else
        empty_state("No activity yet", "Changes made by your team will appear here."))


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
