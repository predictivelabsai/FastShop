"""Append-only audit summaries, collected in the same transaction as admin writes.

Only object kinds and fixed action labels are recorded, never form data, notes,
passwords, credentials or complete before/after payloads.
"""

from contextlib import contextmanager

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.admin_gap_models import AdminActivity
from app.models import Membership, Site, SiteOrder, Stock, Warehouse, new_id
from app.services import CommerceError

AUDITED = {
    "Product": "product", "ProductVariant": "variant", "Collection": "collection",
    "InventoryAdjustment": "inventory", "Order": "order", "SiteOrder": "order",
    "RefundCommand": "refund", "ReturnRequest": "return", "DraftOrder": "draft",
    "ShopCustomer": "customer", "Discount": "discount", "SitePage": "page",
    "Site": "settings", "SiteCommerceSettings": "settings", "Membership": "staff",
    "StaffInvite": "staff", "Warehouse": "location", "SitePreferences": "preferences",
    "CustomDataDefinition": "custom_data", "CustomDataValue": "custom_data",
    "UrlRedirect": "redirect", "ThemeSnapshot": "theme", "GiftCard": "gift_card",
    "GiftCardEntry": "gift_card", "OrderEdit": "order", "Stock": "inventory",
    "ShippingZone": "settings", "ShippingRate": "settings", "TaxRate": "settings",
    "NotificationTemplate": "settings", "VariantChannelListing": "product",
    "CustomerAddress": "customer", "CustomerComment": "customer",
    "OrderComment": "order", "ShipmentEvent": "order", "DiscountCode": "discount",
}


@contextmanager
def activity_context(db, site, actor_id):
    previous = db.info.get("admin_activity_context")
    db.info["admin_activity_context"] = (site.id, actor_id)
    try:
        yield
    finally:
        if previous is None:
            db.info.pop("admin_activity_context", None)
        else:
            db.info["admin_activity_context"] = previous


def record_activity(db, site, actor_id, object_type, object_id, action, summary):
    row = AdminActivity(tenant_id=site.tenant_id, site_id=site.id, actor_id=actor_id,
                        object_type=object_type[:60], object_id=str(object_id)[:64],
                        action=action[:40], summary=summary[:300])
    db.add(row)
    return row


def activities_for(db, site, *, object_type="", object_id="", actor_id="", action=""):
    query = select(AdminActivity).where(AdminActivity.tenant_id == site.tenant_id,
                                         AdminActivity.site_id == site.id)
    for field, value in (("object_type", object_type), ("object_id", object_id),
                         ("actor_id", actor_id), ("action", action)):
        if value:
            query = query.where(getattr(AdminActivity, field) == value)
    return list(db.scalars(query.order_by(AdminActivity.created_at.desc(), AdminActivity.id.desc()).limit(200)))


def _context(db):
    if db.info.get("admin_activity_context"):
        return db.info["admin_activity_context"]
    from app.admin_ui import admin_scope
    scope = admin_scope.get() or {}
    parts = scope.get("path", "").strip("/").split("/")
    if len(parts) >= 3 and parts[:2] == ["admin", "sites"]:
        return parts[2], scope.get("session", {}).get("user_id")
    return None, None


@event.listens_for(Session, "before_flush")
def collect_activity(db, flush_context, instances):
    for obj in db.dirty | db.deleted:
        if isinstance(obj, AdminActivity) and (obj in db.deleted or db.is_modified(obj)):
            raise CommerceError("Activity records are append-only.")
    site_id, actor_id = _context(db)
    if not site_id or not actor_id:
        return
    site = db.get(Site, site_id)
    member = db.scalar(select(Membership.id).where(Membership.tenant_id == site.tenant_id,
        Membership.user_id == actor_id, Membership.role.in_(["admin", "merchant", "editor"]))) if site else None
    if not member:
        return
    for obj in list(db.new | db.dirty | db.deleted):
        kind = AUDITED.get(type(obj).__name__)
        if not kind or (obj in db.dirty and not db.is_modified(obj, include_collections=False)):
            continue
        tenant_id = getattr(obj, "tenant_id", None)
        if isinstance(obj, Stock):
            warehouse = db.get(Warehouse, obj.warehouse_id)
            tenant_id = warehouse.tenant_id if warehouse else None
        if type(obj).__name__ == "VariantChannelListing":
            from app.models import ProductVariant
            variant = db.get(ProductVariant, obj.variant_id)
            tenant_id = variant.tenant_id if variant and obj.channel_id == site.channel_id else None
        if tenant_id != site.tenant_id or getattr(obj, "site_id", site.id) != site.id:
            continue
        action = "deleted" if obj in db.deleted else "created" if obj in db.new else "updated"
        if type(obj).__name__ == "SitePage":
            state = inspect(obj)
            if "published_json" in state.attrs and state.attrs.published_json.history.has_changes():
                action = "published"
        if not obj.id:
            obj.id = new_id()
        identifier = getattr(obj, "order_id", None) if type(obj).__name__ == "SiteOrder" else obj.id
        if type(obj).__name__ == "VariantChannelListing":
            identifier = variant.product_id
        elif type(obj).__name__ in {"CustomerAddress", "CustomerComment"}:
            identifier = obj.customer_id
        elif type(obj).__name__ == "DiscountCode":
            identifier = obj.discount_id
        elif type(obj).__name__ in {"OrderComment", "ShipmentEvent"}:
            link = db.get(SiteOrder, obj.site_order_id)
            identifier = link.order_id if link and link.site_id == site.id else obj.id
        if type(obj).__name__ == "OrderEdit":
            link = db.get(SiteOrder, obj.site_order_id)
            identifier = link.order_id if link and link.site_id == site.id else obj.id
            action = "edited"
        elif type(obj).__name__ == "GiftCardEntry":
            identifier = obj.gift_card_id
        record_activity(db, site, actor_id, kind, identifier, action,
                        f"{kind.replace('_', ' ').capitalize()} {action}.")


@event.listens_for(AdminActivity, "before_update")
@event.listens_for(AdminActivity, "before_delete")
def immutable_activity(mapper, connection, target):
    raise CommerceError("Activity records are append-only.")


@event.listens_for(Session, "do_orm_execute")
def prevent_bulk_activity_changes(state):
    if (state.is_update or state.is_delete) and getattr(
            getattr(state.statement, "table", None), "name", "") == AdminActivity.__tablename__:
        raise CommerceError("Activity records are append-only.")
