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
    DiscountCode,
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


class _AgentForm(dict):
    def getlist(self, key):
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]


def _discount_form_fields(fields, codes):
    result = dict(fields)
    for key in ("once_per_customer", "combines_products", "combines_orders",
                "combines_shipping", "is_active"):
        result[key] = "on" if result.get(key) else ""
    result["product_ids"] = result.pop("product_ids_json", [])
    result["collection_ids"] = result.pop("collection_ids_json", [])
    result["emails"] = ", ".join(result.pop("emails_json", []))
    result["codes"] = codes
    return result


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

    def validate_create_product(a):
        name = a.get("name")
        description = a.get("description", "")
        variants = a.get("variants")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 220:
            raise CommerceError("Enter a product name under 221 characters.")
        if not isinstance(description, str) or len(description) > 100_000:
            raise CommerceError("Enter a valid product description.")
        if not isinstance(variants, list) or not variants or len(variants) > 100:
            raise CommerceError("Provide between 1 and 100 variants.")
        clean_variants = []
        names = []
        for value in variants:
            if not isinstance(value, dict):
                raise CommerceError("Each variant must include a name and price.")
            variant_name = str(value.get("name", "")).strip()
            amount = value.get("price_minor")
            if not variant_name or len(variant_name) > 150:
                raise CommerceError("Enter a valid variant name.")
            if type(amount) is not int or not 0 <= amount <= 1_000_000_000:
                raise CommerceError("Variant prices must be non-negative minor units.")
            if variant_name in names:
                raise CommerceError("Variant names must be unique.")
            names.append(variant_name)
            clean_variants.append({"name": variant_name, "price_minor": amount})
        requested_status = a.get("status", "draft")
        if requested_status not in {"draft", "active"}:
            raise CommerceError("Choose draft or active status.")
        if requested_status == "active" and a.get("merchant_asked_to_publish") is not True:
            requested_status = "draft"
        return {"name": name.strip(), "description": description, "variants": clean_variants,
                "status": requested_status}, f"Create product {name.strip()}", None

    def validate_inventory(a):
        variant = db.scalar(select(ProductVariant).where(ProductVariant.id == a["variant_id"],
            ProductVariant.tenant_id == site.tenant_id))
        if not variant:
            raise CommerceError("Variant not found.")
        p = product(variant.product_id)
        warehouse_id = a.get("warehouse_id")
        warehouse = db.scalar(select(Warehouse).where(Warehouse.id == warehouse_id,
            Warehouse.tenant_id == site.tenant_id, Warehouse.is_active.is_(True)))
        if not warehouse:
            raise CommerceError("Active inventory location not found.")
        has_delta, has_set = "delta" in a, "quantity" in a
        if has_delta == has_set:
            raise CommerceError("Provide either a stock delta or a quantity to set.")
        mode, amount = ("adjust", a["delta"]) if has_delta else ("set", a["quantity"])
        if type(amount) is not int or (mode == "set" and amount < 0):
            raise CommerceError("Inventory must be a whole number and cannot be negative.")
        reason = a.get("reason")
        if reason not in {"correction", "received", "damaged", "returned", "sale"}:
            raise CommerceError("Choose a valid inventory adjustment reason.")
        return {"variant_id": variant.id, "product_id": p.id, "warehouse_id": warehouse.id,
                "mode": mode, "amount": amount, "reason": reason,
                "expected_version": p.version}, f"Adjust inventory for {p.name}", p.version

    def discount_payload(a, *, update_existing=False):
        from app import discount_routes

        rule = None
        if update_existing:
            rule_id = a.get("discount_id")
            rule = db.scalar(select(Discount).where(Discount.id == rule_id,
                Discount.site_id == site.id, Discount.tenant_id == site.tenant_id,
                Discount.deleted_at.is_(None)))
            if not rule:
                raise CommerceError("Discount not found.")
        old_updated = _iso(rule.updated_at) if rule else None
        form_data = dict(a)
        if rule:
            for key in ("once_per_customer", "combines_products", "combines_orders",
                        "combines_shipping", "is_active"):
                form_data.setdefault(key, "on" if getattr(rule, key) else "")
            form_data.setdefault("emails", ", ".join(rule.emails_json))
            form_data.setdefault("product_ids", list(rule.product_ids_json))
            form_data.setdefault("collection_ids", list(rule.collection_ids_json))
            form_data.setdefault("starts_at", _iso(rule.starts_at) or "")
            form_data.setdefault("ends_at", _iso(rule.ends_at) or "")
            form_data.setdefault("codes", ", ".join(db.scalars(select(DiscountCode.code).where(
                DiscountCode.discount_id == rule.id, DiscountCode.site_id == site.id,
                DiscountCode.tenant_id == site.tenant_id))))
        else:
            form_data.setdefault("is_active", "on")
        nested = db.begin_nested()
        try:
            saved = discount_routes.save(db, site, _AgentForm(form_data), rule)
            fields = {column.name: getattr(saved, column.name) for column in Discount.__table__.columns
                      if column.name not in {"id", "created_at", "updated_at", "deleted_at"}}
            for key in ("starts_at", "ends_at"):
                fields[key] = _iso(fields[key]) or ""
            codes = list(db.scalars(select(DiscountCode.code).where(
                DiscountCode.discount_id == saved.id, DiscountCode.site_id == site.id,
                DiscountCode.tenant_id == site.tenant_id)))
            nested.rollback()
            if rule:
                db.refresh(rule)
        except Exception:
            nested.rollback()
            if rule:
                db.refresh(rule)
            raise
        return {"discount_id": rule.id if rule else None, "fields": fields, "codes": codes,
                "expected_updated_at": old_updated}

    def validate_create_discount(a):
        payload = discount_payload(a)
        return payload, f"Create discount {payload['fields']['title']}", None

    def validate_update_discount(a):
        payload = discount_payload(a, update_existing=True)
        return payload, f"Update discount {payload['fields']['title']}", payload["expected_updated_at"]

    def validate_deactivate_discount(a):
        rule = db.scalar(select(Discount).where(Discount.id == a["discount_id"],
            Discount.site_id == site.id, Discount.tenant_id == site.tenant_id,
            Discount.deleted_at.is_(None)))
        if not rule:
            raise CommerceError("Discount not found.")
        return {"discount_id": rule.id, "expected_updated_at": _iso(rule.updated_at)}, \
            f"Deactivate discount {rule.title}", _iso(rule.updated_at)

    def validate_shipping(a):
        shipping = a.get("flat_rate_minor")
        threshold = a.get("free_shipping_threshold_minor")
        if shipping is not None and (type(shipping) is not int or not 0 <= shipping <= 1_000_000):
            raise CommerceError("Flat shipping must be between 0 and 1,000,000 minor units.")
        if type(threshold) is not int or not 0 <= threshold <= 1_000_000:
            raise CommerceError("Free shipping threshold must be between 0 and 1,000,000 minor units.")
        config = db.scalar(select(SiteCommerceSettings).where(
            SiteCommerceSettings.site_id == site.id, SiteCommerceSettings.tenant_id == site.tenant_id))
        return {"flat_rate_minor": shipping, "free_shipping_threshold_minor": threshold,
                "settings_version": config.version if config else 1, "site_version": site.version}, \
            "Update shipping settings", (config.version if config else 1, site.version)

    def validate_fulfil(a):
        link, row = order(a["order_id"])
        if not isinstance(a["carrier"], str) or not isinstance(a["tracking_number"], str):
            raise CommerceError("Carrier and tracking number must be text.")
        carrier, tracking = a["carrier"].strip(), a["tracking_number"].strip()
        if not carrier or len(carrier) > 100 or not tracking or len(tracking) > 100:
            raise CommerceError("Carrier and tracking number are required and must be at most 100 characters.")
        if row.status not in {"unfulfilled", "partially_fulfilled"}:
            raise CommerceError("Choose an open order to fulfil.")
        quantities = {line.id: line.quantity - line.fulfilled_quantity - line.unfulfilled_refunded_quantity
                      for line in row.lines if line.quantity > line.fulfilled_quantity + line.unfulfilled_refunded_quantity}
        if not quantities:
            raise CommerceError("This order has no remaining items to fulfil.")
        notify = a.get("notify_customer", True)
        if type(notify) is not bool:
            raise CommerceError("notify_customer must be true or false.")
        return {"order_id": link.id, "order_version": _iso(row.updated_at), "quantities": quantities,
                "carrier": carrier, "tracking_number": tracking, "notify_customer": notify}, \
            f"Fulfil order {row.number} with {carrier} tracking {tracking}", _iso(row.updated_at)

    def validate_refund(a):
        link, row = order(a["order_id"])
        amount, reason = a["amount_minor"], a["reason"]
        if not isinstance(reason, str):
            raise CommerceError("Provide a valid refund reason.")
        reason = reason.strip()
        if type(amount) is not int or amount <= 0 or amount > 1_000_000_000:
            raise CommerceError("Refund must be a positive amount in minor currency units.")
        if not 10 <= len(reason) <= 500:
            raise CommerceError("Provide a refund reason between 10 and 500 characters.")
        from app.order_management import refund_totals
        captured, _refunded, available = refund_totals(db, site, link, row)
        if captured <= 0 or row.payment_status not in {"paid", "partially_refunded"}:
            raise CommerceError("This order has no refundable captured payment.")
        if amount > available:
            raise CommerceError(f"Refund exceeds the refundable remainder of {money(available, row.currency)}.")
        return {"order_id": link.id, "order_version": _iso(row.updated_at), "amount_minor": amount,
                "currency": row.currency, "reason": reason}, \
            f"Refund {money(amount, row.currency)} {row.currency} for order {row.number}", _iso(row.updated_at)

    def validate_subscription(a, operation):
        contract = db.scalar(select(SubscriptionContract).where(
            SubscriptionContract.id == a["contract_id"], SubscriptionContract.site_id == site.id,
            SubscriptionContract.tenant_id == site.tenant_id))
        if not contract:
            raise CommerceError("Subscription not found.")
        if (operation == "cancel" and contract.state == "cancelled"
                or operation == "pause" and contract.state != "active"
                or operation == "resume" and contract.state != "paused"):
            raise CommerceError("This subscription cannot be changed with that action in its current state.")
        return {"contract_id": contract.id, "version": contract.version}, \
            f"{operation.title()} subscription for {contract.recipient_name or 'customer'}", contract.version

    def validate_customer_update(a):
        row = customer(a["customer_id"])
        tags, note = a["tags"], a["note"]
        if not isinstance(tags, list) or len(tags) > 50 or any(not isinstance(t, str) or not t.strip() or len(t) > 60 for t in tags):
            raise CommerceError("Provide up to 50 valid customer tags.")
        if len(set(t.strip() for t in tags)) != len(tags) or not isinstance(note, str) or len(note) > 5000:
            raise CommerceError("Customer tags must be unique and the note at most 5000 characters.")
        return {"customer_id": row.id, "email": row.email, "tags": [t.strip() for t in tags],
                "note": note, "expected_updated_at": _iso(row.updated_at)}, \
            f"Update customer {row.name or row.email}", _iso(row.updated_at)

    def validate_customer_email(a):
        row = customer(a["customer_id"])
        if not isinstance(a["subject"], str) or not isinstance(a["body"], str):
            raise CommerceError("Email subject and body must be text.")
        subject, body = a["subject"].strip(), a["body"]
        if not subject or len(subject) > 200 or not body.strip() or len(body) > 20_000:
            raise CommerceError("Provide a subject up to 200 characters and a message up to 20000 characters.")
        from app.models import MarketingConsent
        consent = db.scalar(select(MarketingConsent).where(MarketingConsent.site_id == site.id,
            MarketingConsent.tenant_id == site.tenant_id, MarketingConsent.customer_id == row.id))
        marketing = a.get("marketing", False)
        if type(marketing) is not bool:
            raise CommerceError("marketing must be true or false.")
        if marketing and (not row.email_marketing_consent or not consent or consent.status != "subscribed"):
            raise CommerceError("Marketing email requires current customer consent.")
        return {"customer_id": row.id, "subject": subject, "body": body, "marketing": marketing,
                "expected_updated_at": _iso(row.updated_at)}, f"Email {row.email}: {subject}", _iso(row.updated_at)

    tools.append(write("update_product", "Update product name, description or status",
        {"product_id": {"type": "string"}, "name": {"type": "string"},
         "description": {"type": "string"}, "status": {"type": "string", "enum": ["active", "draft", "archived"]}},
        ["product_id"], "update_product", validate_update_product))
    tools.append(write("set_variant_price", "Set a variant price in integer minor currency units",
        {"variant_id": {"type": "string"}, "price_minor": {"type": "integer", "minimum": 0}},
        ["variant_id", "price_minor"], "set_variant_price", validate_price))
    tools.append(write("create_product", "Create a draft product with named variants and minor-unit prices",
        {"name": {"type": "string"}, "description": {"type": "string"},
         "variants": {"type": "array", "items": {"type": "object", "properties": {
             "name": {"type": "string"}, "price_minor": {"type": "integer", "minimum": 0}},
             "required": ["name", "price_minor"]}}, "status": {"type": "string", "enum": ["draft", "active"]},
         "merchant_asked_to_publish": {"type": "boolean"}}, ["name", "variants"],
        "create_product", validate_create_product))
    tools.append(write("adjust_inventory", "Adjust or set variant stock at an active location",
        {"variant_id": {"type": "string"}, "warehouse_id": {"type": "string"},
         "delta": {"type": "integer"}, "quantity": {"type": "integer", "minimum": 0},
         "reason": {"type": "string", "enum": ["correction", "received", "damaged", "returned", "sale"]}},
        ["variant_id", "warehouse_id", "reason"], "adjust_inventory", validate_inventory))
    from app import discounts as discount_service
    discount_props = {"title": {"type": "string"}, "type": {"type": "string", "enum": list(
        discount_service.TYPES)}, "method": {"type": "string", "enum": ["code", "automatic"]},
        "value_type": {"type": "string", "enum": ["percentage", "fixed"]},
        "value": {"type": "integer", "minimum": 0}, "codes": {"type": "string"},
        "discount_id": {"type": "string"}}
    tools.append(write("create_discount", "Create a validated store discount",
        discount_props, ["title", "value"], "create_discount", validate_create_discount))
    tools.append(write("update_discount", "Update an existing store discount",
        discount_props, ["discount_id", "title", "value"], "update_discount", validate_update_discount))
    tools.append(write("deactivate_discount", "Deactivate an existing store discount",
        {"discount_id": {"type": "string"}}, ["discount_id"],
        "deactivate_discount", validate_deactivate_discount))
    tools.append(write("update_shipping_settings", "Update the flat shipping rate and free shipping threshold in minor units",
        {"flat_rate_minor": {"type": ["integer", "null"], "minimum": 0},
         "free_shipping_threshold_minor": {"type": "integer", "minimum": 0}},
        ["flat_rate_minor", "free_shipping_threshold_minor"],
        "update_shipping_settings", validate_shipping))
    tools.append(write("fulfil_order", "Fulfil remaining order items and optionally queue shipment email",
        {"order_id": {"type": "string"}, "carrier": {"type": "string"},
         "tracking_number": {"type": "string"}, "notify_customer": {"type": "boolean"}},
        ["order_id", "carrier", "tracking_number"], "fulfil_order", validate_fulfil))
    tools.append(write("refund_order", "Refund a captured order using integer minor currency units",
        {"order_id": {"type": "string"}, "amount_minor": {"type": "integer", "minimum": 1},
         "reason": {"type": "string"}}, ["order_id", "amount_minor", "reason"], "refund_order", validate_refund))
    for operation in ("cancel", "pause", "resume"):
        tools.append(write(operation + "_subscription", operation.title() + " a subscription",
            {"contract_id": {"type": "string"}}, ["contract_id"], operation + "_subscription",
            lambda a, op=operation: validate_subscription(a, op)))
    tools.append(write("update_customer", "Update customer tags and note without changing consent",
        {"customer_id": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
         "note": {"type": "string"}}, ["customer_id", "tags", "note"], "update_customer", validate_customer_update))
    tools.append(write("send_customer_email", "Queue a merchant message for a customer",
        {"customer_id": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
         "marketing": {"type": "boolean"}}, ["customer_id", "subject", "body"],
        "send_customer_email", validate_customer_email))
    return {t.name: t for t in tools}


def _owned_action(db, site, action_id):
    action = db.scalar(select(AgentAction).where(AgentAction.id == action_id,
        AgentAction.site_id == site.id, AgentAction.tenant_id == site.tenant_id))
    if not action:
        raise CommerceError("Pending action not found.")
    return action


def confirm_action(db, site, user, action_id, *, gateway_factory=None):
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
    if action.kind == "refund_order":
        payload = action.payload_json
        pair = db.execute(select(SiteOrder, Order).join(Order, Order.id == SiteOrder.order_id).where(
            SiteOrder.id == payload["order_id"], SiteOrder.site_id == site.id,
            SiteOrder.tenant_id == site.tenant_id, Order.tenant_id == site.tenant_id)).first()
        try:
            if not pair:
                raise CommerceError("Order not found.")
            link, row = pair
            if _iso(row.updated_at) != payload["expected_version"]:
                raise CommerceError("This order changed after the action was proposed.")
            db.commit()
            from sqlalchemy.orm import sessionmaker

            from app import order_management
            refund_options = {"sessions": sessionmaker(bind=db.get_bind(), expire_on_commit=False)}
            if gateway_factory is not None:
                refund_options["gateway_factory"] = gateway_factory
            command = order_management.refund(site.id, user_id, link.id, payload["amount_minor"],
                payload["reason"], action.idempotency_key, **refund_options)
            action.result_json = {"refund_command_id": command.id, "amount_minor": command.amount_minor,
                                  "currency": command.currency, "state": command.state}
            action.status = "confirmed" if command.state == "succeeded" else "failed"
            note = ("Done: " if action.status == "confirmed" else "Could not complete: ") + action.summary
        except Exception as exc:
            action.status = "failed"
            action.result_json = {"error": str(exc)[:300]}
            note = "Could not complete: " + action.summary
        action.decided_at = datetime.now(UTC)
        db.add(AgentMessage(tenant_id=site.tenant_id, site_id=site.id,
            conversation_id=action.conversation_id, role="assistant", content=note))
        db.commit()
        return action
    execution = db.begin_nested()
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
        elif action.kind == "create_product":
            from app import catalog_admin
            payload = action.payload_json
            variant_names = [variant["name"] for variant in payload["variants"]]
            options = ([{"name": "Variant", "values": variant_names}]
                       if len(variant_names) > 1 else [])
            product = catalog_admin.create_product(db, site, {
                "name": payload["name"], "description": payload["description"],
                "status": payload["status"], "options": options,
                "variants": [{"price_minor": variant["price_minor"]}
                             for variant in payload["variants"]]}, user_id=user_id)
            action.result_json = {"product_id": product.id, "variant_ids": [v.id for v in product.variants],
                                  "status": product.status}
        elif action.kind == "adjust_inventory":
            from app import catalog_admin
            payload = action.payload_json
            p = catalog_admin.get_product(db, site, payload["product_id"])
            if p.version != payload["expected_version"]:
                raise CommerceError("This product changed after the inventory action was proposed.")
            adjustment = catalog_admin.adjust_inventory(db, site, payload["variant_id"],
                payload["warehouse_id"], payload["amount"], mode=payload["mode"],
                reason=payload["reason"], user_id=user_id)
            action.result_json = {"adjustment_id": adjustment.id, "delta": adjustment.delta,
                                  "quantity": adjustment.resulting_quantity}
        elif action.kind in {"create_discount", "update_discount", "deactivate_discount"}:
            from app import discount_routes
            from app.checkout_services import lock_site
            lock_site(db, site)
            payload = action.payload_json
            if action.kind == "create_discount":
                fields = _discount_form_fields(payload["fields"], payload["codes"])
                rule = discount_routes.save(db, site, _AgentForm(fields))
            else:
                rule = db.scalar(select(Discount).where(Discount.id == payload["discount_id"],
                    Discount.site_id == site.id, Discount.tenant_id == site.tenant_id,
                    Discount.deleted_at.is_(None)).with_for_update())
                if not rule:
                    raise CommerceError("Discount not found.")
                if _iso(rule.updated_at) != payload["expected_updated_at"]:
                    raise CommerceError("This discount changed after the action was proposed.")
                if action.kind == "deactivate_discount":
                    rule.is_active = False
                else:
                    fields = _discount_form_fields(payload["fields"], payload["codes"])
                    rule = discount_routes.save(db, site, _AgentForm(fields), rule)
            action.result_json = {"discount_id": rule.id, "active": rule.is_active}
        elif action.kind == "update_shipping_settings":
            from app import commerce, site_builder_services
            payload = action.payload_json
            site = site_builder_services.lock_site(db, site.id, user_id, payload["site_version"])
            config = commerce.settings_for(db, site, create=True)
            config = db.scalar(select(SiteCommerceSettings).where(
                SiteCommerceSettings.id == config.id, SiteCommerceSettings.site_id == site.id,
                SiteCommerceSettings.tenant_id == site.tenant_id).with_for_update()
                .execution_options(populate_existing=True))
            if config.version != payload["settings_version"]:
                raise CommerceError("Shipping settings changed after the action was proposed.")
            config.shipping_minor = payload["flat_rate_minor"]
            config.free_shipping_threshold_minor = payload["free_shipping_threshold_minor"]
            config.version += 1
            site.version += 1
            action.result_json = {"flat_rate_minor": config.shipping_minor,
                "free_shipping_threshold_minor": config.free_shipping_threshold_minor,
                "version": config.version}
        elif action.kind == "fulfil_order":
            from app import order_admin
            payload = action.payload_json
            pair = db.execute(select(SiteOrder, Order).join(Order, Order.id == SiteOrder.order_id).where(
                SiteOrder.id == payload["order_id"], SiteOrder.site_id == site.id,
                SiteOrder.tenant_id == site.tenant_id, Order.tenant_id == site.tenant_id)).first()
            if not pair:
                raise CommerceError("Order not found.")
            link, row = pair
            if _iso(row.updated_at) != payload["expected_version"]:
                raise CommerceError("This order changed after the action was proposed.")
            event = order_admin.fulfil_items(db, site, user_id, link.id, payload["quantities"],
                carrier=payload["carrier"], tracking_number=payload["tracking_number"],
                notify=payload["notify_customer"], request_key=action.idempotency_key)
            action.result_json = {"shipment_id": event.id, "status": event.status}
        elif action.kind in {"cancel_subscription", "pause_subscription", "resume_subscription"}:
            from app import subscriptions
            payload = action.payload_json
            subscription = db.scalar(select(SubscriptionContract).where(
                SubscriptionContract.id == payload["contract_id"], SubscriptionContract.site_id == site.id,
                SubscriptionContract.tenant_id == site.tenant_id))
            if not subscription or subscription.version != payload["expected_version"]:
                raise CommerceError("This subscription changed after the action was proposed.")
            op = action.kind.removesuffix("_subscription")
            subscription = subscriptions.merchant_change(db, site, user_id, subscription.id, op,
                version=subscription.version, request_key=action.idempotency_key)
            action.result_json = {"contract_id": subscription.id, "state": subscription.state,
                                  "version": subscription.version}
        elif action.kind == "update_customer":
            from app import customer_admin
            payload = action.payload_json
            row = db.scalar(select(ShopCustomer).where(ShopCustomer.id == payload["customer_id"],
                ShopCustomer.tenant_id == site.tenant_id, ShopCustomer.site_id == site.id))
            if not row:
                raise CommerceError("Customer not found.")
            if _iso(row.updated_at) != payload["expected_updated_at"]:
                raise CommerceError("This customer changed after the action was proposed.")
            row = customer_admin.save_customer(db, site, {"email": payload["email"],
                "tags": payload["tags"], "note": payload["note"]}, row.id)
            action.result_json = {"customer_id": row.id, "tags": row.tags_json}
        elif action.kind == "send_customer_email":
            from app.models import CommerceMail
            payload = action.payload_json
            row = db.scalar(select(ShopCustomer).where(ShopCustomer.id == payload["customer_id"],
                ShopCustomer.tenant_id == site.tenant_id, ShopCustomer.site_id == site.id))
            if not row:
                raise CommerceError("Customer not found.")
            if _iso(row.updated_at) != payload["expected_updated_at"]:
                raise CommerceError("This customer changed after the action was proposed.")
            message = CommerceMail(tenant_id=site.tenant_id, site_id=site.id, customer_id=row.id,
                kind="merchant_message", dedupe_key="agent-message:" + action.idempotency_key,
                available_at=datetime.now(UTC), reference_json={"subject": payload["subject"],
                    "body": payload["body"], "marketing": payload["marketing"]})
            db.add(message)
            db.flush()
            action.result_json = {"mail_id": message.id, "status": message.status}
        else:
            raise CommerceError("This action type cannot be executed yet.")
        execution.commit()
        action.status = "confirmed"
        note = "Done: " + action.summary
    except Exception as exc:
        if execution.is_active:
            execution.rollback()
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
        def make_endpoint(action_handler):
            async def endpoint(site_id: str, action_id: str, request):
                try:
                    headers = getattr(request, "headers", {})
                    if headers.get("content-type", "").startswith("application/json"):
                        form = await request.json()
                    else:
                        form = await request.form()
                    check_csrf(request.session, form)
                    user_id = actor(request.session)
                    with SessionLocal() as db:
                        site = db.scalar(select(Site).where(Site.id == site_id))
                        if not site:
                            raise CommerceError("Site not found.")
                        action = action_handler(db, site, user_id, action_id)
                        return JSONResponse({"status": action.status, "summary": action.summary,
                                             "result": action.result_json})
                except Exception as exc:
                    return error(exc)
            return endpoint

        rt(f"/admin/sites/{{site_id}}/agent/actions/{{action_id}}/{decision}", methods=["POST"])(
            make_endpoint(handler))
