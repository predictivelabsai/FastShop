"""Tenant locations with safe deactivation and stable inventory locks."""

from sqlalchemy import func, select, update

from app.catalog_admin import _atomic
from app.models import Stock, Tenant, Warehouse, new_id
from app.services import CommerceError


def locations_for(db, site):
    return list(db.scalars(select(Warehouse).where(Warehouse.tenant_id == site.tenant_id)
                          .order_by(Warehouse.is_default.desc(), Warehouse.name, Warehouse.id)))


def location_for(db, site, location_id):
    row = db.scalar(select(Warehouse).where(Warehouse.tenant_id == site.tenant_id,
                                           Warehouse.id == location_id).with_for_update())
    if not row:
        raise CommerceError("Location not found.")
    return row


@_atomic
def save_location(db, site, data, location_id=None):
    # Serialise default changes, including two simultaneous first locations.
    db.execute(update(Tenant).where(Tenant.id == site.tenant_id)
               .values(updated_at=Tenant.updated_at))
    row = location_for(db, site, location_id) if location_id else Warehouse(
        tenant_id=site.tenant_id, code=new_id()[:12])
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 140:
        raise CommerceError("Enter a location name of up to 140 characters.")
    active = bool(data.get("is_active"))
    fulfilling = bool(data.get("fulfils_online_orders"))
    default = bool(data.get("is_default"))
    if default and (not active or not fulfilling):
        raise CommerceError("The default location must be active and fulfil online orders.")
    if row.id and (not active or not fulfilling):
        stocks = list(db.scalars(select(Stock).where(Stock.warehouse_id == row.id)
                                 .order_by(Stock.id).with_for_update()))
        if any(s.allocated > 0 for s in stocks):
            raise CommerceError("Move or release committed stock before deactivating this location.")
    if row.is_default and not default:
        raise CommerceError("Set another active location as default first.")
    if default:
        for other in locations_for(db, site):
            if other.id != row.id:
                other.is_default = False
    row.name, row.is_active, row.fulfils_online_orders, row.is_default = name, active, fulfilling, default
    row.country_code = str(data.get("country_code", "US")).strip().upper()
    if len(row.country_code) != 2 or not row.country_code.isalpha():
        raise CommerceError("Enter a two-letter country code.")
    row.address_json = {key: str(data.get(key, ""))[:240] for key in ("address", "city", "region", "postal_code")}
    db.add(row)
    db.flush()
    return row


@_atomic
def delete_location(db, site, location_id):
    row = location_for(db, site, location_id)
    if row.is_default:
        raise CommerceError("Choose another default location before deleting this one.")
    if db.scalar(select(func.count()).select_from(Stock).where(Stock.warehouse_id == row.id)):
        raise CommerceError("This location has inventory history. Deactivate it instead.")
    db.delete(row)
    db.flush()
