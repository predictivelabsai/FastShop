"""Per-person index views. Stored filters are allowlisted, never executable queries."""

from urllib.parse import urlencode

from sqlalchemy import select

from app.admin_gap_models import AdminTablePreference, SavedAdminView
from app.services import CommerceError

COLUMNS = {
    "products": ["Product", "Status", "Inventory", "Type", "Vendor"],
    "orders": ["Order", "Date", "Customer", "Total", "Payment", "Fulfilment", "Items", "Delivery", "Tags"],
    "customers": ["Customer name", "Email", "Email consent", "Location", "Orders", "Amount spent"],
}
FILTERS = {
    "products": {"q", "status", "vendor", "product_type", "tag", "collection", "stock"},
    "orders": {"view", "q", "payment", "fulfilment", "start", "end", "tag", "channel"},
    "customers": {"q", "segment", "tag", "location", "orders_min", "orders_max", "spent_min", "spent_max", "consent"},
}


def resource_name(resource):
    if resource not in COLUMNS:
        raise CommerceError("Choose products, orders or customers.")
    return resource


def clean_columns(resource, columns):
    resource_name(resource)
    result = list(dict.fromkeys(str(c) for c in columns if c in COLUMNS[resource]))
    if not result:
        raise CommerceError("Keep at least one column visible.")
    return result


def views_for(db, site, user_id, resource):
    resource_name(resource)
    return list(db.scalars(select(SavedAdminView).where(
        SavedAdminView.tenant_id == site.tenant_id, SavedAdminView.site_id == site.id,
        SavedAdminView.user_id == user_id, SavedAdminView.resource == resource).order_by(SavedAdminView.name)))


def view_for(db, site, user_id, resource, view_id):
    row = next((r for r in views_for(db, site, user_id, resource) if r.id == view_id), None)
    if not row:
        raise CommerceError("Saved view not found.")
    return row


def save_view(db, site, user_id, resource, name, filters, sort, columns, view_id=None):
    resource_name(resource)
    name = str(name).strip()
    if not name or len(name) > 80:
        raise CommerceError("Enter a view name of up to 80 characters.")
    row = view_for(db, site, user_id, resource, view_id) if view_id else SavedAdminView(
        tenant_id=site.tenant_id, site_id=site.id, user_id=user_id, resource=resource)
    if any(v.name == name and v.id != row.id for v in views_for(db, site, user_id, resource)):
        raise CommerceError("A view with this name already exists.")
    row.name = name
    row.filters_json = {key: str(value)[:300] for key, value in filters.items()
                        if key in FILTERS[resource] and value}
    row.sort = str(sort)[:40]
    row.columns_json = clean_columns(resource, columns)
    db.add(row)
    db.flush()
    return row


def view_url(site, view):
    return f"/admin/sites/{site.id}/{view.resource}?" + urlencode({
        **view.filters_json, "sort": view.sort, "saved_view": view.id})


def columns_for(db, site, user_id, resource, view_id=""):
    if view_id:
        return view_for(db, site, user_id, resource, view_id).columns_json
    row = db.scalar(select(AdminTablePreference).where(
        AdminTablePreference.site_id == site.id, AdminTablePreference.tenant_id == site.tenant_id,
        AdminTablePreference.user_id == user_id, AdminTablePreference.resource == resource))
    return row.columns_json if row else COLUMNS[resource]


def save_columns(db, site, user_id, resource, columns):
    columns = clean_columns(resource, columns)
    row = db.scalar(select(AdminTablePreference).where(
        AdminTablePreference.site_id == site.id, AdminTablePreference.tenant_id == site.tenant_id,
        AdminTablePreference.user_id == user_id, AdminTablePreference.resource == resource))
    if not row:
        row = AdminTablePreference(tenant_id=site.tenant_id, site_id=site.id,
                                  user_id=user_id, resource=resource)
        db.add(row)
    row.columns_json = columns
    db.flush()
    return row
