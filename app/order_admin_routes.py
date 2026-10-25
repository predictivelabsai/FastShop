"""Tenant-authorised order workspaces, retaining the existing provider commands."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse, Response

from app import content
from app import order_admin as orders
from app import order_admin_ui as ui
from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway
from app.services import CommerceError


def quantities(form):
    try:
        return {key[4:]: int(value) for key, value in form.items()
                if key.startswith("qty_") and str(value).strip() and int(value) != 0}
    except (TypeError, ValueError) as exc:
        raise CommerceError("Enter whole item quantities.") from exc


def shipping_minor(value):
    try:
        amount = Decimal(str(value or "0")) * 100
        if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
            raise ValueError
        return int(amount)
    except (ValueError, InvalidOperation) as exc:
        raise CommerceError("Enter a shipping amount with at most two decimal places.") from exc


def csv_response(csv):
    return Response(csv, media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": 'attachment; filename="orders.csv"'})


def register_order_admin_routes(rt, actor, csrf, check_csrf, shell, error):
    def access(db, session, site_id):
        user_id = actor(session)
        return content.owned_site(db, site_id, user_id, publish=True), user_id

    def redirect(site_id, link_id="", notice=""):
        url = f"/admin/sites/{site_id}/orders" + (f"/{link_id}" if link_id else "")
        return RedirectResponse(url + ("?" + urlencode({"notice": str(notice)[:500]}) if notice else ""),
                                status_code=303)

    def filters_for(request):
        return {key: request.query_params.get(key, "") for key in (
            "view", "q", "payment", "fulfilment", "start", "end", "tag", "channel", "sort", "page", "per_page")
                if request.query_params.get(key, "")}

    @rt("/admin/sites/{site_id}/orders/export", methods=["GET"])
    def get(session, request, site_id: str):
        try:
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                return csv_response(orders.export_orders(db, site, filters_for(request)))
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/orders/bulk", methods=["POST"])
    async def post(session, request, site_id: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id)
                ids, action = form.getlist("ids"), str(form.get("action", ""))
                if not ids:
                    raise CommerceError("Select at least one order.")
                if action == "export":
                    # Resolve every identifier before exporting, including foreign IDs.
                    for identifier in ids:
                        orders.detail(db, site, identifier)
                    return csv_response(orders.export_orders(db, site, {"ids": ids}))
                value = str(form.get("value", ""))
                if action == "fulfil":
                    value = json.dumps({key: str(form.get(key, "")) for key in (
                        "carrier", "tracking_number", "tracking_url", "request_key")})
                count = orders.bulk_orders(db, site, user_id, ids, action, value)
                db.commit()
            return redirect(site_id, notice=f"Updated {count} orders.")
        except (CommerceError, ValueError) as exc:
            return redirect(site_id, notice=exc)

    @rt("/admin/sites/{site_id}/orders", methods=["GET"])
    def get(session, request, site_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                filters = filters_for(request)
                return shell("Orders", ui.orders_index(site, orders.list_orders(db, site, filters),
                                                       filters, csrf(session), notice))
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/orders/{site_order_id}/packing-slip", methods=["GET"])
    def get(session, site_id: str, site_order_id: str):
        try:
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                return shell("Packing slip", ui.packing_slip(site, orders.detail(db, site, site_order_id)))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/orders/{site_order_id}", methods=["GET"])
    def get(session, site_id: str, site_order_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                data = orders.detail(db, site, site_order_id)
                return shell(data["order"].number, ui.order_detail(site, data, lambda: csrf(session), notice))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/orders/{site_order_id}/fulfil-items", methods=["POST"])
    async def post(session, request, site_id: str, site_order_id: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id)
                orders.fulfil_items(db, site, user_id, site_order_id, quantities(form),
                    carrier=str(form.get("carrier", "")), tracking_number=str(form.get("tracking_number", "")),
                    tracking_url=str(form.get("tracking_url", "")), notify=form.get("notify") == "on",
                    request_key=str(form.get("request_key", "")))
                db.commit()
            return redirect(site_id, site_order_id, "Selected items fulfilled.")
        except (CommerceError, ValueError) as exc:
            return redirect(site_id, site_order_id, exc)

    def refund_values(form, data=None):
        values = {"quantities": quantities(form), "shipping_minor": shipping_minor(form.get("shipping", "0")),
                  "restock": form.get("restock") == "on", "reason": str(form.get("reason", "")).strip(),
                  "return_id": str(form.get("return_id", "")) or None}
        if data and values["return_id"] and not values["quantities"]:
            row = next((row for row in data["returns"] if row.id == values["return_id"]), None)
            if not row:
                raise CommerceError("Return not found.")
            values["quantities"] = {line.order_line_id: line.quantity for line in row.lines}
            values["reason"] = values["reason"] or row.reason
            if len(values["reason"]) < 10:
                values["reason"] = "Return refund: " + values["reason"]
        return values

    @rt("/admin/sites/{site_id}/orders/{site_order_id}/refund-items", methods=["GET"])
    def get(session, request, site_id: str, site_order_id: str):
        try:
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                data = orders.detail(db, site, site_order_id)
                values = refund_values(request.query_params, data)
                preview = orders.preview_refund(db, site, site_order_id, values["quantities"],
                    values["shipping_minor"], values["restock"], values["return_id"])
                if len(values["reason"]) < 10 or len(values["reason"]) > 500:
                    raise CommerceError("Give a refund reason between 10 and 500 characters.")
                return shell("Review refund", ui.refund_review(site, data, preview, values, csrf(session)))
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/orders/{site_order_id}/refund-items", methods=["POST"])
    async def post(session, request, site_id: str, site_order_id: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            if form.get("confirmed") != "on":
                raise CommerceError("Confirm the exact refund before continuing.")
            with SessionLocal() as db:
                _, user_id = access(db, session, site_id)
            values = refund_values(form)
            command = await run_in_threadpool(orders.refund_items,
                site_id, user_id, site_order_id, values["quantities"], values["shipping_minor"],
                values["restock"], values["reason"], str(form.get("request_key", "")), values["return_id"],
                sessions=SessionLocal, gateway_factory=StripeGateway,
                expected_amount_minor=int(form.get("expected_amount_minor", "0")))
            return redirect(site_id, site_order_id, "Refund " + command.state.replace("_", " ") + ".")
        except (CommerceError, ValueError) as exc:
            return redirect(site_id, site_order_id, exc)

    @rt("/admin/sites/{site_id}/orders/{site_order_id}/returns", methods=["POST"])
    async def post(session, request, site_id: str, site_order_id: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id)
                orders.create_return(db, site, user_id, site_order_id, quantities(form),
                                     str(form.get("reason", "")), form.get("restock") == "on")
                db.commit()
            return redirect(site_id, site_order_id, "Return requested.")
        except (CommerceError, ValueError) as exc:
            return redirect(site_id, site_order_id, exc)

    @rt("/admin/sites/{site_id}/orders/{site_order_id}/returns/{return_id}/status", methods=["POST"])
    async def post(session, request, site_id: str, site_order_id: str, return_id: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id)
                orders.transition_return(db, site, user_id, site_order_id, return_id, str(form.get("target", "")))
                db.commit()
            return redirect(site_id, site_order_id, "Return updated.")
        except (CommerceError, ValueError) as exc:
            return redirect(site_id, site_order_id, exc)

    @rt("/admin/sites/{site_id}/orders/{site_order_id}/comments", methods=["POST"])
    async def post(session, request, site_id: str, site_order_id: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id)
                orders.add_comment(db, site, user_id, site_order_id, str(form.get("body", "")), True)
                db.commit()
            return redirect(site_id, site_order_id, "Comment added.")
        except (CommerceError, ValueError) as exc:
            return redirect(site_id, site_order_id, exc)

    def update_handler(endpoint, key, notice):
        @rt(f"/admin/sites/{{site_id}}/orders/{{site_order_id}}/{endpoint}", methods=["POST"])
        async def post(session, request, site_id: str, site_order_id: str):
            try:
                form = await request.form()
                check_csrf(session, form)
                value = form.get(endpoint, "")
                if key == "archived":
                    value = form.get("archived") == "1"
                with SessionLocal() as db:
                    site, user_id = access(db, session, site_id)
                    orders.update_order(db, site, user_id, site_order_id, **{key: value})
                    db.commit()
                return redirect(site_id, site_order_id, notice)
            except (CommerceError, ValueError) as exc:
                return redirect(site_id, site_order_id, exc)
        return post

    update_handler("tags", "tags", "Tags saved.")
    update_handler("note", "note", "Note saved.")
    update_handler("archive", "archived", "Archive state saved.")
