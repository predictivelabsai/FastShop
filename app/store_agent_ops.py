"""Tenant-scoped store tools and confirmed merchant actions for the agent."""
from __future__ import annotations

import secrets
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import or_, select, update

from app.models import (
    AgentAction,
    AgentMessage,
    Channel,
    Discount,
    Membership,
    Order,
    Product,
    ProductVariant,
    ShippingRate,
    ShippingZone,
    ShopCustomer,
    SiteCommerceSettings,
    SiteOrder,
    Stock,
    SubscriptionContract,
    TaxRate,
    VariantChannelListing,
    Warehouse,
)
from app.services import CommerceError, money


def _bounded(value, default=20, maximum=50):
    if isinstance(value, bool):
        raise CommerceError("Limit must be a whole number.")
    return max(1, min(maximum, int(value if value is not None else default)))


def _iso(value):
    return value.isoformat() if value else None


def register_tools(db, site, user_id, conversation_id):
    from app.store_agent import ToolSpec, _schema

    def read(name, description, props, required, fn):
        return ToolSpec(name, description, _schema(props, required), fn, "read", name.replace("_", " ").title())

    def write(name, description, props, required, kind, validate):
        def propose(args):
            payload, summary, version = validate(args)
            action = AgentAction(tenant_id=site.tenant_id, site_id=site.id,
                conversation_id=conversation_id, user_id=user_id, kind=kind,
                payload_json=payload | {"expected_version": version}, summary=summary,
                idempotency_key=secrets.token_urlsafe(24), status="pending")
            db.add(action)
            db.flush()
            return {"pending_action": action.id, "kind": kind, "summary": summary}
        return ToolSpec(name, description + ". This only proposes the change; merchant confirmation is required.",
                        _schema(props, required), propose, "store_write", name.replace("_", " ").title())

    def product(pid):
        row = db.scalar(select(Product).where(Product.id == pid, Product.tenant_id == site.tenant_id))
        if not row:
            raise CommerceError("Product not found.")
        return row

    def customer(cid):
        row = db.scalar(select(ShopCustomer).where(ShopCustomer.id == cid,
            ShopCustomer.tenant_id == site.tenant_id, ShopCustomer.site_id == site.id))
        if not row:
            raise CommerceError("Customer not found.")
        return row

    def order(oid):
        row = db.execute(select(SiteOrder, Order).join(Order, Order.id == SiteOrder.order_id).where(
            SiteOrder.id == oid, SiteOrder.tenant_id == site.tenant_id, SiteOrder.site_id == site.id,
            Order.tenant_id == site.tenant_id)).first()
        if not row:
            raise CommerceError("Order not found.")
        return row

    def list_products(a):
        limit = _bounded(a.get("limit"))
        q = str(a.get("query", "")).strip()[:160]
        stmt = select(Product).where(Product.tenant_id == site.tenant_id)
        if q:
            stmt = stmt.where(or_(Product.name.ilike(f"%{q}%"), Product.slug.ilike(f"%{q}%")))
        rows = db.scalars(stmt.order_by(Product.name, Product.id).limit(limit)).all()
        return {"products": [{"id": p.id, "name": p.name, "status": p.status,
            "description": p.description_html[:2000], "version": p.version} for p in rows]}

    def get_product(a):
        p = product(a["product_id"])
        variants = []
        for v in p.variants:
            listing = db.scalar(select(VariantChannelListing).where(
                VariantChannelListing.variant_id == v.id, VariantChannelListing.channel_id == site.channel_id))
            quantity = sum(db.scalars(select(Stock.quantity).join(Warehouse).where(
                Stock.variant_id == v.id, Warehouse.tenant_id == site.tenant_id,
                Warehouse.fulfils_online_orders.is_(True))))
            variants.append({"id": v.id, "name": v.name, "sku": v.sku,
                "price_minor": listing.price_minor if listing else None,
                "currency": listing.currency if listing else None, "stock": quantity})
        return {"id": p.id, "name": p.name, "description": p.description_html[:4000],
                "status": p.status, "version": p.version, "variants": variants}

    def list_customers(a):
        limit = _bounded(a.get("limit"))
        q = str(a.get("query", "")).strip()[:160]
        stmt = select(ShopCustomer).where(ShopCustomer.tenant_id == site.tenant_id,
            ShopCustomer.site_id == site.id)
        if q:
            stmt = stmt.where(or_(ShopCustomer.name.ilike(f"%{q}%"), ShopCustomer.email.ilike(f"%{q}%")))
        rows = db.scalars(stmt.order_by(ShopCustomer.created_at.desc()).limit(limit)).all()
        return {"customers": [{"id": c.id, "name": c.name, "email": c.email,
            "phone": c.phone, "tags": c.tags_json, "note": c.note[:1000]} for c in rows]}

    def get_customer(a):
        c = customer(a["customer_id"])
        return {"id": c.id, "name": c.name, "email": c.email, "phone": c.phone,
                "tags": c.tags_json, "note": c.note[:3000], "created_at": _iso(c.created_at)}

    def list_orders(a):
        stmt = select(SiteOrder, Order).join(Order, Order.id == SiteOrder.order_id).where(
            SiteOrder.tenant_id == site.tenant_id, SiteOrder.site_id == site.id,
            Order.tenant_id == site.tenant_id)
        if a.get("status"):
            stmt = stmt.where(Order.status == str(a["status"])[:30])
        if a.get("query"):
            q = str(a["query"]).strip()[:100]
            stmt = stmt.where(or_(Order.number.ilike(f"%{q}%"), Order.email.ilike(f"%{q}%")))
        if a.get("date_from"):
            stmt = stmt.where(Order.created_at >= datetime.fromisoformat(a["date_from"]))
        if a.get("date_to"):
            stmt = stmt.where(Order.created_at <= datetime.fromisoformat(a["date_to"]))
        rows = db.execute(stmt.order_by(Order.created_at.desc()).limit(_bounded(a.get("limit")))).all()
        return {"orders": [{"id": link.id, "number": o.number, "status": o.status,
            "payment_status": o.payment_status, "total_minor": o.total_minor,
            "currency": o.currency, "email": o.email, "created_at": _iso(o.created_at)}
            for link, o in rows]}

    def get_order(a):
        link, o = order(a["order_id"])
        return {"id": link.id, "number": o.number, "status": o.status,
            "payment_status": o.payment_status, "total_minor": o.total_minor,
            "currency": o.currency, "email": o.email,
            "shipping_address": {k: str(v)[:240] for k, v in (o.shipping_address_json or {}).items()
                                 if k in {"name", "city", "state", "postal_code", "country"}},
            "lines": [{"product": line.product_name, "variant": line.variant_name,
                       "quantity": line.quantity, "price_minor": line.unit_price_minor}
                      for line in o.lines]}

    def list_subscriptions(a):
        stmt = select(SubscriptionContract).where(SubscriptionContract.tenant_id == site.tenant_id,
            SubscriptionContract.site_id == site.id)
        if a.get("state"):
            stmt = stmt.where(SubscriptionContract.state == str(a["state"])[:24])
        rows = db.scalars(stmt.order_by(SubscriptionContract.created_at.desc()).limit(
            _bounded(a.get("limit")))).all()
        return {"subscriptions": [{"id": c.id, "state": c.state,
            "next_due_at": _iso(c.next_due_at), "version": c.version,
            "lines": [{"product_name": line.get("product_name", ""), "quantity": line.get("quantity", 0)}
                      for line in c.lines_json]} for c in rows]}

    def list_discounts(_a):
        rows = db.scalars(select(Discount).where(Discount.tenant_id == site.tenant_id,
            Discount.site_id == site.id, Discount.deleted_at.is_(None))
            .order_by(Discount.created_at.desc()).limit(50)).all()
        return {"discounts": [{"id": d.id, "title": d.title, "type": d.type,
            "value_type": d.value_type, "value": d.value, "active": d.is_active,
            "version": _iso(d.updated_at)} for d in rows]}

    def store_settings(_a):
        settings = db.scalar(select(SiteCommerceSettings).where(
            SiteCommerceSettings.site_id == site.id, SiteCommerceSettings.tenant_id == site.tenant_id))
        zones = db.scalars(select(ShippingZone).where(ShippingZone.tenant_id == site.tenant_id,
            ShippingZone.site_id == site.id).order_by(ShippingZone.name).limit(30)).all()
        shipping = []
        for zone in zones:
            rates = db.scalars(select(ShippingRate).where(ShippingRate.tenant_id == site.tenant_id,
                ShippingRate.site_id == site.id, ShippingRate.zone_id == zone.id)
                .order_by(ShippingRate.name).limit(30)).all()
            shipping.append({"name": zone.name, "countries": zone.countries_json,
                "active": zone.is_active, "rates": [{"name": r.name,
                    "amount_minor": r.amount_minor, "kind": r.kind} for r in rates]})
        taxes = db.scalars(select(TaxRate).where(TaxRate.tenant_id == site.tenant_id,
            TaxRate.site_id == site.id).order_by(TaxRate.country, TaxRate.region).limit(50)).all()
        return {"shipping_zones": shipping,
            "tax_rates": [{"country": t.country, "region": t.region,
                "name": t.name, "rate_bps": t.rate_bps,
                "prices_include_tax": t.prices_include_tax} for t in taxes],
            "payments": {"connected": bool(settings and settings.mode),
                         "mode": settings.mode if settings else "unconfigured"},
            "policies": {k: str(v)[:2000] for k, v in (site.settings_json or {}).items()
                         if k in {"shipping_policy", "returns_policy", "privacy_policy", "terms_policy"}}}

    def sales_summary(a):
        from app.order_management import revenue_report
        period = str(a.get("period", "30d"))
        if period not in {"7d", "30d", "90d"}:
            raise CommerceError("Choose 7d, 30d or 90d.")
        days = int(period[:-1])
        result = revenue_report(db, site, date.today() - timedelta(days=days - 1), date.today())
        return {"period": period, "orders": result.order_count, "gross_minor": result.gross_sales_minor,
            "refunds_minor": result.refunds_minor, "net_revenue_minor": result.net_revenue_minor,
            "currency": result.currency}

    tools = [
        read("list_products", "Search store products", {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, [], list_products),
        read("get_product", "Read product variants, prices and stock", {"product_id": {"type": "string"}}, ["product_id"], get_product),
        read("list_customers", "Search customers", {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, [], list_customers),
        read("get_customer", "Read customer profile without payment details", {"customer_id": {"type": "string"}}, ["customer_id"], get_customer),
        read("list_orders", "List recent store orders", {"status": {"type": "string"}, "query": {"type": "string"}, "date_from": {"type": "string"}, "date_to": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, [], list_orders),
        read("get_order", "Read an order with safe shipping summary", {"order_id": {"type": "string"}}, ["order_id"], get_order),
        read("list_subscriptions", "List subscriptions", {"state": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, [], list_subscriptions),
        read("list_discounts", "List store discounts", {}, [], list_discounts),
        read("get_store_settings", "Read shipping, tax, policies and payment connection status without secrets", {}, [], store_settings),
        read("sales_summary", "Read sales summary for a bounded period", {"period": {"type": "string", "enum": ["7d", "30d", "90d"]}}, ["period"], sales_summary),
    ]

    def validate_update_product(a):
        p = product(a["product_id"])
        fields = {k: a[k] for k in ("name", "description", "status") if k in a}
        if not fields or ("name" in fields and (not fields["name"].strip() or len(fields["name"]) > 220)):
            raise CommerceError("Provide a valid product name, description or status.")
        if fields.get("status") not in (None, "active", "draft", "archived"):
            raise CommerceError("Choose active, draft or archived status.")
        return {"product_id": p.id, "fields": fields}, f"Update {p.name}", p.version

    def validate_price(a):
        variant = db.scalar(select(ProductVariant).where(
            ProductVariant.id == a["variant_id"], ProductVariant.tenant_id == site.tenant_id))
        if not variant:
            raise CommerceError("Variant not found.")
        p = product(variant.product_id)
        amount = a["price_minor"]
        if type(amount) is not int or not 0 <= amount <= 1_000_000_000:
            raise CommerceError("Price must be a non-negative amount in minor currency units.")
        listing = db.scalar(select(VariantChannelListing).where(
            VariantChannelListing.variant_id == variant.id,
            VariantChannelListing.channel_id == site.channel_id))
        if not listing:
            raise CommerceError("Variant has no price in this site's currency.")
        currency = db.get(Channel, site.channel_id).currency
        summary = f"Change price of {p.name} from {money(listing.price_minor, currency)} to {money(amount, currency)}"
        return {"product_id": p.id, "variant_id": variant.id, "price_minor": amount,
                "currency": currency}, summary, p.version

    tools.append(write("update_product", "Update product name, description or status",
        {"product_id": {"type": "string"}, "name": {"type": "string"},
         "description": {"type": "string"}, "status": {"type": "string", "enum": ["active", "draft", "archived"]}},
        ["product_id"], "update_product", validate_update_product))
    tools.append(write("set_variant_price", "Set a variant price in integer minor currency units",
        {"variant_id": {"type": "string"}, "price_minor": {"type": "integer", "minimum": 0}},
        ["variant_id", "price_minor"], "set_variant_price", validate_price))
    return {t.name: t for t in tools}


def _owned_action(db, site, action_id):
    action = db.scalar(select(AgentAction).where(AgentAction.id == action_id,
        AgentAction.site_id == site.id, AgentAction.tenant_id == site.tenant_id))
    if not action:
        raise CommerceError("Pending action not found.")
    return action


def confirm_action(db, site, user, action_id):
    user_id = user.id if hasattr(user, "id") else str(user)
    _member(db, site, user_id)
    action = _owned_action(db, site, action_id)
    now = datetime.now(UTC)
    if action.status != "pending":
        return action
    if action.created_at.replace(tzinfo=UTC) + timedelta(hours=24) <= now:
        action.status = "expired"
        db.commit()
        return action
    claimed = db.execute(update(AgentAction).where(AgentAction.id == action.id,
        AgentAction.status == "pending").values(status="confirmed", decided_at=now))
    if claimed.rowcount != 1:
        db.refresh(action)
        return action
    try:
        if action.kind == "update_product":
            from app import catalog_admin
            p = catalog_admin.get_product(db, site, action.payload_json["product_id"])
            if p.version != action.payload_json["expected_version"]:
                raise CommerceError("This product changed after the action was proposed.")
            p = catalog_admin.update_product(db, site, p.id, action.payload_json["fields"],
                expected_version=p.version, user_id=user_id)
            action.result_json = {"product_id": p.id, "version": p.version}
        elif action.kind == "set_variant_price":
            from app import catalog_admin
            payload = action.payload_json
            p = catalog_admin.get_product(db, site, payload["product_id"])
            if p.version != payload["expected_version"]:
                raise CommerceError("This product changed after the action was proposed.")
            variant = db.scalar(select(ProductVariant).where(ProductVariant.id == payload["variant_id"],
                ProductVariant.product_id == p.id, ProductVariant.tenant_id == site.tenant_id))
            listing = db.scalar(select(VariantChannelListing).where(
                VariantChannelListing.variant_id == payload["variant_id"],
                VariantChannelListing.channel_id == site.channel_id))
            channel = db.get(Channel, site.channel_id)
            if not variant or not listing or channel.currency != payload["currency"]:
                raise CommerceError("The variant price or site currency changed after the action was proposed.")
            listing.price_minor = payload["price_minor"]
            catalog_admin.update_product(db, site, p.id, {}, expected_version=p.version, user_id=user_id)
            action.result_json = {"variant_id": variant.id, "price_minor": listing.price_minor,
                                  "currency": listing.currency}
        else:
            raise CommerceError("This action type cannot be executed yet.")
        action.status = "confirmed"
        note = "Done: " + action.summary
    except Exception as exc:
        action.status = "failed"
        action.result_json = {"error": str(exc)[:300]}
        note = "Could not complete: " + action.summary
    action.decided_at = datetime.now(UTC)
    db.add(AgentMessage(tenant_id=site.tenant_id, site_id=site.id,
        conversation_id=action.conversation_id, role="assistant", content=note))
    db.commit()
    return action


def decline_action(db, site, user, action_id):
    user_id = user.id if hasattr(user, "id") else str(user)
    _member(db, site, user_id)
    action = _owned_action(db, site, action_id)
    if action.status == "pending":
        action.status = "declined"
        action.decided_at = datetime.now(UTC)
        db.add(AgentMessage(tenant_id=site.tenant_id, site_id=site.id,
            conversation_id=action.conversation_id, role="assistant", content="Declined: " + action.summary))
        db.commit()
    return action


def _member(db, site, user_id):
    membership = db.scalar(select(Membership).where(Membership.tenant_id == site.tenant_id,
        Membership.user_id == user_id, Membership.role.in_(["admin", "merchant"])))
    if not membership:
        raise CommerceError("Merchant or admin membership required.")


def register_routes(rt, actor, csrf, check_csrf, shell, error):
    from starlette.responses import JSONResponse

    from app.db import SessionLocal
    from app.models import Site

    @rt("/admin/sites/{site_id}/agent/actions", methods=["GET"])
    def list_actions(site_id: str, request):
        try:
            user_id = actor(request.session)
            with SessionLocal() as db:
                site = db.scalar(select(Site).where(Site.id == site_id))
                if not site:
                    raise CommerceError("Site not found.")
                _member(db, site, user_id)
                status = request.query_params.get("status", "pending")
                rows = db.scalars(select(AgentAction).where(AgentAction.tenant_id == site.tenant_id,
                    AgentAction.site_id == site.id, AgentAction.status == status)
                    .order_by(AgentAction.created_at.desc()).limit(50)).all()
                return JSONResponse([{"id": r.id, "kind": r.kind, "summary": r.summary,
                    "status": r.status, "created_at": _iso(r.created_at)} for r in rows])
        except Exception as exc:
            return error(exc)

    for decision, handler in (("confirm", confirm_action), ("decline", decline_action)):
        async def endpoint(site_id: str, action_id: str, request, _handler=handler):
            try:
                form = await request.form()
                check_csrf(request.session, form)
                user_id = actor(request.session)
                with SessionLocal() as db:
                    site = db.scalar(select(Site).where(Site.id == site_id))
                    if not site:
                        raise CommerceError("Site not found.")
                    action = _handler(db, site, user_id, action_id)
                    return JSONResponse({"status": action.status, "summary": action.summary,
                                         "result": action.result_json})
            except Exception as exc:
                return error(exc)
        rt(f"/admin/sites/{{site_id}}/agent/actions/{{action_id}}/{decision}", methods=["POST"])(endpoint)
