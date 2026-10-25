"""Merchant discount management, using the shared admin shell."""
import secrets
from datetime import datetime

from fasthtml.common import (
    A,
    Button,
    Div,
    Fieldset,
    Form,
    Input,
    Label,
    Legend,
    Link,
    Option,
    P,
    Script,
    Select,
    Small,
    Span,
    Strong,
)
from sqlalchemy import select
from starlette.responses import JSONResponse, RedirectResponse

from app import content, discounts
from app.admin_ui import badge, card, icon, index_table, modal, page_header, two_column
from app.db import SessionLocal
from app.models import Collection, Discount, DiscountCode, Product
from app.services import CommerceError


def owned(db, site, discount_id):
    rule = db.scalar(select(Discount).where(Discount.id == discount_id,
        Discount.site_id == site.id, Discount.tenant_id == site.tenant_id,
        Discount.deleted_at.is_(None)))
    if not rule:
        raise CommerceError("Discount not found.")
    return rule


def save(db, site, data, rule=None):
    rule = rule or Discount(site_id=site.id, tenant_id=site.tenant_id)
    title = str(data.get("title", "")).strip()[:160]
    if not title:
        raise CommerceError("Enter a discount title.")
    rule.title = title
    choices = {"type": discounts.TYPES, "method": {"code", "automatic"},
        "value_type": {"percentage", "fixed"}, "applies_to": {"all", "products", "collections"},
        "eligibility": {"all", "emails"}, "minimum_type": {"none", "amount", "quantity"}}
    defaults = {"type": "amount_off_order", "method": "code", "value_type": "percentage",
                "applies_to": "all", "eligibility": "all", "minimum_type": "none"}
    for field, allowed in choices.items():
        value = str(data.get(field, getattr(rule, field) or defaults[field]))
        if value not in allowed:
            raise CommerceError("Choose valid discount conditions.")
        setattr(rule, field, value)
    for field in ("value", "minimum_value", "buy_quantity", "get_quantity"):
        try:
            value = int(data.get(field, 1 if field.endswith("quantity") else 0))
        except (ValueError, TypeError) as exc:
            raise CommerceError("Enter whole numbers for amounts and quantities.") from exc
        if value < (1 if field.endswith("quantity") else 0) or value > 100_000_000:
            raise CommerceError("Enter a valid amount or quantity.")
        setattr(rule, field, value)
    if rule.value_type == "percentage" and rule.value > 100:
        raise CommerceError("Percentages must be between 0 and 100.")
    try:
        rule.usage_limit = int(data["usage_limit"]) if data.get("usage_limit") else None
        if rule.usage_limit is not None and rule.usage_limit < 1:
            raise ValueError
        for field in ("starts_at", "ends_at"):
            value = str(data.get(field, ""))
            setattr(rule, field, discounts.utc(datetime.fromisoformat(value)) if value else None)
    except (ValueError, TypeError) as exc:
        raise CommerceError("Enter valid dates and a positive usage limit.") from exc
    if rule.starts_at and rule.ends_at and rule.ends_at <= rule.starts_at:
        raise CommerceError("The end date must follow the start date.")
    for field in ("once_per_customer", "combines_products", "combines_orders", "combines_shipping", "is_active"):
        setattr(rule, field, data.get(field) == "on")
    rule.emails_json = sorted({email.strip().lower() for email in str(data.get("emails", "")).split(",") if email.strip()})
    if rule.eligibility == "emails" and (not rule.emails_json or any("@" not in e for e in rule.emails_json)):
        raise CommerceError("Enter eligible customer email addresses.")
    for field, model in (("product", Product), ("collection", Collection)):
        ids = list(data.getlist(field + "_ids")) if hasattr(data, "getlist") else data.get(field + "_ids", [])
        query = select(model.id).where(model.tenant_id == site.tenant_id, model.id.in_(ids))
        if set(db.scalars(query)) != set(ids):
            raise CommerceError("Choose resources belonging to this store.")
        setattr(rule, field + "_ids_json", ids)
    if rule.applies_to != "all" and not getattr(rule, rule.applies_to[:-1] + "_ids_json", []):
        raise CommerceError("Choose at least one product or collection.")
    codes = sorted({v.strip().upper() for v in str(data.get("codes", "")).split(",") if v.strip()})
    if rule.method == "code" and not codes:
        raise CommerceError("Enter at least one discount code.")
    for code in codes:
        if len(code) > 40 or any(c.isspace() for c in code):
            raise CommerceError("Codes must contain up to 40 characters without spaces.")
        other = discounts.code_rule(db, site, code)
        if other and other.id != rule.id:
            raise CommerceError("This code is already in use by another discount.")
    db.add(rule)
    db.flush()
    for old in db.scalars(select(DiscountCode).where(DiscountCode.discount_id == rule.id)):
        db.delete(old)
    db.flush()
    for code in codes if rule.method == "code" else []:
        db.add(DiscountCode(site_id=site.id, tenant_id=site.tenant_id, discount_id=rule.id, code=code))
    db.flush()
    return rule


def register_discount_routes(rt, actor, csrf, check_csrf, shell, error):
    def editor(db, session, site, rule=None, kind="amount_off_order"):
        prefix = f"/admin/sites/{site.id}/discounts"
        def val(name, default=""):
            value = getattr(rule, name) if rule else default
            return value if value is not None else ""
        def field(label, name, default="", **attrs):
            return Label(label, Input(name=name, value=val(name, default), **attrs))
        def choice(label, name, options, default):
            return Label(label, Select(*[Option(text, value=value, selected=val(name, default) == value)
                         for value, text in options], name=name, aria_label=label))
        def radios(label, name, options, default, segmented=False):
            return Fieldset(Legend(label), Div(*[
                Label(Input(type="radio", name=name, value=value,
                            checked=val(name, default) == value), Span(text))
                for value, text in options], cls="a-segmented" if segmented else "a-radio-options"),
                data_discount_field=name)
        def resources(model_name, model, label):
            title_attr = Product.name if model is Product else Collection.title
            objects = list(db.scalars(select(model).where(
                model.tenant_id == site.tenant_id).order_by(title_attr)))
            return Div(
                Label(label, Select(*[Option(obj.name if model is Product else obj.title,
                    value=obj.id, selected=obj.id in val(model_name + "_ids_json", []))
                    for obj in objects], name=model_name + "_ids", multiple=True,
                    data_resource_fallback="")),
                Div(data_resource_chips="", aria_live="polite"),
                Button("Browse " + model_name + "s", type="button", cls="e-button secondary",
                       data_discount_resources=model_name + "s", hidden=True),
                data_discount_resource_group=model_name + "s")
        def check(label, name, default=False):
            return Label(Input(type="checkbox", name=name, checked=val(name, default)), label)
        codes = ", ".join(db.scalars(select(DiscountCode.code).where(DiscountCode.discount_id == rule.id))) if rule else ""
        form = Form(csrf(session), two_column(Div(
            card(field("Title", "title", required=True),
                 Input(type="hidden", name="type", value=val("type", kind)),
                 Div(badge(discounts.TYPES[val("type", kind)]),
                     A("Change type", href=prefix + "/new") if not rule else None,
                     cls="e-actions"),
                 radios("Method", "method", [("code", "Discount code"), ("automatic", "Automatic")], "code", True),
                 Label("Codes (comma separated)", Input(name="codes", value=codes)),
                 Button("Generate code", type="button", data_generate_discount="", cls="e-button secondary"), title="Details"),
            card(radios("Value type", "value_type", [("percentage", "Percentage"), ("fixed", "Fixed amount")], "percentage", True),
                 field("Value", "value", 10, type="number", min=0),
                 field("Customer buys quantity", "buy_quantity", 1, type="number", min=1),
                 field("Customer gets quantity", "get_quantity", 1, type="number", min=1),
                 P("For Buy X get Y, the customer adds both quantities to the bag. The cheapest qualifying units receive the discount."),
                 choice("Applies to", "applies_to", [("all", "All products"), ("products", "Specific products"), ("collections", "Specific collections")], "all"),
                 resources("product", Product, "Products"),
                 resources("collection", Collection, "Collections"), title="Value and products"),
            card(radios("Customer eligibility", "eligibility", [("all", "All customers"), ("emails", "Specific emails")], "all"),
                 Label("Eligible emails (comma separated)", Input(name="emails", value=", ".join(val("emails_json", [])))),
                 radios("Minimum requirement", "minimum_type", [("none", "No minimum"), ("amount", "Minimum purchase amount"), ("quantity", "Minimum quantity of items")], "none"),
                 field("Minimum value", "minimum_value", 0, type="number", min=0), title="Conditions"),
            card(field("Maximum total uses", "usage_limit", type="number", min=1),
                 check("Once per customer", "once_per_customer"),
                 check("Combine with product discounts", "combines_products"),
                 check("Combine with order discounts", "combines_orders"),
                 check("Combine with shipping discounts", "combines_shipping"), title="Usage and combinations"),
            card(*[Label(label, Input(type="datetime-local", name=name,
                  value=val(name).strftime("%Y-%m-%dT%H:%M") if val(name) else ""))
                  for name, label in (("starts_at", "Starts at (UTC)"), ("ends_at", "Ends at (UTC)"))],
                 check("Active", "is_active", True), title="Active dates")),
            card(Strong("Discount summary"), P("", data_discount_summary=""),
                 Small("Savings are checked against the customer's purchase at checkout."), title="Summary")),
            Button("Save discount", cls="e-button"), method="post",
            action=prefix + ("/" + rule.id if rule else "/new"), cls="e-form",
            data_discount_resources_url=prefix + "/resources",
            data_discount_form="", data_discount_kind_label=discounts.TYPES[val("type", kind)], data_dirty_guard=True)
        return shell("Edit discount" if rule else "Create discount",
            page_header(rule.title if rule else "Create discount", back_href=prefix), form,
            Form(csrf(session),
                *[Button(label, name="action", value=action, cls="e-button secondary") for label, action in
                  (("Duplicate", "duplicate"), ("Deactivate", "deactivate"), ("Delete", "delete"))],
                method="post", action=prefix + "/" + rule.id + "/action", cls="e-actions") if rule else None,
            modal("Select products or collections",
                Label("Search resources", Input(type="search", data_discount_resource_search="",
                      autocomplete="off", placeholder="Search by name")),
                P("", data_discount_resource_status="", role="status"),
                Div(data_discount_resource_results="", cls="a-resource-results"),
                Button("Done", type="button", data_modal_close="", cls="e-button"),
                id="discount-resources"),
            Script(src="/static/admin/discounts.js", defer=True),
            Link(rel="stylesheet", href="/static/admin/growth.css"))

    @rt("/admin/sites/{site_id}/discounts", methods=["GET"])
    def get(session, site_id: str, q: str = "", status: str = "All", sort: str = "recent"):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                discounts.bridge_vouchers(db, site)
                db.commit()
                rules = list(db.scalars(select(Discount).where(Discount.site_id == site.id,
                    Discount.tenant_id == site.tenant_id, Discount.deleted_at.is_(None))
                    .order_by(Discount.created_at.desc())))
                prefix = f"/admin/sites/{site.id}/discounts"
                rules = [r for r in rules if (status.casefold() == "all" or discounts.status(r) == status)
                         and (not q or q.lower() in r.title.lower() or any(q.lower() in c.lower()
                         for c in db.scalars(select(DiscountCode.code).where(DiscountCode.discount_id == r.id))))]
                if sort == "title":
                    rules.sort(key=lambda rule: rule.title.casefold())
                return shell("Discounts", page_header("Discounts", primary_action=A("Create discount", href=prefix + "/new", cls="e-button")),
                    Form(Label("Search discounts", Input(name="q", value=q, type="search")),
                         Label("Status", Select(*[Option(label, value=value,
                             selected=status.casefold() == value.casefold()) for value, label in
                             (("all", "All statuses"), ("Active", "Active"), ("Scheduled", "Scheduled"),
                              ("Expired", "Expired"), ("Inactive", "Inactive"))], name="status", aria_label="Status")),
                         Label("Sort", Select(Option("Newest first", value="recent", selected=sort != "title"),
                             Option("Title A to Z", value="title", selected=sort == "title"), name="sort", aria_label="Sort")),
                         Button("Search", cls="e-button secondary"), method="get", cls="e-actions"),
                    Form(csrf(session), index_table(["Discount", "Status", "Method", "Type", "Used", "Select"], [
                        [A(r.title, href=prefix + "/" + r.id), badge(discounts.status(r), "success" if discounts.status(r) == "Active" else "neutral"),
                         r.method.title(), discounts.TYPES[r.type], str(discounts.usage(db, r)),
                         Input(type="checkbox", name="ids", value=r.id, aria_label="Select " + r.title)] for r in rules]),
                         Div(Span("", data_selected_count=""),
                             Select(Option("Activate", value="activate"), Option("Deactivate", value="deactivate"),
                                    name="action", aria_label="Bulk action"),
                             Button("Apply to selected", cls="e-button secondary"),
                             data_bulk_bar="", hidden=True, cls="e-actions"),
                         method="post", action=prefix + "/bulk", cls="e-form"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/discounts/new", methods=["GET"])
    def get(session, site_id: str, type: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                if type not in discounts.TYPES:
                    descriptions = {
                        "amount_off_products": "Discount specific products or collections with a percentage or fixed amount.",
                        "amount_off_order": "Take a percentage or fixed amount off the entire order.",
                        "buy_x_get_y": "Reward customers with discounted items when they buy qualifying products.",
                        "free_shipping": "Offer free shipping on eligible orders.",
                    }
                    return shell("Create discount",
                        page_header("Create discount", back_href=f"/admin/sites/{site.id}/discounts"),
                        card(P("Choose the type of discount you want to offer."),
                            Div(*[A(icon("Discounts"), Div(Strong(label), P(descriptions[kind])),
                                Span(">", aria_hidden="true"), href=f"?type={kind}",
                                aria_label=label, cls="a-discount-type")
                                for kind, label in discounts.TYPES.items()], cls="a-discount-types"),
                            title="Select a discount type"),
                        Link(rel="stylesheet", href="/static/admin/growth.css"))
                return editor(db, session, site, kind=type)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/discounts/resources", methods=["GET"])
    def resources(session, site_id: str, kind: str = "products", q: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                if kind not in {"products", "collections"}:
                    raise CommerceError("Choose products or collections.")
                model, title = (Product, Product.name) if kind == "products" else (Collection, Collection.title)
                query = select(model).where(model.tenant_id == site.tenant_id)
                needle = q.strip()[:160]
                if needle:
                    query = query.where(title.icontains(needle, autoescape=True))
                rows = db.scalars(query.order_by(title).limit(100))
                return JSONResponse({"results": [{"id": row.id,
                    "title": row.name if kind == "products" else row.title} for row in rows]})
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/discounts/{discount_id}", methods=["GET"])
    def get(session, site_id: str, discount_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                rule = owned(db, site, discount_id)
                return editor(db, session, site, rule)
        except CommerceError as exc:
            return error(exc)

    async def persist(session, request, site_id, discount_id=None):
        try:
            data = await request.form()
            check_csrf(session, data)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                from app.checkout_services import lock_site
                lock_site(db, site)
                rule = save(db, site, data, owned(db, site, discount_id) if discount_id else None)
                db.commit()
                return RedirectResponse(f"/admin/sites/{site.id}/discounts/{rule.id}", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/discounts/new", methods=["POST"])
    async def post(session, request, site_id: str):
        return await persist(session, request, site_id)

    @rt("/admin/sites/{site_id}/discounts/bulk", methods=["POST"])
    async def post(session, request, site_id: str):
        return await action(session, request, site_id)

    @rt("/admin/sites/{site_id}/discounts/{discount_id}", methods=["POST"])
    async def post(session, request, site_id: str, discount_id: str):
        return await persist(session, request, site_id, discount_id)

    async def action(session, request, site_id, discount_id=None):
        try:
            data = await request.form()
            check_csrf(session, data)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                from app.checkout_services import lock_site
                lock_site(db, site)
                rules = [owned(db, site, i) for i in ([discount_id] if discount_id else data.getlist("ids"))]
                command = data.get("action")
                if command not in {"activate", "deactivate", "delete", "duplicate"}:
                    raise CommerceError("Choose a valid action.")
                for rule in rules:
                    if command == "delete":
                        discounts.delete(db, site, rule)
                    elif command == "duplicate":
                        clone = Discount(**{c.name: getattr(rule, c.name) for c in Discount.__table__.columns
                            if c.name not in {"id", "created_at", "updated_at", "voucher_id"}})
                        clone.title += " (copy)"
                        clone.is_active = False
                        db.add(clone)
                        db.flush()
                        if clone.method == "code":
                            db.add(DiscountCode(site_id=site.id, tenant_id=site.tenant_id,
                                discount_id=clone.id, code=secrets.token_hex(6).upper()))
                    else:
                        rule.is_active = command == "activate"
                db.commit()
                return RedirectResponse(f"/admin/sites/{site.id}/discounts", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/discounts/{discount_id}/action", methods=["POST"])
    async def post(session, request, site_id: str, discount_id: str):
        return await action(session, request, site_id, discount_id)
