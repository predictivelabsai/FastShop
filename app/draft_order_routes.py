"""Manager-only draft order and checkout-history routes."""
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from sqlalchemy import or_, select
from starlette.responses import JSONResponse, RedirectResponse

from app import content
from app import draft_order_ui as ui
from app import draft_orders as drafts
from app.db import SessionLocal
from app.models import ShopCustomer
from app.services import CommerceError


def minor(value):
    try:
        amount = Decimal(str(value or "0"))*100
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise ValueError
        return drafts.integer(int(amount), "amount")
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise CommerceError("Enter a non-negative amount with at most two decimal places.") from exc


def register_draft_order_routes(rt, actor, csrf, check_csrf, shell, error):
    def site_for(db, session, site_id):
        return content.owned_site(db, site_id, actor(session), publish=True)

    def redirect(site_id, draft_id, notice=""):
        return RedirectResponse(f"/admin/sites/{site_id}/drafts/{draft_id}"+
            ("?"+urlencode({"notice": notice}) if notice else ""), status_code=303)

    @rt("/admin/sites/{site_id}/drafts", methods=["GET"])
    def get(session, request, site_id: str):
        try:
            with SessionLocal() as db:
                site = site_for(db, session, site_id)
                page = drafts.integer(request.query_params.get("page", "1"), "page", minimum=1)
                filters = dict(request.query_params)
                rows, count = drafts.list_drafts(db, site, q=filters.get("q", ""),
                    status=filters.get("status", ""), page=page,
                    sort=filters.get("sort", "updated_desc"))
                people = {row.id: row.name or row.email for row in db.scalars(select(ShopCustomer).where(
                    ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id))}
                values = {row.id: sum(line.quantity*line.unit_price_minor for line in drafts.lines_for(db, site, row))
                    -row.discount_minor+row.shipping_minor for row in rows}
                return shell("Draft orders", *ui.index(site, rows, count, page, filters, people, values, csrf(session)))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/drafts", methods=["POST"])
    async def post(session, request, site_id: str):
        try:
            check_csrf(session, await request.form())
            with SessionLocal() as db:
                site = site_for(db, session, site_id)
                draft = drafts.create(db, site)
                db.commit()
                return redirect(site.id, draft.id)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/drafts/variants", methods=["GET"])
    def get(session, request, site_id: str):
        try:
            with SessionLocal() as db:
                site = site_for(db, session, site_id)
                return JSONResponse({"items": [{"id": variant.id, "label": product.name+" · "+variant.name+" · "+variant.sku}
                    for variant, product, listing in drafts.variants(db, site, request.query_params.get("q", ""))]})
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/drafts/customers", methods=["GET"])
    def get(session, request, site_id: str):
        try:
            with SessionLocal() as db:
                site = site_for(db, session, site_id)
                term = request.query_params.get("q", "")[:100].replace("%", "\\%").replace("_", "\\_")
                rows = db.scalars(select(ShopCustomer).where(ShopCustomer.tenant_id == site.tenant_id,
                    ShopCustomer.site_id == site.id, or_(ShopCustomer.email.ilike("%"+term+"%", escape="\\"),
                    ShopCustomer.name.ilike("%"+term+"%", escape="\\"))).order_by(ShopCustomer.name).limit(40))
                return JSONResponse({"items": [{"id": row.id, "label": (row.name+" · " if row.name else "")+row.email} for row in rows]})
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/drafts/{draft_id}", methods=["GET"])
    def get(session, request, site_id: str, draft_id: str):
        try:
            with SessionLocal() as db:
                site = site_for(db, session, site_id)
                draft = drafts.owned(db, site, draft_id)
                lines = drafts.lines_for(db, site, draft)
                subtotal = sum(line.quantity*line.unit_price_minor for line in lines)
                people = list(db.scalars(select(ShopCustomer).where(ShopCustomer.tenant_id == site.tenant_id,
                    ShopCustomer.site_id == site.id).order_by(ShopCustomer.name).limit(100)))
                if draft.customer_id and all(person.id != draft.customer_id for person in people):
                    people.append(drafts.customer(db, site, draft.customer_id))
                return shell("Draft order", *ui.editor(site, draft, lines, people, csrf(session), subtotal,
                    subtotal-draft.discount_minor+draft.shipping_minor, request.query_params.get("notice", "")))
        except CommerceError as exc:
            return error(exc)

    async def mutate(session, request, site_id, draft_id, action, line_id=""):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                site = site_for(db, session, site_id)
                values = dict(form)
                notice = "Draft saved."
                if action == "save":
                    values["discount_minor"] = minor(form.get("discount"))
                    values["shipping_minor"] = minor(form.get("shipping"))
                    drafts.save(db, site, draft_id, values, version=form.get("version"))
                elif action == "line":
                    values["unit_price_minor"] = minor(form.get("unit_price"))
                    drafts.add_line(db, site, draft_id, values, version=form.get("version"))
                elif action == "remove":
                    drafts.remove_line(db, site, draft_id, line_id, version=form.get("version"))
                elif action == "invoice":
                    drafts.invoice(db, site, draft_id, version=form.get("version"))
                    notice = "Invoice queued. Payment is arranged directly with the merchant."
                elif action == "complete":
                    drafts.complete(db, site, draft_id, mark_as_paid=form.get("mark_as_paid")=="on")
                    notice = "Manual payment recorded and order created."
                db.commit()
                return redirect(site.id, draft_id, notice)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/drafts/{draft_id}", methods=["POST"])
    async def post(session, request, site_id: str, draft_id: str):
        return await mutate(session, request, site_id, draft_id, "save")

    @rt("/admin/sites/{site_id}/drafts/{draft_id}/lines", methods=["POST"])
    async def post(session, request, site_id: str, draft_id: str):
        return await mutate(session, request, site_id, draft_id, "line")

    @rt("/admin/sites/{site_id}/drafts/{draft_id}/lines/{line_id}/remove", methods=["POST"])
    async def post(session, request, site_id: str, draft_id: str, line_id: str):
        return await mutate(session, request, site_id, draft_id, "remove", line_id)

    @rt("/admin/sites/{site_id}/drafts/{draft_id}/invoice", methods=["POST"])
    async def post(session, request, site_id: str, draft_id: str):
        return await mutate(session, request, site_id, draft_id, "invoice")

    @rt("/admin/sites/{site_id}/drafts/{draft_id}/complete", methods=["POST"])
    async def post(session, request, site_id: str, draft_id: str):
        return await mutate(session, request, site_id, draft_id, "complete")

    @rt("/admin/sites/{site_id}/abandoned-checkouts", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = site_for(db, session, site_id)
                return shell("Abandoned checkouts", *ui.abandoned(site, drafts.abandoned(db, site)))
        except CommerceError as exc:
            return error(exc)
