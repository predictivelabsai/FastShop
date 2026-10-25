"""Spreadsheet edits reuse catalogue commands and their transactional invariants."""
import hashlib
import json
from collections import defaultdict

from sqlalchemy import select

from app import catalog_admin as catalog
from app.models import Product, ProductVariant, Stock, VariantChannelListing, Warehouse
from app.services import CommerceError


def rows_for(db, site, product_ids=None):
    products = select(Product).where(Product.tenant_id == site.tenant_id)
    if product_ids is not None:
        products = products.where(Product.id.in_(product_ids))
    rows = []
    warehouses = list(db.scalars(select(Warehouse).where(
        Warehouse.tenant_id == site.tenant_id, Warehouse.is_active.is_(True))
        .order_by(Warehouse.name, Warehouse.id)))
    for product in db.scalars(products.order_by(Product.name, Product.id).limit(200)):
        for variant in db.scalars(select(ProductVariant).where(
            ProductVariant.tenant_id == site.tenant_id, ProductVariant.product_id == product.id,
            ProductVariant.is_active.is_(True)).order_by(ProductVariant.sort_order, ProductVariant.id)):
            listing = db.scalar(select(VariantChannelListing).where(
                VariantChannelListing.channel_id == site.channel_id,
                VariantChannelListing.variant_id == variant.id))
            stocks = {s.warehouse_id: s for s in db.scalars(select(Stock).where(
                Stock.variant_id == variant.id, Stock.warehouse_id.in_([w.id for w in warehouses])))}
            row = {"id": variant.id, "product_id": product.id, "product": product.name,
                "variant": variant.name, "product_version": product.version,
                "price_minor": listing.price_minor if listing else 0,
                "compare_at_minor": listing.compare_at_minor if listing else None,
                "cost_minor": listing.cost_minor if listing else 0,
                "sku": variant.sku, "barcode": variant.barcode,
                "status": product.status, "track_inventory": variant.track_inventory,
                "quantities": {w.id: stocks[w.id].quantity if w.id in stocks else 0
                               for w in warehouses},
                "allocated": {w.id: stocks[w.id].allocated if w.id in stocks else 0
                              for w in warehouses}}
            row["version"] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
            rows.append(row)
    return rows, [{"id": w.id, "name": w.name} for w in warehouses]


def save_rows(db, site, rows, user_id):
    """Save valid product groups, returning errors against every rejected row.

    Product groups are atomic because status/version belong to the product. All
    stock locks are obtained before processing groups, in stable Stock.id order.
    Fingerprints include stock and allocations to detect concurrent inventory work.
    """
    if not isinstance(rows, list) or not rows or len(rows) > 500:
        raise CommerceError("Select between 1 and 500 variant rows.")
    if any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in rows):
        raise CommerceError("Send valid variant rows.")
    if len({r["id"] for r in rows}) != len(rows):
        raise CommerceError("Each variant may appear only once.")
    catalog._ensure_transaction(db)
    ids = [r["id"] for r in rows]
    variants = list(db.scalars(select(ProductVariant).where(
        ProductVariant.tenant_id == site.tenant_id, ProductVariant.id.in_(ids))))
    product_ids = sorted({v.product_id for v in variants})
    list(db.scalars(select(Product).where(Product.tenant_id == site.tenant_id,
        Product.id.in_(product_ids)).order_by(Product.id).with_for_update()))
    list(db.scalars(select(Stock).join(ProductVariant).where(
        ProductVariant.tenant_id == site.tenant_id, ProductVariant.product_id.in_(product_ids))
        .order_by(Stock.id).with_for_update().execution_options(populate_existing=True)))
    current, _ = rows_for(db, site, product_ids)
    by_id = {r["id"]: r for r in current}
    groups, errors, saved = defaultdict(list), {}, []
    for row in rows:
        original = by_id.get(row["id"])
        if not original:
            errors[row["id"]] = "Variant not found."
        else:
            groups[original["product_id"]].append(row)
    for product_id, group in sorted(groups.items()):
        try:
            with db.begin_nested():
                for row in group:
                    if row.get("version") != by_id[row["id"]]["version"]:
                        raise CommerceError("This row changed. Reload before saving your changes.")
                    if "track_inventory" in row and not isinstance(row["track_inventory"], bool):
                        raise CommerceError("Track inventory must be true or false.")
                    if "quantities" in row and not isinstance(row["quantities"], dict):
                        raise CommerceError("Supply stock quantities by location.")
                    amounts = [row[k] for k in ("price_minor", "compare_at_minor", "cost_minor")
                               if k in row and row[k] is not None]
                    amounts += list(row.get("quantities", {}).values())
                    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in amounts):
                        raise CommerceError("Prices and stock must be non-negative whole numbers.")
                statuses = {r["status"] for r in group if "status" in r}
                if len(statuses) > 1:
                    raise CommerceError("Choose the same status for all variants of a product.")
                changes = {"variants": [{key: value for key, value in row.items()
                    if key in {"id", "price_minor", "compare_at_minor", "cost_minor", "sku",
                               "barcode", "track_inventory", "quantities"}} for row in group]}
                if statuses:
                    changes["status"] = next(iter(statuses))
                catalog.update_product(db, site, product_id, changes,
                    expected_version=by_id[group[0]["id"]]["product_version"], user_id=user_id)
            saved.extend(r["id"] for r in group)
        except (CommerceError, ValueError, TypeError) as exc:
            errors.update({r["id"]: str(exc) for r in group})
    refreshed, locations = rows_for(db, site, product_ids)
    return {"saved": saved, "errors": errors, "rows": refreshed, "locations": locations}
