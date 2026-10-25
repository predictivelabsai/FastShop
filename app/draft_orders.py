"""Tenant-owned merchant drafts and genuine checkout records.

Drafts do not reserve stock until completion and do not expose payment tokens.
"""
from __future__ import annotations

from sqlalchemy import func, or_, select

from app import checkout_services as checkout
from app import commerce, customer_services
from app.catalog_admin import _atomic
from app.models import (
    Channel,
    CheckoutAttempt,
    CommerceQuote,
    DraftOrder,
    DraftOrderLine,
    Product,
    ProductVariant,
    ShopCustomer,
    VariantChannelListing,
    Warehouse,
)
from app.services import CommerceError


def owned(db, site, draft_id, *, editable=False):
    row = db.scalar(select(DraftOrder).where(DraftOrder.id == draft_id,
        DraftOrder.tenant_id == site.tenant_id, DraftOrder.site_id == site.id).with_for_update())
    if not row:
        raise CommerceError("Draft order not found.")
    if editable and row.status == "completed":
        raise CommerceError("Completed drafts cannot be edited.")
    return row


def lines_for(db, site, draft):
    return list(db.scalars(select(DraftOrderLine).where(
        DraftOrderLine.draft_order_id == draft.id, DraftOrderLine.tenant_id == site.tenant_id,
        DraftOrderLine.site_id == site.id).order_by(DraftOrderLine.created_at, DraftOrderLine.id)))


def integer(value, label, *, minimum=0, maximum=1_000_000_000):
    if isinstance(value, bool):
        raise CommerceError(f"Enter a valid {label}.")
    try:
        result = int(str(value))
    except (ValueError, TypeError) as exc:
        raise CommerceError(f"Enter a valid {label}.") from exc
    if not minimum <= result <= maximum:
        raise CommerceError(f"Enter a valid {label}.")
    return result


@_atomic
def create(db, site):
    row = DraftOrder(tenant_id=site.tenant_id, site_id=site.id, currency=currency_for(db, site))
    db.add(row)
    db.flush()
    return row


def check_version(draft, version):
    if draft.version != integer(version, "version", minimum=1):
        raise CommerceError("This draft changed. Reload before saving.")


def customer(db, site, customer_id):
    row = db.scalar(select(ShopCustomer).where(ShopCustomer.id == customer_id,
        ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id))
    if not row:
        raise CommerceError("Customer not found.")
    return row


@_atomic
def save(db, site, draft_id, values, *, version):
    with db.begin_nested():
        checkout.lock_site(db, site)
        draft = owned(db, site, draft_id, editable=True)
        check_version(draft, version)
        selected = str(values.get("customer_id", ""))
        if values.get("customer_email"):
            selected = customer_services.customer_for(db, site, values["customer_email"], create=True).id
            person = customer(db, site, selected)
            if values.get("customer_name") and not person.name:
                person.name = str(values["customer_name"]).strip()[:160]
        draft.customer_id = customer(db, site, selected).id if selected else None
        draft.discount_minor = integer(values.get("discount_minor", 0), "discount")
        draft.shipping_minor = integer(values.get("shipping_minor", 0), "shipping amount")
        draft.note = str(values.get("note", "")).strip()[:2000]
        destination = {key: str(values.get(key, "")).strip()[:200]
            for key in ("name", "line1", "line2", "city", "state", "postal_code", "country")}
        destination["country"] = destination["country"].upper()
        if destination["country"] and len(destination["country"]) != 2:
            raise CommerceError("Use a two-letter country code.")
        draft.destination_json = destination
        for line in lines_for(db, site, draft):
            key = "quantity_" + line.id
            if key in values:
                line.quantity = integer(values[key], "quantity", minimum=1, maximum=999)
        draft.version += 1
        draft.status = "open"
        db.flush()
        return draft


def currency_for(db, site):
    currency = db.scalar(select(Channel.currency).where(Channel.id == site.channel_id,
        Channel.tenant_id == site.tenant_id))
    if not currency:
        raise CommerceError("Store currency is not configured.")
    return currency


def variants(db, site, q=""):
    pattern = "%" + q[:100].replace("%", "\\%").replace("_", "\\_") + "%"
    return list(db.execute(select(ProductVariant, Product, VariantChannelListing)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(VariantChannelListing, VariantChannelListing.variant_id == ProductVariant.id)
        .where(ProductVariant.tenant_id == site.tenant_id, Product.tenant_id == site.tenant_id,
            ProductVariant.is_active.is_(True), Product.status != "archived",
            VariantChannelListing.channel_id == site.channel_id,
            VariantChannelListing.currency == currency_for(db, site), or_(Product.name.ilike(pattern, escape="\\"),
                ProductVariant.sku.ilike(pattern, escape="\\")))
        .order_by(Product.name, ProductVariant.id).limit(40)))


@_atomic
def add_line(db, site, draft_id, values, *, version):
    with db.begin_nested():
        checkout.lock_site(db, site)
        draft = owned(db, site, draft_id, editable=True)
        check_version(draft, version)
        if len(lines_for(db, site, draft)) >= 50:
            raise CommerceError("Drafts support at most fifty lines.")
        quantity = integer(values.get("quantity", 1), "quantity", minimum=1, maximum=999)
        variant_id = str(values.get("variant_id", ""))
        if variant_id:
            row = next((row for row in variants(db, site) if row[0].id == variant_id), None)
            # Search is capped for the picker, so look up the chosen scoped variant directly.
            if not row:
                row = db.execute(select(ProductVariant, Product, VariantChannelListing)
                    .join(Product, Product.id == ProductVariant.product_id)
                    .join(VariantChannelListing, VariantChannelListing.variant_id == ProductVariant.id)
                    .where(ProductVariant.id == variant_id, ProductVariant.tenant_id == site.tenant_id,
                        Product.tenant_id == site.tenant_id, ProductVariant.is_active.is_(True),
                        Product.status != "archived", VariantChannelListing.channel_id == site.channel_id,
                        VariantChannelListing.currency == draft.currency)).first()
            if not row:
                raise CommerceError("Product option is unavailable for this store.")
            variant, product, listing = row
            title, sku, unit = product.name + " · " + variant.name, variant.sku, listing.price_minor
        else:
            title = str(values.get("title", "")).strip()
            if not title or len(title) > 220:
                raise CommerceError("Enter a custom item title of up to 220 characters.")
            sku = str(values.get("sku", "")).strip()[:100]
            unit = integer(values.get("unit_price_minor", 0), "item price")
        row = DraftOrderLine(tenant_id=site.tenant_id, site_id=site.id,
            draft_order_id=draft.id, variant_id=variant_id or None, title=title[:220], sku=sku,
            quantity=quantity, unit_price_minor=unit)
        db.add(row)
        draft.version += 1
        draft.status = "open"
        db.flush()
        return row


@_atomic
def remove_line(db, site, draft_id, line_id, *, version):
    with db.begin_nested():
        checkout.lock_site(db, site)
        draft = owned(db, site, draft_id, editable=True)
        check_version(draft, version)
        line = next((line for line in lines_for(db, site, draft) if line.id == line_id), None)
        if not line:
            raise CommerceError("Draft line not found.")
        db.delete(line)
        draft.version += 1
        draft.status = "open"
        db.flush()


def totals(db, site, draft):
    subtotal = sum(line.quantity * line.unit_price_minor for line in lines_for(db, site, draft))
    if draft.discount_minor > subtotal:
        raise CommerceError("The discount cannot exceed the merchandise subtotal.")
    return subtotal, subtotal - draft.discount_minor + draft.shipping_minor


@_atomic
def invoice(db, site, draft_id, *, version):
    with db.begin_nested():
        checkout.lock_site(db, site)
        draft = owned(db, site, draft_id, editable=True)
        check_version(draft, version)
        if not lines_for(db, site, draft) or not draft.customer_id:
            raise CommerceError("Add items and choose a customer before sending an invoice.")
        totals(db, site, draft)
        person = customer(db, site, draft.customer_id)
        if not person.is_active:
            raise CommerceError("Choose an active customer.")
        draft.status = "invoice_sent"
        draft.version += 1
        mail = customer_services.queue_mail(db, site, person, "draft_invoice",
            f"draft-invoice:{site.id}:{draft.id}:{draft.version}",
            reference={"draft_id": draft.id, "version": draft.version})
        db.flush()
        return mail


@_atomic
def complete(db, site, draft_id, *, mark_as_paid=False):
    with db.begin_nested():
        checkout.lock_site(db, site)
        draft = owned(db, site, draft_id)
        if draft.status == "completed":
            return draft
        if mark_as_paid is not True:
            raise CommerceError("Confirm that payment was received outside FastShop.")
        rows = lines_for(db, site, draft)
        if not rows or not draft.customer_id:
            raise CommerceError("Add items and choose a customer before completing this draft.")
        person = customer(db, site, draft.customer_id)
        subtotal, total = totals(db, site, draft)
        amounts = [line.quantity * line.unit_price_minor for line in rows]
        discounts = [draft.discount_minor * amount // (subtotal or 1) for amount in amounts]
        for i in range(draft.discount_minor - sum(discounts)):
            discounts[i] += 1
        snapshot_lines = []
        for i, line in enumerate(rows):
            variant = db.scalar(select(ProductVariant).where(ProductVariant.id == line.variant_id,
                ProductVariant.tenant_id == site.tenant_id)) if line.variant_id else None
            listing = db.scalar(select(VariantChannelListing.id).join(ProductVariant,
                ProductVariant.id == VariantChannelListing.variant_id).join(Product,
                Product.id == ProductVariant.product_id).where(
                    ProductVariant.id == line.variant_id, ProductVariant.tenant_id == site.tenant_id,
                    Product.tenant_id == site.tenant_id, Product.status != "archived",
                    VariantChannelListing.channel_id == site.channel_id,
                    VariantChannelListing.currency == draft.currency)) if line.variant_id else None
            if line.variant_id and (not variant or not variant.is_active or not listing):
                raise CommerceError("A draft product option is no longer available.")
            snapshot_lines.append({"variant_id": line.variant_id, "name": line.title,
                "variant_name": "", "sku": line.sku, "quantity": line.quantity,
                "unit_minor": line.unit_price_minor, "amount_minor": amounts[i]-discounts[i],
                "subscription": False, "track_inventory": bool(variant and variant.track_inventory),
                "continue_selling_when_out_of_stock": bool(variant and variant.continue_selling_when_out_of_stock)})
        config = commerce.settings_for(db, site)
        origin = dict(config.origin_json or {}) if config else {}
        if not origin.get("country"):
            origin["country"] = db.scalar(select(Warehouse.country_code).where(
                Warehouse.tenant_id == site.tenant_id, Warehouse.is_active.is_(True),
                Warehouse.fulfils_online_orders.is_(True)).order_by(Warehouse.is_default.desc(), Warehouse.id).limit(1)) or ""
        if any(line["track_inventory"] for line in snapshot_lines) and not origin["country"]:
            raise CommerceError("Configure a fulfilment origin or inventory location before completing this draft.")
        snapshot = {"lines": snapshot_lines, "destination": draft.destination_json or {},
            "recipient_name": (draft.destination_json or {}).get("name") or person.name, "origin": origin,
            "manual_payment": True, "tax_review": "No automatic tax calculation; merchant reviewed."}
        link = checkout.complete_manual_order(db, site, person.id, snapshot,
            request_key="draft-"+draft.id, currency=draft.currency, subtotal_minor=subtotal,
            discount_minor=draft.discount_minor, shipping_minor=draft.shipping_minor)
        link.note = draft.note
        draft.status, draft.order_id, draft.site_order_id = "completed", link.order_id, link.id
        draft.version += 1
        db.flush()
        return draft


def list_drafts(db, site, *, q="", status="", page=1):
    query = select(DraftOrder).where(DraftOrder.tenant_id == site.tenant_id, DraftOrder.site_id == site.id)
    if status in {"open", "invoice_sent", "completed"}:
        query = query.where(DraftOrder.status == status)
    if q:
        pattern = "%"+q[:100].replace("%", "\\%").replace("_", "\\_")+"%"
        ids = select(ShopCustomer.id).where(ShopCustomer.site_id == site.id,
            ShopCustomer.tenant_id == site.tenant_id, or_(ShopCustomer.email.ilike(pattern, escape="\\"),
                ShopCustomer.name.ilike(pattern, escape="\\")))
        query = query.where(or_(DraftOrder.id.ilike(pattern, escape="\\"), DraftOrder.customer_id.in_(ids)))
    count = db.scalar(select(func.count()).select_from(query.subquery()))
    return list(db.scalars(query.order_by(DraftOrder.updated_at.desc(), DraftOrder.id)
        .offset((page-1)*25).limit(25))), count


def abandoned(db, site):
    return list(db.execute(select(CheckoutAttempt, CommerceQuote, ShopCustomer)
        .join(CommerceQuote, CommerceQuote.id == CheckoutAttempt.quote_id)
        .join(ShopCustomer, ShopCustomer.id == CheckoutAttempt.customer_id)
        .where(CheckoutAttempt.site_id == site.id, CheckoutAttempt.tenant_id == site.tenant_id,
            CommerceQuote.site_id == site.id, CommerceQuote.tenant_id == site.tenant_id,
            ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id,
            CheckoutAttempt.state.in_(checkout.ACTIVE)).order_by(CheckoutAttempt.created_at.desc()).limit(100)))
