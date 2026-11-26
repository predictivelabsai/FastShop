"""Tenant-scoped store tools and confirmed merchant actions for the store assistant."""
from __future__ import annotations

import copy
import json
import re
import secrets
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, or_, select, update

from app import customer_admin
from app.models import (
    AgentAction,
    AgentMessage,
    CatalogueSettings,
    Channel,
    Collection,
    CustomerSegment,
    Discount,
    DiscountCode,
    Membership,
    NotificationTemplate,
    Order,
    Product,
    ProductVariant,
    ShippingRate,
    ShippingZone,
    ShopCustomer,
    SiteCommerceSettings,
    SiteIntegrationCredential,
    SiteOrder,
    SitePage,
    Stock,
    SubscriptionContract,
    TaxRate,
    VariantChannelListing,
    Warehouse,
)
from app.services import CommerceError, money

SUMMARY_LIMIT = 500
# The policy keys the Settings, Policies page edits.
POLICY_LABELS = {
    "refund": "returns and refunds policy",
    "privacy": "privacy policy",
    "terms": "terms and conditions",
    "shipping_policy": "shipping policy",
}
REVIEW_VALUE_LIMIT = 160

# These are relative to a site's admin root so that the assistant can return a
# working, tenant-specific route without being given any provider credentials.
OPERATOR_CAPABILITIES = (
    ("General store data", ("general", "store details", "preferences", "brand"),
     "/settings/general", "draft", "Draft store details and shared content are editable."),
    ("Pages", ("page", "pages", "content", "metadata"), "/content/pages", "draft",
     "Draft pages and their titles and descriptions can be created and edited before publication."),
    ("Blog posts", ("blog", "article", "articles", "post", "posts"), "/content/blog-posts", "draft",
     "Article drafts can be created and edited before publication."),
    ("Navigation", ("navigation", "menu", "menus"), "/content/menus", "draft",
     "Draft menus can be edited before publication."),
    ("Draft visual theme and shell content", ("theme", "visual", "shell", "design", "editor"),
     "/editor", "draft", "Visual and shell changes stay in the store draft."),
    ("Analytics tracking", ("tracking", "pixel", "analytics", "ga4", "google analytics"),
     "/settings/general", "route", "The Google Analytics measurement ID is managed on this route."),
    ("Catalogue", ("catalogue", "catalog", "product", "collection", "price"),
     "/products", "review", "Catalogue and price changes require a merchant review card."),
    ("Discounts", ("discount", "promotion", "voucher", "coupon"), "/discounts", "review",
     "Discount changes require a merchant review card."),
    ("Inventory", ("inventory", "stock", "warehouse", "location"), "/inventory", "review",
     "Inventory changes require a merchant review card."),
    ("Shipping", ("shipping", "delivery", "fulfilment", "fulfillment"),
     "/settings/shipping", "review", "Shipping changes require a merchant review card."),
    ("Checkout", ("checkout", "customer account", "customer accounts"),
     "/settings/checkout", "review", "Checkout changes require a merchant review card."),
    ("Taxes", ("tax", "taxes", "duty", "duties"), "/settings/taxes", "review",
     "Tax changes require a merchant review card."),
    ("Customers", ("customer", "customers", "customer record", "segment"),
     "/customers", "review", "Customer changes require a merchant review card."),
    ("Subscriptions", ("subscription", "subscriptions", "recurring"), "/subscriptions", "review",
     "Subscription changes require a merchant review card."),
    ("Notifications", ("notification", "notifications", "email delivery"),
     "/settings/notifications", "review", "Notification changes require a merchant review card."),
    ("Policies", ("policy", "policies", "privacy", "returns", "refund", "terms"),
     "/settings/policies", "review", "Policy updates require a merchant review card."),
    ("Domains", ("domain", "domains", "go live", "go-live"), "/settings/domains", "route",
     "Domain and go-live settings are managed on this route."),
    ("Integrations", ("integration", "integrations", "connector", "connect service", "import"),
     "/integrations", "route", "Integration configuration is managed on this route."),
    ("Payment credentials and live mode", ("credential", "credentials", "secret", "live payment", "live mode", "payment mode"),
     "/settings/payments", "protected", "Credentials and live payment mode stay protected in Settings."),
    ("Assistant model and API key", ("assistant", "ai model", "api key", "openai", "xai", "openrouter", "model key"),
     "/settings/assistant", "protected", "The assistant's model connection and key stay protected in Settings."),
    ("Staff permissions", ("staff", "permission", "permissions", "user role", "users"),
     "/settings/users", "protected", "Staff access and roles stay protected in Settings."),
    ("Store deletion", ("delete store", "delete site", "irreversible deletion", "remove store"),
     "/settings", "protected", "Irreversible deletion is not available through the assistant."),
)


def operator_capabilities(site, query=""):
    """Return the bounded, tenant-specific operator capability directory."""
    terms = {term for term in re.findall(r"[a-z0-9]+", str(query).lower()) if term}
    candidates = []
    for domain, aliases, suffix, mode, guidance in OPERATOR_CAPABILITIES:
        haystack = " ".join((domain, *aliases)).lower()
        score = sum(term in haystack for term in terms)
        if terms and not score:
            continue
        candidates.append((score, {"domain": domain, "route": f"/admin/sites/{site.id}{suffix}",
                                   "mode": mode, "guidance": guidance}))
    best_score = max((score for score, _ in candidates), default=0)
    rows = [row for score, row in candidates if score == best_score]
    result = {"capabilities": rows, "settings_route": f"/admin/sites/{site.id}/settings"}
    if terms and not rows:
        result["guidance"] = "No exact assistant capability matched. Open Settings to choose a domain."
    return result


def _display(value, limit=REVIEW_VALUE_LIMIT):
    if value is None or value == "" or value == []:
        return "not set"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, sort_keys=True, default=str)
    else:
        text = str(value)
    return text if limit is None or len(text) <= limit else text[:limit - 3].rstrip() + "..."


def _review(changes, *, exact=False):
    limit = None if exact else REVIEW_VALUE_LIMIT
    return {"changes": [{"field": str(label)[:80], "before": _display(before, limit),
                         "after": _display(after, limit)} for label, before, after in changes]}


def _summary(*parts):
    text = "; ".join(str(part) for part in parts if part)
    return text if len(text) <= SUMMARY_LIMIT else text[:SUMMARY_LIMIT - 3].rstrip() + "..."


def _change(label, before, after):
    return f"{label}: {_display(before)} → {_display(after)}"


def _with_review(payload, changes, *, exact=False):
    return payload | {"review": _review(changes, exact=exact)}


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

    def read(name, description, props, required, fn, max_output_chars=1200):
        return ToolSpec(name, description, _schema(props, required), fn, "read",
                        name.replace("_", " ").title(), max_output_chars)

    def write(name, description, props, required, kind, validate):
        def propose(args):
            payload, summary, version = validate(args)
            summary = _summary(summary)
            review = payload.get("review", {"changes": []})
            action = AgentAction(tenant_id=site.tenant_id, site_id=site.id,
                conversation_id=conversation_id, user_id=user_id, kind=kind,
                payload_json=payload | {"expected_version": version}, summary=summary,
                idempotency_key=secrets.token_urlsafe(24), status="pending")
            db.add(action)
            db.flush()
            return {"pending_action": action.id, "kind": kind, "summary": summary,
                    "review": review}
        return ToolSpec(name, description + ". This only proposes the change; merchant confirmation is required.",
                        _schema(props, required), propose, "store_write",
                        name.replace("_", " ").title(), 900)

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

    def preview_customer_audience(a):
        """Summarise a prospective customer group without exposing its members."""
        supplied = a.get("filters", {})
        if not isinstance(supplied, dict):
            raise CommerceError("Audience filters must be an object.")
        allowed = customer_admin.FILTER_KEYS - {"q", "sort"}
        unknown = set(supplied) - allowed
        if unknown:
            raise CommerceError("Choose only tag, location, consent, order or spend audience filters.")
        filters = customer_admin.validate_filters(supplied)
        rows = customer_admin.customer_rows(db, site, filters)
        subscribed = sum(1 for row in rows if row.email_consent)
        tag_counts = {}
        for row in rows:
            for tag in row.customer.tags_json or []:
                tag = str(tag).strip()
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        currency = rows[0].currency if rows else db.get(Channel, site.channel_id).currency
        return {
            "filters": filters,
            "matched_customers": len(rows),
            "email_marketing_consent_customers": subscribed,
            "not_subscribed_customers": len(rows) - subscribed,
            "orders": sum(row.orders for row in rows),
            "paid_spend_minor": sum(row.spent for row in rows),
            "currency": currency,
            "top_tags": [{"tag": tag, "customers": count} for tag, count in
                         sorted(tag_counts.items(), key=lambda item: (-item[1], item[0].casefold()))[:12]],
            "member_details_included": False,
            "note": "This is a planning preview only. It does not create a recipient list or send email.",
        }

    def list_customer_segments(_a):
        segments = []
        for segment in customer_admin.segments_for(db, site):
            segments.append({
                "id": segment.id,
                "name": segment.name,
                "filters": segment.filters_json,
                "matched_customers": len(customer_admin.customer_rows(db, site, segment.filters_json)),
                "route": f"/admin/sites/{site.id}/customers?segment={segment.id}",
            })
        return {
            "segments": segments,
            "customer_details_included": False,
            "segments_changed": False,
        }

    def validate_customer_segment(a):
        name = str(a.get("name", "")).strip()
        if not name or len(name) > 100:
            raise CommerceError("Enter a segment name of up to 100 characters.")
        supplied = a.get("filters")
        if not isinstance(supplied, dict):
            raise CommerceError("Segment filters must be an object.")
        allowed = customer_admin.FILTER_KEYS - {"q", "sort"}
        if set(supplied) - allowed:
            raise CommerceError("Choose only tag, location, consent, order or spend segment filters.")
        filters = customer_admin.validate_filters(supplied)
        if not filters:
            raise CommerceError("Choose at least one customer segment filter.")
        existing = db.scalar(select(CustomerSegment).where(
            CustomerSegment.tenant_id == site.tenant_id,
            CustomerSegment.site_id == site.id,
            CustomerSegment.name == name,
        ))
        before = dict(existing.filters_json) if existing else None
        payload = _with_review({
            "name": name,
            "filters": filters,
            "expected_existing_filters": before,
        }, [("Segment", "not saved" if before is None else before, filters)])
        verb = "Update" if existing else "Create"
        return payload, f"{verb} customer segment {name}", site.version

    def get_customer(a):
        c = customer(a["customer_id"])
        return {"id": c.id, "name": c.name, "email": c.email, "phone": c.phone,
                "tags": c.tags_json, "note": c.note[:3000], "created_at": _iso(c.created_at)}

    def customer_support_brief(a):
        c = customer(a["customer_id"])
        rows = db.execute(select(SiteOrder, Order).join(Order, Order.id == SiteOrder.order_id).where(
            SiteOrder.tenant_id == site.tenant_id,
            SiteOrder.site_id == site.id,
            SiteOrder.customer_id == c.id,
            Order.tenant_id == site.tenant_id,
        ).order_by(Order.created_at.desc(), Order.id.desc()).limit(8)).all()
        return {
            "customer": {
                "id": c.id,
                "name": c.name,
                "email": c.email,
                "tags": c.tags_json,
                "note": c.note[:1000],
            },
            "orders": [{
                "id": link.id,
                "number": order.number,
                "status": order.status,
                "payment_status": order.payment_status,
                "total_minor": order.total_minor,
                "currency": order.currency,
                "created_at": _iso(order.created_at),
            } for link, order in rows],
        }

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
            "checkout": {
                "customer_accounts_enabled": settings.customer_accounts_enabled if settings else False,
                "shipping_minor": settings.shipping_minor if settings else None,
                "free_shipping_threshold_minor": (
                    settings.free_shipping_threshold_minor if settings else 7500
                ),
            },
            "payments": {"connected": bool(settings and settings.mode not in {"", "disabled"})},
            "tracking": {"ga4_measurement_id": str((site.settings_json or {}).get("ga4_measurement_id", ""))},
            "policies": {k: str(v)[:2000] for k, v in ((site.settings_json or {}).get("policies") or {}).items()
                         if k in POLICY_LABELS}}

    def validate_policies(a):
        values = a.get("values")
        if not isinstance(values, dict) or not values or set(values) - set(POLICY_LABELS):
            raise CommerceError("Choose one or more supported store policies.")
        cleaned = {}
        for key, raw in values.items():
            if not isinstance(raw, str) or len(raw) > 50000:
                raise CommerceError(f"The {POLICY_LABELS[key]} must contain fewer than 50,000 characters.")
            cleaned[key] = raw.strip()
        existing = (site.settings_json or {}).get("policies") or {}
        changes = []
        for key, value in cleaned.items():
            before_count = len(str(existing.get(key, "")))
            after_count = len(value)
            changes.append((POLICY_LABELS[key], f"{before_count} characters", f"{after_count} characters"))
        return _with_review({"values": cleaned}, changes), _summary(
            *[_change(label, old, new) for label, old, new in changes]
        ), site.version

    def validate_checkout_settings(a):
        allowed = {
            "customer_accounts_enabled",
            "shipping_minor",
            "free_shipping_threshold_minor",
        }
        if not isinstance(a, dict) or set(a) - allowed:
            raise CommerceError("Choose supported checkout settings.")
        fields = {field: a[field] for field in (
            "customer_accounts_enabled", "shipping_minor", "free_shipping_threshold_minor"
        ) if field in a}
        if not fields:
            raise CommerceError("Provide at least one checkout setting to update.")
        if ("customer_accounts_enabled" in fields
                and type(fields["customer_accounts_enabled"]) is not bool):
            raise CommerceError("Choose whether customer accounts are enabled.")
        for field, label, optional in (
            ("shipping_minor", "Default shipping", True),
            ("free_shipping_threshold_minor", "Free shipping threshold", False),
        ):
            if field not in fields:
                continue
            value = fields[field]
            if value is None and optional:
                continue
            if type(value) is not int or not 0 <= value <= 1_000_000:
                raise CommerceError(f"{label} must be between 0 and 1,000,000 minor units.")
        config = db.scalar(select(SiteCommerceSettings).where(
            SiteCommerceSettings.site_id == site.id,
            SiteCommerceSettings.tenant_id == site.tenant_id,
        ))
        current = {
            "customer_accounts_enabled": config.customer_accounts_enabled if config else False,
            "shipping_minor": config.shipping_minor if config else None,
            "free_shipping_threshold_minor": config.free_shipping_threshold_minor if config else 7500,
        }
        values = current | fields
        channel = db.get(Channel, site.channel_id)
        currency = channel.currency if channel else "USD"
        labels = {
            "customer_accounts_enabled": "Customer accounts",
            "shipping_minor": "Default shipping",
            "free_shipping_threshold_minor": "Free shipping threshold",
        }
        changes = []
        for field in labels:
            if field not in fields:
                continue
            before, after = current[field], values[field]
            if field in {"shipping_minor", "free_shipping_threshold_minor"}:
                before = money(before, currency) if before is not None else None
                after = money(after, currency) if after is not None else None
            changes.append((labels[field], before, after))
        return _with_review({
            "fields": fields,
            "expected_values": current,
            "settings_version": config.version if config else 1,
            "site_version": site.version,
        }, changes), _summary(*[_change(label, old, new) for label, old, new in changes]), (
            config.version if config else 1,
            site.version,
        )

    def validate_notification_template(a):
        from app import growth_settings

        allowed = {"event", "subject", "body", "enabled"}
        if not isinstance(a, dict) or set(a) - allowed:
            raise CommerceError("Choose supported notification settings.")
        event = a.get("event")
        if not isinstance(event, str) or event not in growth_settings.EVENTS:
            raise CommerceError("Choose a notification event.")
        subject, body = a.get("subject"), a.get("body")
        if not isinstance(subject, str) or not isinstance(body, str):
            raise CommerceError("Notification subject and message must be text.")
        enabled = a.get("enabled", True)
        if type(enabled) is not bool:
            raise CommerceError("Choose whether the notification is enabled.")
        growth_settings.render_template(subject, body, {
            "store_name": site.name,
            "customer_name": "Customer",
            "order_number": "ORDER-001",
            "total": "0.00",
            "tracking_number": "TRACKING",
            "action_url": "https://example.test/",
            "message": "Message",
        }, escape=False)
        template = db.scalar(select(NotificationTemplate).where(
            NotificationTemplate.tenant_id == site.tenant_id,
            NotificationTemplate.site_id == site.id,
            NotificationTemplate.event == event,
        ))
        expected = None if template is None else {
            "subject": template.subject,
            "body": template.body,
            "enabled": template.enabled,
        }
        before = expected or {}
        changes = [
            ("Subject", before.get("subject"), subject),
            ("Message", before.get("body"), body),
            ("Enabled", before.get("enabled"), enabled),
        ]
        return _with_review({
            "event": event,
            "subject": subject,
            "body": body,
            "enabled": enabled,
            "expected_values": expected,
        }, changes, exact=True), _summary(
            f"Notification {event.replace('_', ' ')}",
            *[_change(label, old, new) for label, old, new in changes],
        ), _iso(template.updated_at) if template else None

    def validate_tax_rate(a):
        country = str(a.get("country", "")).upper().strip()
        region = str(a.get("region", "")).upper().strip()
        name = str(a.get("name", "Sales tax")).strip()
        rate_bps = a.get("rate_bps")
        included = a.get("prices_include_tax", False)
        if not re.fullmatch(r"[A-Z]{2}", country) or not re.fullmatch(r"[A-Z0-9:-]{0,20}", region):
            raise CommerceError("Choose a valid country and optional region code.")
        if not name or len(name) > 100 or type(rate_bps) is not int or not 0 <= rate_bps <= 10000:
            raise CommerceError("Choose a tax name and a rate between 0% and 100%.")
        if type(included) is not bool:
            raise CommerceError("Choose whether prices include tax.")
        current = db.scalar(select(TaxRate).where(
            TaxRate.tenant_id == site.tenant_id, TaxRate.site_id == site.id,
            TaxRate.country == country, TaxRate.region == region,
        ))
        expected = None if current is None else {
            "name": current.name,
            "rate_bps": current.rate_bps,
            "prices_include_tax": current.prices_include_tax,
        }
        before = "not set" if current is None else (
            f"{current.name}, {current.rate_bps / 100:g}%, "
            f"{'included' if current.prices_include_tax else 'excluded'}"
        )
        after = f"{name}, {rate_bps / 100:g}%, {'included' if included else 'excluded'}"
        changes = [(f"Tax rate for {country}{('-' + region) if region else ''}", before, after)]
        return _with_review({
            "country": country,
            "region": region,
            "name": name,
            "rate_bps": rate_bps,
            "prices_include_tax": included,
            "expected_values": expected,
            "expected_updated_at": _iso(current.updated_at) if current else None,
        }, changes), _summary(*[_change(label, old, new) for label, old, new in changes]), (
            _iso(current.updated_at) if current else None
        )

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

    def sales_comparison(a):
        from app.order_management import revenue_report

        period = str(a.get("period", "7d"))
        if period not in {"7d", "30d", "90d"}:
            raise CommerceError("Choose 7d, 30d or 90d.")
        days = int(period[:-1])
        current_end = date.today()
        current_start = current_end - timedelta(days=days - 1)
        previous_end = current_start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=days - 1)
        current = revenue_report(db, site, current_start, current_end)
        previous = revenue_report(db, site, previous_start, previous_end)

        def values(report):
            return {
                "from": report.start_date.isoformat(),
                "to": report.end_date.isoformat(),
                "orders": report.order_count,
                "gross_sales_minor": report.gross_sales_minor,
                "refunds_minor": report.refunds_minor,
                "net_revenue_minor": report.net_revenue_minor,
            }

        result = {
            "period": period,
            "currency": current.currency,
            "current": values(current),
            "previous": values(previous),
            "change": {
                "orders": current.order_count - previous.order_count,
                "gross_sales_minor": current.gross_sales_minor - previous.gross_sales_minor,
                "refunds_minor": current.refunds_minor - previous.refunds_minor,
                "net_revenue_minor": current.net_revenue_minor - previous.net_revenue_minor,
            },
        }
        if not current.order_count and not previous.order_count:
            result["message"] = "No paid orders were found in either period."
        return result

    def merchandising_brief(a):
        limit = _bounded(a.get("limit"), default=12, maximum=20)
        period = str(a.get("period", "30d"))
        if period not in {"7d", "30d", "90d"}:
            raise CommerceError("Choose 7d, 30d or 90d.")
        products = db.scalars(select(Product).where(
            Product.tenant_id == site.tenant_id,
        ).order_by(Product.status != "active", Product.name, Product.id).limit(limit)).all()
        catalogue = []
        for row in products:
            prices = [listing.price_minor for variant in row.variants for listing in [db.scalar(select(
                VariantChannelListing).where(VariantChannelListing.variant_id == variant.id,
                VariantChannelListing.channel_id == site.channel_id))] if listing is not None]
            catalogue.append({
                "product_id": row.id,
                "name": row.name,
                "status": row.status,
                "variants": len(row.variants),
                "from_price_minor": min(prices) if prices else None,
            })
        revenue = sales_summary({"period": period})
        low_stock = low_stock_variants({"limit": min(limit, 12)})
        collections = db.scalar(select(func.count(Collection.id)).where(
            Collection.tenant_id == site.tenant_id,
        )) or 0
        return {
            "period": period,
            "revenue": revenue,
            "collections": collections,
            "catalogue": catalogue,
            "low_stock": low_stock["variants"],
            "catalogue_truncated": len(catalogue) == limit,
        }

    def audit_product_content(a):
        """Identify incomplete catalogue content without modifying products or pages."""
        requested_id = str(a.get("product_id", "")).strip()
        if requested_id:
            rows = [product(requested_id)]
        else:
            limit = _bounded(a.get("limit"), default=20, maximum=40)
            rows = list(db.scalars(select(Product).where(
                Product.tenant_id == site.tenant_id,
            ).order_by(Product.status != "active", Product.name, Product.id).limit(limit)))
        product_page_ids = set(db.scalars(select(SitePage.product_id).where(
            SitePage.tenant_id == site.tenant_id,
            SitePage.site_id == site.id,
            SitePage.product_id.is_not(None),
        )))
        findings = []
        for row in rows:
            active_variants = [variant for variant in row.variants if variant.is_active]
            priced_variants = [variant for variant in active_variants if db.scalar(select(
                VariantChannelListing.id
            ).where(VariantChannelListing.variant_id == variant.id,
                    VariantChannelListing.channel_id == site.channel_id))]
            issues = []
            if not (row.description_html or "").strip():
                issues.append("missing_description")
            if not (row.short_description or "").strip():
                issues.append("missing_short_description")
            if not row.images and not (row.image_url or "").strip():
                issues.append("missing_image")
            if not (row.seo_title or "").strip():
                issues.append("missing_seo_title")
            if not (row.seo_description or "").strip():
                issues.append("missing_seo_description")
            if not active_variants:
                issues.append("no_active_variant")
            elif len(priced_variants) != len(active_variants):
                issues.append("variant_without_channel_price")
            if row.status == "active" and row.id not in product_page_ids:
                issues.append("missing_storefront_page")
            findings.append({
                "product_id": row.id,
                "name": row.name,
                "status": row.status,
                "issues": issues,
                "complete": not issues,
                "recommended_routes": [
                    f"/admin/sites/{site.id}/products/{row.id}",
                    f"/admin/sites/{site.id}/settings/seo",
                ],
            })
        return {
            "products": findings,
            "audited_products": len(findings),
            "products_with_issues": sum(1 for finding in findings if finding["issues"]),
            "catalogue_changed": False,
            "note": "This audit is read-only. Product, price and SEO changes still require merchant review.",
        }

    def fulfilment_queue(a):
        limit = _bounded(a.get("limit"), default=10, maximum=20)
        rows = db.execute(select(SiteOrder, Order).join(Order, Order.id == SiteOrder.order_id).where(
            SiteOrder.tenant_id == site.tenant_id,
            SiteOrder.site_id == site.id,
            Order.tenant_id == site.tenant_id,
            Order.status.in_(("unfulfilled", "partially_fulfilled")),
        ).order_by(Order.created_at, Order.id).limit(limit)).all()
        orders = []
        for link, row in rows:
            remaining = sum(max(0, line.quantity - line.fulfilled_quantity
                                - line.unfulfilled_refunded_quantity) for line in row.lines)
            orders.append({
                "id": link.id,
                "number": row.number,
                "status": row.status,
                "items_remaining": remaining,
                "created_at": _iso(row.created_at),
            })
        result = {"orders": orders}
        if not orders:
            result["message"] = "No orders are awaiting fulfilment."
        return result

    def low_stock_variants(a):
        limit = _bounded(a.get("limit"), default=10, maximum=20)
        settings = db.scalar(select(CatalogueSettings).where(
            CatalogueSettings.tenant_id == site.tenant_id,
            CatalogueSettings.site_id == site.id,
        ))
        threshold = settings.low_stock_threshold if settings else 5
        available_stock = select(
            Stock.variant_id.label("variant_id"),
            func.coalesce(func.sum(Stock.quantity - Stock.allocated), 0).label("available"),
        ).join(Warehouse, Warehouse.id == Stock.warehouse_id).where(
            Warehouse.tenant_id == site.tenant_id,
            Warehouse.is_active.is_(True),
            Warehouse.fulfils_online_orders.is_(True),
        ).group_by(Stock.variant_id).subquery()
        available = func.coalesce(available_stock.c.available, 0)
        rows = db.execute(select(Product, ProductVariant, available).join(
            ProductVariant, ProductVariant.product_id == Product.id,
        ).outerjoin(
            available_stock, available_stock.c.variant_id == ProductVariant.id,
        ).where(
            Product.tenant_id == site.tenant_id,
            ProductVariant.tenant_id == site.tenant_id,
            ProductVariant.is_active.is_(True),
            ProductVariant.track_inventory.is_(True),
            available <= threshold,
        ).order_by(available, Product.name, ProductVariant.name, ProductVariant.id).limit(limit)).all()
        variants = [{
            "product": product.name,
            "variant": variant.name,
            "sku": variant.sku,
            "available": int(quantity),
        } for product, variant, quantity in rows]
        result = {"threshold": threshold, "variants": variants}
        if not variants:
            result["message"] = "No tracked variants are at or below the low-stock threshold."
        return result

    def integration_status(a):
        from app.assistant_provider import CREDENTIAL

        limit = _bounded(a.get("limit"), default=10, maximum=20)
        credentials = db.execute(select(
            SiteIntegrationCredential.provider,
            SiteIntegrationCredential.status,
            SiteIntegrationCredential.mode,
            SiteIntegrationCredential.verified_at,
        ).where(
            SiteIntegrationCredential.tenant_id == site.tenant_id,
            SiteIntegrationCredential.site_id == site.id,
            SiteIntegrationCredential.provider != CREDENTIAL,
        ).order_by(SiteIntegrationCredential.provider).limit(limit)).all()
        integrations = [{
            "provider": provider,
            "status": status,
            "mode": mode,
            "verified_at": _iso(verified_at),
        } for provider, status, mode, verified_at in credentials]
        result = {"integrations": integrations,
                  "integrations_route": f"/admin/sites/{site.id}/integrations"}
        if not integrations:
            result["message"] = "No integrations are configured for this store."
        return result

    def integration_setup_guide(a):
        provider = str(a["provider"]).strip().lower()
        importer = "/settings/integrations/importers/"
        guides = {
            "stripe": {
                "label": "Stripe payments",
                "route": f"/admin/sites/{site.id}/settings/payments",
                "requirements": ["A Stripe account", "test mode first, then live approval"],
                "next_steps": ["Connect Stripe from the protected payments settings.",
                               "Test checkout in test mode before asking for live payments."],
            },
            "shopify": {
                "label": "Shopify import",
                "route": f"/admin/sites/{site.id}{importer}shopify",
                "requirements": ["Shopify store domain", "Admin API access token from a custom app"],
                "next_steps": ["Enter the details in the protected connection form.",
                               "Run a preview import before applying it."],
            },
            "woocommerce": {
                "label": "WooCommerce import",
                "route": f"/admin/sites/{site.id}{importer}woocommerce",
                "requirements": ["Store address", "REST API consumer key and secret with read access"],
                "next_steps": ["Enter the details in the protected connection form.",
                               "Run a preview import before applying it."],
            },
            "wordpress": {
                "label": "WordPress import",
                "route": f"/admin/sites/{site.id}{importer}wordpress",
                "requirements": ["Site address", "optional username and application password"],
                "next_steps": ["Enter the details in the protected connection form.",
                               "Run a preview import before applying it."],
            },
        }
        guide = guides.get(provider)
        if guide is None:
            raise CommerceError("Choose Stripe, Shopify, WooCommerce or WordPress setup guidance.")
        return guide | {
            "provider": provider,
            "credential_entry_is_protected": True,
            "assistant_can_not_receive_or_reveal_credentials": True,
        }

    tools = [
        read("get_operator_capabilities", "Find a merchant-managed domain, its exact route and control boundary",
             {"query": {"type": "string", "maxLength": 120}}, [],
             lambda a: operator_capabilities(site, a.get("query", "")), 4800),
        read("list_products", "Search store products", {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, [], list_products),
        read("get_product", "Read product variants, prices and stock", {"product_id": {"type": "string"}}, ["product_id"], get_product),
        read("list_customers", "Search customers", {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, [], list_customers),
        read("preview_customer_audience", "Preview a filtered customer audience without exposing customer details or changing any campaign", {
            "filters": {"type": "object", "additionalProperties": False, "properties": {
                "tag": {"type": "string", "maxLength": 80},
                "location": {"type": "string", "maxLength": 160},
                "consent": {"type": "string", "enum": ["subscribed", "not_subscribed"]},
                "orders_min": {"type": "integer", "minimum": 0, "maximum": 1000000000000},
                "orders_max": {"type": "integer", "minimum": 0, "maximum": 1000000000000},
                "spent_min": {"type": "integer", "minimum": 0, "maximum": 1000000000000},
                "spent_max": {"type": "integer", "minimum": 0, "maximum": 1000000000000},
            }},
        }, [], preview_customer_audience, 2400),
        read("list_customer_segments", "List saved customer segments with aggregate matches and no customer details", {}, [], list_customer_segments, 3200),
        write("save_customer_segment", "Create or update a saved customer segment for merchant confirmation", {
            "name": {"type": "string", "maxLength": 100},
            "filters": {"type": "object", "additionalProperties": False, "properties": {
                "tag": {"type": "string", "maxLength": 80},
                "location": {"type": "string", "maxLength": 160},
                "consent": {"type": "string", "enum": ["subscribed", "not_subscribed"]},
                "orders_min": {"type": "integer", "minimum": 0, "maximum": 1000000000000},
                "orders_max": {"type": "integer", "minimum": 0, "maximum": 1000000000000},
                "spent_min": {"type": "integer", "minimum": 0, "maximum": 1000000000000},
                "spent_max": {"type": "integer", "minimum": 0, "maximum": 1000000000000},
            }},
        }, ["name", "filters"], "save_customer_segment", validate_customer_segment),
        read("get_customer", "Read customer profile without payment details", {"customer_id": {"type": "string"}}, ["customer_id"], get_customer),
        read("customer_support_brief", "Read safe customer and recent-order facts before drafting a support reply", {
            "customer_id": {"type": "string"},
        }, ["customer_id"], customer_support_brief, 3200),
        read("list_orders", "List recent store orders", {"status": {"type": "string"}, "query": {"type": "string"}, "date_from": {"type": "string"}, "date_to": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, [], list_orders),
        read("get_order", "Read an order with safe shipping summary", {"order_id": {"type": "string"}}, ["order_id"], get_order),
        read("list_subscriptions", "List subscriptions", {"state": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, [], list_subscriptions),
        read("list_discounts", "List store discounts", {}, [], list_discounts),
        read("merchandising_brief", "Read bounded catalogue, sales and stock facts before drafting assortment or promotion recommendations", {
            "period": {"type": "string", "enum": ["7d", "30d", "90d"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        }, [], merchandising_brief, 4800),
        read("audit_product_content", "Audit product copy, imagery, pricing and storefront-page completeness without changing the catalogue", {
            "product_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 40},
        }, [], audit_product_content, 7200),
        read("get_store_settings", "Read checkout, shipping, tax and policy settings without secrets", {}, [], store_settings),
        write("update_store_policies", "Update store policy copy", {
            "values": {"type": "object", "additionalProperties": False,
                       "properties": {key: {"type": "string", "maxLength": 50000}
                                      for key in POLICY_LABELS}}},
            ["values"], "update_store_policies", validate_policies),
        write("update_checkout_settings", "Update checkout customer accounts and delivery preferences", {
            "customer_accounts_enabled": {"type": "boolean"},
            "shipping_minor": {"type": ["integer", "null"], "minimum": 0},
            "free_shipping_threshold_minor": {"type": "integer", "minimum": 0},
        }, [], "update_checkout_settings", validate_checkout_settings),
        write("update_notification_template", "Update a customer notification template", {
            "event": {"type": "string"},
            "subject": {"type": "string", "maxLength": 300},
            "body": {"type": "string", "maxLength": 50000},
            "enabled": {"type": "boolean"},
        }, ["event", "subject", "body", "enabled"],
        "update_notification_template", validate_notification_template),
        write("set_tax_rate", "Create or update a sandbox tax rate", {
            "country": {"type": "string", "minLength": 2, "maxLength": 2},
            "region": {"type": "string", "maxLength": 20},
            "name": {"type": "string", "maxLength": 100},
            "rate_bps": {"type": "integer", "minimum": 0, "maximum": 10000},
            "prices_include_tax": {"type": "boolean"},
        }, ["country", "rate_bps"], "set_tax_rate", validate_tax_rate),
        read("sales_summary", "Read sales summary for a bounded period", {"period": {"type": "string", "enum": ["7d", "30d", "90d"]}}, ["period"], sales_summary),
        read("sales_comparison", "Compare sales with the preceding matching period", {
            "period": {"type": "string", "enum": ["7d", "30d", "90d"]},
        }, ["period"], sales_comparison, 1800),
        read("fulfilment_queue", "List a bounded queue of orders awaiting fulfilment without customer details", {
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        }, [], fulfilment_queue, 1800),
        read("low_stock_variants", "List a bounded set of tracked variants at or below the low-stock threshold", {
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        }, [], low_stock_variants, 1800),
        read("integration_status", "List configured integration status and bounded safe sync metadata", {
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        }, [], integration_status, 1800),
        read("get_integration_setup_guide", "Get a protected, provider-specific setup checklist without credentials", {
            "provider": {"type": "string", "enum": ["stripe", "shopify", "woocommerce", "wordpress"]},
        }, ["provider"], integration_setup_guide, 1800),
    ]

    def validate_update_product(a):
        p = product(a["product_id"])
        fields = {k: a[k] for k in ("name", "description", "status") if k in a}
        if not fields or ("name" in fields and (not fields["name"].strip() or len(fields["name"]) > 220)):
            raise CommerceError("Provide a valid product name, description or status.")
        if fields.get("status") not in (None, "active", "draft", "archived"):
            raise CommerceError("Choose active, draft or archived status.")
        changes = []
        if "name" in fields:
            changes.append(("Product name", p.name, fields["name"].strip()))
        if "description" in fields:
            changes.append(("Product description", f"{len(p.description_html or '')} characters",
                            f"{len(fields['description'])} characters"))
        if "status" in fields:
            changes.append(("Product status", p.status, fields["status"]))
        return _with_review({"product_id": p.id, "fields": fields}, changes), _summary(
            *[_change(label, old, new) for label, old, new in changes]
        ), p.version

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
        changes = [("Price", money(listing.price_minor, currency), money(amount, currency))]
        return _with_review({"product_id": p.id, "variant_id": variant.id, "price_minor": amount,
                             "currency": currency}, changes), _summary(
            f"{p.name}", *[_change(label, old, new) for label, old, new in changes]
        ), p.version

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
        currency = db.get(Channel, site.channel_id).currency
        variant_summary = ", ".join(
            f"{variant['name']} ({money(variant['price_minor'], currency)})"
            for variant in clean_variants
        )
        changes = [
            ("Product", "not present", name.strip()),
            ("Status", "not present", requested_status),
            ("Variants", "not present", variant_summary),
        ]
        return _with_review({"name": name.strip(), "description": description,
                             "variants": clean_variants, "status": requested_status}, changes), _summary(
            f"Create product {name.strip()}", *[_change(label, old, new) for label, old, new in changes[1:]]
        ), None

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
        stock = db.scalar(select(Stock).where(
            Stock.variant_id == variant.id, Stock.warehouse_id == warehouse.id,
        ))
        before = stock.quantity if stock else 0
        after = amount if mode == "set" else before + amount
        changes = [(f"Stock at {warehouse.name}", before, after)]
        return _with_review({"variant_id": variant.id, "product_id": p.id,
                             "warehouse_id": warehouse.id, "mode": mode, "amount": amount,
                             "reason": reason, "expected_version": p.version}, changes), _summary(
            f"{p.name} ({reason})", *[_change(label, old, new) for label, old, new in changes]
        ), p.version

    def discount_payload(a, *, update_existing=False):
        from app import discount_routes

        rule = None
        before = "not present"
        if update_existing:
            rule_id = a.get("discount_id")
            rule = db.scalar(select(Discount).where(Discount.id == rule_id,
                Discount.site_id == site.id, Discount.tenant_id == site.tenant_id,
                Discount.deleted_at.is_(None)))
            if not rule:
                raise CommerceError("Discount not found.")
        old_updated = _iso(rule.updated_at) if rule else None
        if rule:
            existing_codes = list(db.scalars(select(DiscountCode.code).where(
                DiscountCode.discount_id == rule.id, DiscountCode.site_id == site.id,
                DiscountCode.tenant_id == site.tenant_id,
            )))
            before = {
                "title": rule.title,
                "type": rule.type,
                "method": rule.method,
                "value_type": rule.value_type,
                "value": rule.value,
                "codes": existing_codes,
                "active": rule.is_active,
            }
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
        after = {
            "title": fields["title"],
            "type": fields["type"],
            "method": fields["method"],
            "value_type": fields["value_type"],
            "value": fields["value"],
            "codes": codes,
            "active": fields["is_active"],
        }
        return {"discount_id": rule.id if rule else None, "fields": fields, "codes": codes,
                "expected_updated_at": old_updated, "review_before": before, "review_after": after}

    def validate_create_discount(a):
        payload = discount_payload(a)
        before, after = payload.pop("review_before"), payload.pop("review_after")
        changes = [("Discount", before, after)]
        return _with_review(payload, changes), _summary(
            f"Create discount {payload['fields']['title']}",
            *[_change(label, old, new) for label, old, new in changes]
        ), None

    def validate_update_discount(a):
        payload = discount_payload(a, update_existing=True)
        before, after = payload.pop("review_before"), payload.pop("review_after")
        changes = [("Discount", before, after)]
        return _with_review(payload, changes), _summary(
            f"Update discount {payload['fields']['title']}",
            *[_change(label, old, new) for label, old, new in changes]
        ), payload["expected_updated_at"]

    def validate_deactivate_discount(a):
        rule = db.scalar(select(Discount).where(Discount.id == a["discount_id"],
            Discount.site_id == site.id, Discount.tenant_id == site.tenant_id,
            Discount.deleted_at.is_(None)))
        if not rule:
            raise CommerceError("Discount not found.")
        changes = [("Discount status", "active" if rule.is_active else "inactive", "inactive")]
        return _with_review({"discount_id": rule.id, "expected_updated_at": _iso(rule.updated_at)}, changes), \
            _summary(f"Deactivate discount {rule.title}",
                     *[_change(label, old, new) for label, old, new in changes]), _iso(rule.updated_at)

    def validate_shipping(a):
        shipping = a.get("flat_rate_minor")
        threshold = a.get("free_shipping_threshold_minor")
        if shipping is not None and (type(shipping) is not int or not 0 <= shipping <= 1_000_000):
            raise CommerceError("Flat shipping must be between 0 and 1,000,000 minor units.")
        if type(threshold) is not int or not 0 <= threshold <= 1_000_000:
            raise CommerceError("Free shipping threshold must be between 0 and 1,000,000 minor units.")
        config = db.scalar(select(SiteCommerceSettings).where(
            SiteCommerceSettings.site_id == site.id, SiteCommerceSettings.tenant_id == site.tenant_id))
        channel = db.get(Channel, site.channel_id)
        currency = channel.currency if channel else "USD"
        before_shipping = config.shipping_minor if config else None
        before_threshold = config.free_shipping_threshold_minor if config else None
        changes = [
            ("Flat shipping", money(before_shipping, currency) if before_shipping is not None else "not set",
             money(shipping, currency) if shipping is not None else "not set"),
            ("Free shipping threshold", money(before_threshold, currency) if before_threshold is not None else "not set",
             money(threshold, currency)),
        ]
        return _with_review({"flat_rate_minor": shipping, "free_shipping_threshold_minor": threshold,
                             "settings_version": config.version if config else 1,
                             "site_version": site.version}, changes), _summary(
            *[_change(label, old, new) for label, old, new in changes]
        ), (config.version if config else 1, site.version)

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
        changes = [
            ("Fulfilment status", row.status, "fulfilled"),
            ("Shipment", "not recorded", f"{carrier} tracking {tracking}"),
        ]
        return _with_review({"order_id": link.id, "order_version": _iso(row.updated_at),
                             "quantities": quantities, "carrier": carrier,
                             "tracking_number": tracking, "notify_customer": notify}, changes), _summary(
            f"Order {row.number}", *[_change(label, old, new) for label, old, new in changes]
        ), _iso(row.updated_at)

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
        changes = [("Refundable balance", money(available, row.currency),
                    money(available - amount, row.currency))]
        return _with_review({"order_id": link.id, "order_version": _iso(row.updated_at),
                             "amount_minor": amount, "currency": row.currency, "reason": reason}, changes), \
            _summary(f"Refund {money(amount, row.currency)} {row.currency} for order {row.number}",
                     *[_change(label, old, new) for label, old, new in changes]), _iso(row.updated_at)

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
        target_state = {"cancel": "cancelled", "pause": "paused", "resume": "active"}[operation]
        changes = [("Subscription status", contract.state, target_state)]
        return _with_review({"contract_id": contract.id, "version": contract.version}, changes), \
            _summary(f"{operation.title()} subscription for {contract.recipient_name or 'customer'}",
                     *[_change(label, old, new) for label, old, new in changes]), contract.version

    def validate_customer_update(a):
        row = customer(a["customer_id"])
        tags, note = a["tags"], a["note"]
        if not isinstance(tags, list) or len(tags) > 50 or any(not isinstance(t, str) or not t.strip() or len(t) > 60 for t in tags):
            raise CommerceError("Provide up to 50 valid customer tags.")
        if len(set(t.strip() for t in tags)) != len(tags) or not isinstance(note, str) or len(note) > 5000:
            raise CommerceError("Customer tags must be unique and the note at most 5000 characters.")
        clean_tags = [t.strip() for t in tags]
        changes = [
            ("Customer tags", row.tags_json, clean_tags),
            ("Customer note", f"{len(row.note or '')} characters", f"{len(note)} characters"),
        ]
        return _with_review({"customer_id": row.id, "email": row.email, "tags": clean_tags,
                             "note": note, "expected_updated_at": _iso(row.updated_at)}, changes), \
            _summary(f"Customer {row.name or row.email}",
                     *[_change(label, old, new) for label, old, new in changes]), _iso(row.updated_at)

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
        elif action.kind == "update_store_policies":
            from app import site_builder_services as builder

            payload = action.payload_json
            site = builder.lock_site(db, site.id, user_id, payload["expected_version"])
            before = builder.snapshot(db, site)
            values = payload["values"]
            draft = copy.deepcopy(site.settings_json or {})
            policies = copy.deepcopy(draft.get("policies") or {})
            policies.update(values)
            draft["policies"] = policies
            site.settings_json = draft
            site.version += 1
            db.flush()
            builder.record_change(db, site, user_id, before, "agent", "Store policies")
            action.result_json = {"updated": sorted(values), "version": site.version}
        elif action.kind == "set_tax_rate":
            payload = action.payload_json
            row = db.scalar(select(TaxRate).where(
                TaxRate.tenant_id == site.tenant_id, TaxRate.site_id == site.id,
                TaxRate.country == payload["country"], TaxRate.region == payload["region"],
            ).with_for_update())
            expected = payload["expected_values"]
            if row and (expected is None or {
                "name": row.name,
                "rate_bps": row.rate_bps,
                "prices_include_tax": row.prices_include_tax,
            } != expected):
                raise CommerceError("This tax rate changed after the action was proposed.")
            if not row and expected is not None:
                raise CommerceError("This tax rate was removed after the action was proposed.")
            if not row:
                row = TaxRate(tenant_id=site.tenant_id, site_id=site.id,
                              country=payload["country"], region=payload["region"])
                db.add(row)
            row.name = payload["name"]
            row.rate_bps = payload["rate_bps"]
            row.prices_include_tax = payload["prices_include_tax"]
            db.flush()
            action.result_json = {"tax_rate_id": row.id, "country": row.country,
                                  "region": row.region, "rate_bps": row.rate_bps}
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
        elif action.kind == "update_checkout_settings":
            from app import commerce, site_builder_services

            payload = action.payload_json
            fields = payload["fields"]
            allowed = {
                "customer_accounts_enabled",
                "shipping_minor",
                "free_shipping_threshold_minor",
            }
            if not isinstance(fields, dict) or not fields or set(fields) - allowed:
                raise CommerceError("Checkout settings action is invalid.")
            if ("customer_accounts_enabled" in fields
                    and type(fields["customer_accounts_enabled"]) is not bool):
                raise CommerceError("Checkout settings action is invalid.")
            for field in {"shipping_minor", "free_shipping_threshold_minor"} & set(fields):
                value = fields[field]
                if field == "shipping_minor" and value is None:
                    continue
                if type(value) is not int or not 0 <= value <= 1_000_000:
                    raise CommerceError("Checkout settings action is invalid.")
            expected = payload["expected_values"]
            if not isinstance(expected, dict) or set(expected) != allowed:
                raise CommerceError("Checkout settings action is invalid.")
            site = site_builder_services.lock_site(db, site.id, user_id, payload["site_version"])
            config = commerce.settings_for(db, site, create=True)
            config = db.scalar(select(SiteCommerceSettings).where(
                SiteCommerceSettings.id == config.id,
                SiteCommerceSettings.site_id == site.id,
                SiteCommerceSettings.tenant_id == site.tenant_id,
            ).with_for_update().execution_options(populate_existing=True))
            current = {
                "customer_accounts_enabled": config.customer_accounts_enabled,
                "shipping_minor": config.shipping_minor,
                "free_shipping_threshold_minor": config.free_shipping_threshold_minor,
            }
            if config.version != payload["settings_version"] or current != expected:
                raise CommerceError("Checkout settings changed after the action was proposed.")
            for field, value in fields.items():
                setattr(config, field, value)
            config.version += 1
            site.version += 1
            action.result_json = current | {
                field: getattr(config, field)
                for field in allowed
            } | {"version": config.version}
        elif action.kind == "update_notification_template":
            from app import growth_settings

            payload = action.payload_json
            event = payload["event"]
            values = {
                "subject": payload["subject"],
                "body": payload["body"],
                "enabled": payload["enabled"],
            }
            if (not isinstance(event, str) or event not in growth_settings.EVENTS
                    or not isinstance(values["subject"], str)
                    or not isinstance(values["body"], str)
                    or type(values["enabled"]) is not bool):
                raise CommerceError("Notification template action is invalid.")
            growth_settings.render_template(values["subject"], values["body"], {
                "store_name": site.name,
                "customer_name": "Customer",
                "order_number": "ORDER-001",
                "total": "0.00",
                "tracking_number": "TRACKING",
                "action_url": "https://example.test/",
                "message": "Message",
            }, escape=False)
            template = db.scalar(select(NotificationTemplate).where(
                NotificationTemplate.tenant_id == site.tenant_id,
                NotificationTemplate.site_id == site.id,
                NotificationTemplate.event == event,
            ).with_for_update().execution_options(populate_existing=True))
            current = None if template is None else {
                "subject": template.subject,
                "body": template.body,
                "enabled": template.enabled,
            }
            expected = payload["expected_values"]
            if expected is not None and (
                    not isinstance(expected, dict) or set(expected) != set(values)):
                raise CommerceError("Notification template action is invalid.")
            if current != expected:
                raise CommerceError("Notification template changed after the action was proposed.")
            if template is None:
                template = NotificationTemplate(
                    tenant_id=site.tenant_id,
                    site_id=site.id,
                    event=event,
                )
                db.add(template)
            template.subject = values["subject"]
            template.body = values["body"]
            template.enabled = values["enabled"]
            db.flush()
            action.result_json = {"notification_template_id": template.id, "event": event, **values}
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
        elif action.kind == "save_customer_segment":
            payload = action.payload_json
            row = db.scalar(select(CustomerSegment).where(
                CustomerSegment.tenant_id == site.tenant_id,
                CustomerSegment.site_id == site.id,
                CustomerSegment.name == payload["name"],
            ))
            expected = payload["expected_existing_filters"]
            if (expected is None and row) or (expected is not None and (
                not row or row.filters_json != expected
            )):
                raise CommerceError("This customer segment changed after the action was proposed.")
            row = customer_admin.save_segment(db, site, payload["name"], payload["filters"])
            action.result_json = {"segment_id": row.id, "name": row.name,
                                  "filters": row.filters_json}
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
                    "review": (r.payload_json or {}).get("review", {"changes": []}),
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
