"""Authorised, CSRF-protected customer administration routes."""

import json
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from sqlalchemy import select
from starlette.responses import RedirectResponse, Response

from app import content
from app import customer_admin as customers
from app import customer_admin_ui as ui
from app.content_gap_routes import custom_data_card
from app.customer_services import orders_for
from app.db import SessionLocal
from app.models import CommerceMail
from app.services import CommerceError


def register_customer_admin_routes(rt, actor, csrf, check_csrf, shell, error):
    def redirect(site_id, path="", notice="Customer saved."):
        return RedirectResponse(f"/admin/sites/{site_id}/customers" + path + "?" + urlencode({"notice": notice}), status_code=303)

    def filters_for(db, site, params):
        filters = {}
        if params.get("segment"):
            segment = next((s for s in customers.segments_for(db, site) if s.id == params["segment"]), None)
            if not segment:
                raise CommerceError("Customer segment not found.")
            filters = dict(segment.filters_json)
        filters.update({key: value for key, value in params.items() if key in customers.FILTER_KEYS})
        for key in ("spent_min", "spent_max"):
            if key + "_amount" in params:
                value = params[key + "_amount"]
                if not value:
                    filters.pop(key, None)
                    continue
                try:
                    amount = Decimal(value) * 100
                    if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
                        raise ValueError
                    filters[key] = str(int(amount))
                except (ValueError, InvalidOperation) as exc:
                    raise CommerceError("Enter a non-negative amount with at most two decimal places.") from exc
        return customers.validate_filters(filters)

    @rt("/admin/sites/{site_id}/customers", methods=["GET"])
    def get(session, request, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                filters = filters_for(db, site, request.query_params)
                rows = customers.customer_rows(db, site, filters)
                page = max(1, min(int(request.query_params.get("page", "1")), max(1, (len(rows) + 24) // 25)))
                return shell("Customers", ui.customer_index(site, rows[(page - 1) * 25:page * 25], len(rows), filters, page, csrf(session), customers.segments_for(db, site)))
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/customers/export", methods=["GET"])
    def get(session, request, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                payload = customers.export_csv(db, site, filters_for(db, site, request.query_params))
                return Response(payload, media_type="text/csv", headers={
                    "Content-Disposition": 'attachment; filename="customers.csv"', "Cache-Control": "private, no-store"})
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/customers/mail", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                messages = list(db.scalars(select(CommerceMail).where(CommerceMail.tenant_id == site.tenant_id,
                    CommerceMail.site_id == site.id).order_by(CommerceMail.created_at.desc()).limit(100)))
                return shell("Email queue", ui.mail_page(site, messages))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/customers/new", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                return shell("Add customer", ui.new_customer(site, csrf(session)))
        except CommerceError as exc:
            return error(exc)

    async def save(session, request, site_id, customer_id=None):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                data = {key: str(form.get(key, "")) for key in ("name", "email", "phone", "note", "tags", "account_state")}
                data.update({key: form.get(key) == "on" for key in ("email_marketing_consent", "sms_marketing_consent")})
                customer = customers.save_customer(db, site, data, customer_id)
                db.commit()
                return redirect(site.id, "/" + customer.id)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/customers/new", methods=["POST"])
    async def post(session, request, site_id: str):
        return await save(session, request, site_id)

    @rt("/admin/sites/{site_id}/customers/import", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                return shell("Import customers", ui.import_page(site, csrf(session)))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/customers/import", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                upload = form.get("file")
                if upload and hasattr(upload, "read"):
                    payload = await upload.read(2 * 1024 * 1024 + 1)
                else:
                    payload = str(form.get("csv_text", "")).encode("utf-8")
                apply = form.get("apply") == "yes"
                results = customers.import_csv(db, site, payload, apply=apply)
                if apply:
                    db.commit()
                return shell("Import customers", ui.import_page(site, csrf(session), results,
                    payload.decode("utf-8-sig"), applied=apply))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/customers/bulk", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                customers.bulk_tag(db, site, form.getlist("customer_ids"), form.get("tags", ""))
                db.commit()
                return redirect(site.id, notice="Customer tags updated.")
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/customers/segments", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            filters = json.loads(str(form.get("filters_json", "{}")))
            if not isinstance(filters, dict):
                raise CommerceError("Choose valid segment filters.")
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                segment = customers.save_segment(db, site, form.get("name", ""), filters)
                db.commit()
                return RedirectResponse(f"/admin/sites/{site.id}/customers?segment={segment.id}", status_code=303)
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/customers/{customer_id}", methods=["GET"])
    def get(session, site_id: str, customer_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                customer = customers.owned_customer(db, site, customer_id)
                row = next(row for row in customers.customer_rows(db, site) if row.customer.id == customer.id)
                return shell(customer.name or customer.email,
                    ui.customer_detail(site, customer, row, orders_for(db, site, customer),
                        customers.addresses_for(db, site, customer), customers.timeline_for(db, site, customer), csrf(session)),
                    custom_data_card(db, site, "customer", customer.id, csrf(session)))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/customers/{customer_id}", methods=["POST"])
    async def post(session, request, site_id: str, customer_id: str):
        return await save(session, request, site_id, customer_id)

    @rt("/admin/sites/{site_id}/customers/{customer_id}/comments", methods=["POST"])
    async def post(session, request, site_id: str, customer_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=True)
                customers.add_comment(db, site, customer_id, user_id, form.get("body", ""))
                db.commit()
                return redirect(site.id, "/" + customer_id, "Comment added.")
        except CommerceError as exc:
            return error(exc)

    async def address(session, request, site_id, customer_id, address_id=None):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                data = {key: form.get(key, "") for key in customers.ADDRESS_FIELDS}
                data["is_default"] = form.get("is_default") == "on"
                customers.save_address(db, site, customer_id, data, address_id, delete=form.get("action") == "delete")
                db.commit()
                return redirect(site.id, "/" + customer_id, "Address updated.")
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/customers/{customer_id}/addresses", methods=["POST"])
    async def post(session, request, site_id: str, customer_id: str):
        return await address(session, request, site_id, customer_id)

    @rt("/admin/sites/{site_id}/customers/{customer_id}/addresses/{address_id}", methods=["POST"])
    async def post(session, request, site_id: str, customer_id: str, address_id: str):
        return await address(session, request, site_id, customer_id, address_id)
