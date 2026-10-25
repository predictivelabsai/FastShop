"""Authorised catalogue routes within the merchant admin shell."""
import json
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from urllib.parse import urlencode

from fasthtml.common import A, Button, Form, Input, P
from sqlalchemy import func, or_, select
from starlette.responses import RedirectResponse, Response

from app import catalog_admin as catalog
from app import catalog_admin_ui as ui
from app import content
from app.content_gap_routes import custom_data_card
from app.db import SessionLocal
from app.models import (
    CatalogueSettings,
    Channel,
    Collection,
    CollectionProduct,
    InventoryAdjustment,
    Product,
    ProductImage,
    ProductOption,
    ProductOptionValue,
    ProductTag,
    ProductVariant,
    SiteMedia,
    Stock,
    VariantChannelListing,
    Warehouse,
    new_id,
)
from app.services import CommerceError
from app.site_media import MAX_IMAGE_BYTES, media_for, prepare_image_bytes


def minor(value):
    try:
        amount = Decimal(str(value or "0")) * 100
        if not amount.is_finite() or amount != amount.to_integral_value() or amount < 0:
            raise ValueError
        return int(amount)
    except (ValueError, InvalidOperation) as exc:
        raise CommerceError("Enter a non-negative amount with at most two decimal places.") from exc


def json_field(form, name, default):
    try:
        value = json.loads(str(form.get(name, json.dumps(default))))
    except (TypeError, ValueError) as exc:
        raise CommerceError("Invalid catalogue form data. Reload and try again.") from exc
    if not isinstance(value, type(default)):
        raise CommerceError("Invalid catalogue form data.")
    return value


def products_for(db, site, filters):
    query = select(Product).where(Product.tenant_id == site.tenant_id)
    status = filters.get("status", "")
    if status in {"active", "draft", "archived"}:
        query = query.where(Product.status == status)
    if filters.get("q"):
        term = filters["q"][:120].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = "%" + term + "%"
        variant_ids = select(ProductVariant.product_id).where(
            ProductVariant.tenant_id == site.tenant_id,
            ProductVariant.sku.ilike(pattern, escape="\\"))
        query = query.where(or_(Product.name.ilike(pattern, escape="\\"),
                               Product.slug.ilike(pattern, escape="\\"), Product.id.in_(variant_ids)))
    for key in ("vendor", "product_type"):
        if filters.get(key):
            query = query.where(getattr(Product, key) == filters[key])
    if filters.get("tag"):
        query = query.where(Product.id.in_(select(ProductTag.product_id).where(
            ProductTag.tenant_id == site.tenant_id, ProductTag.tag == filters["tag"])))
    ordering = {"title": Product.name.asc(), "title_desc": Product.name.desc(),
                "created": Product.created_at.desc()}.get(filters.get("sort"), Product.updated_at.desc())
    products = list(db.scalars(query.order_by(ordering, Product.id)))
    quantities = dict(db.execute(select(ProductVariant.product_id,
        func.coalesce(func.sum(Stock.quantity - Stock.allocated), 0))
        .outerjoin(Stock, Stock.variant_id == ProductVariant.id)
        .outerjoin(Warehouse, Warehouse.id == Stock.warehouse_id)
        .where(ProductVariant.tenant_id == site.tenant_id, ProductVariant.is_active.is_(True),
               or_(Warehouse.id.is_(None), Warehouse.is_active.is_(True) & Warehouse.fulfils_online_orders.is_(True)))
        .group_by(ProductVariant.product_id)).all())
    settings = db.scalar(select(CatalogueSettings).where(
        CatalogueSettings.tenant_id == site.tenant_id, CatalogueSettings.site_id == site.id))
    threshold = settings.low_stock_threshold if settings else 5
    collection_ids = None
    if filters.get("collection"):
        collection = db.scalar(select(Collection).where(Collection.id == filters["collection"],
            Collection.tenant_id == site.tenant_id))
        if not collection:
            return []
        collection_ids = {p.id for p in catalog.collection_products(db, site, collection)}
    return [(p, quantities.get(p.id, 0)) for p in products
            if (collection_ids is None or p.id in collection_ids)
            and (filters.get("stock") != "out" or quantities.get(p.id, 0) <= 0)
            and (filters.get("stock") != "low" or quantities.get(p.id, 0) <= threshold)]


def collections_for(db, site):
    return list(db.scalars(select(Collection).where(Collection.tenant_id == site.tenant_id).order_by(Collection.title)))


def variants_for(db, site, product):
    rows = []
    for v in db.scalars(select(ProductVariant).where(ProductVariant.tenant_id == site.tenant_id,
        ProductVariant.product_id == product.id, ProductVariant.is_active.is_(True)).order_by(ProductVariant.sort_order, ProductVariant.id)):
        listing = db.scalar(select(VariantChannelListing).where(
            VariantChannelListing.variant_id == v.id,
            VariantChannelListing.channel_id == site.channel_id))
        rows.append({"id": v.id, "name": v.name, "attributes_json": v.attributes_json,
            "sku": v.sku, "barcode": v.barcode, "track_inventory": v.track_inventory,
            "continue_selling_when_out_of_stock": v.continue_selling_when_out_of_stock,
            "image_id": v.image_id, "price_minor": listing.price_minor if listing else 0,
            "compare_at_minor": listing.compare_at_minor if listing else None,
            "cost_minor": listing.cost_minor if listing else 0,
            "quantities": {s.warehouse_id: s.quantity for s in db.scalars(
                select(Stock).join(Warehouse).where(Stock.variant_id == v.id,
                    Warehouse.tenant_id == site.tenant_id, Warehouse.is_active.is_(True)))}})
    return rows


def register_catalog_admin_routes(rt, actor, csrf, check_csrf, shell, error):
    def redirect(site_id, path="products", notice=""):
        return RedirectResponse(f"/admin/sites/{site_id}/{path}" +
            ("?" + urlencode({"notice": notice}) if notice else ""), status_code=303)

    def render_editor(db, session, site, product):
        variants, options, images, selected = [], [], [], []
        previous = following = None
        if product:
            variants = variants_for(db, site, product)
            for option in db.scalars(select(ProductOption).where(
                    ProductOption.tenant_id == site.tenant_id,
                    ProductOption.product_id == product.id).order_by(ProductOption.position)):
                values = list(db.scalars(select(ProductOptionValue.value).where(
                    ProductOptionValue.option_id == option.id).order_by(ProductOptionValue.position)))
                options.append({"name": option.name, "values": values})
            images = list(db.scalars(select(ProductImage).where(
                ProductImage.tenant_id == site.tenant_id,
                ProductImage.product_id == product.id).order_by(ProductImage.position)))
            if not images and product.image_url:
                images = [SimpleNamespace(id=None, image_url=product.image_url,
                                           alt=product.name, media_id=None)]
            selected = list(db.scalars(select(CollectionProduct.collection_id).where(
                CollectionProduct.product_id == product.id, CollectionProduct.tenant_id == site.tenant_id)))
            ids = list(db.scalars(select(Product.id).where(Product.tenant_id == site.tenant_id)
                .order_by(Product.name, Product.id)))
            index = ids.index(product.id)
            previous = ids[index - 1] if index else None
            following = ids[index + 1] if index < len(ids) - 1 else None
        warehouses = list(db.scalars(select(Warehouse).where(
            Warehouse.tenant_id == site.tenant_id, Warehouse.is_active.is_(True)).order_by(Warehouse.name)))
        if not warehouses and not db.scalar(select(Warehouse.id).where(
                Warehouse.tenant_id == site.tenant_id).limit(1)):
            warehouses = [SimpleNamespace(id="default", name="Default location")]
        return shell("Edit product" if product else "Add product", ui.product_editor(
            site, product, variants, options, images, media_for(db, site),
            collections_for(db, site), selected, warehouses, csrf(session), previous, following,
            currency=db.get(Channel, site.channel_id).currency),
            custom_data_card(db, site, "product", product.id, csrf(session)) if product else None,
            *([custom_data_card(db, site, "variant", variant["id"], csrf(session))
               for variant in variants if variant.get("id")] if product else []))

    @rt("/admin/sites/{site_id}/products", methods=["GET"])
    def get(session, site_id: str, q: str = "", status: str = "", vendor: str = "",
            product_type: str = "", tag: str = "", collection: str = "", stock: str = "",
            sort: str = "updated", page: int = 1, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                filters = dict(q=q, status=status, vendor=vendor, product_type=product_type,
                               tag=tag, collection=collection, stock=stock, sort=sort)
                rows = products_for(db, site, filters)
                all_rows = products_for(db, site, {})
                counts = {"all": len(all_rows), "active": sum(p.status == "active" for p, _ in all_rows),
                          "draft": sum(p.status == "draft" for p, _ in all_rows),
                          "low": len(products_for(db, site, {"stock": "low"}))}
                page = max(1, min(page, max(1, (len(rows) + 24) // 25)))
                return shell("Products", P(notice[:300], role="status") if notice else None,
                    ui.product_index(site, rows[(page - 1) * 25:page * 25], len(rows), counts,
                                     filters, page, csrf(session), collections_for(db, site)))
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/products/new", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                return render_editor(db, session, site, None)
        except CommerceError as exc:
            return error(exc)

    async def save_product(session, request, site_id, product_id=None):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                keys = ("name", "slug", "description_html", "short_description", "seo_title",
                        "seo_description", "vendor", "product_type", "template", "country_of_origin",
                        "hs_code", "tax_code", "status")
                data = {k: str(form.get(k, "")) for k in keys}
                data.update(create_redirect=form.get("create_redirect", "on") == "on", tags_json=[t.strip() for t in str(form.get("tags", "")).split(",") if t.strip()],
                    is_published=form.get("published") == "on", charge_tax=form.get("charge_tax") == "on", requires_shipping=form.get("requires_shipping") == "on",
                    weight_grams=int(form.get("weight_grams") or 0),
                    dimensions_json={k: int(form.get("dimension_" + k) or 0) for k in ("length", "width", "height")},
                    options=json_field(form, "options_json", []),
                    variants=json_field(form, "variants_json", []),
                    images=json_field(form, "images_json", []), collection_ids=form.getlist("collection_ids"))
                if not data["slug"]:
                    import re
                    data["slug"] = re.sub(r"[^a-z0-9]+", "-", data["name"].lower()).strip("-")
                warehouses = list(db.scalars(select(Warehouse).where(
                    Warehouse.tenant_id == site.tenant_id, Warehouse.is_active.is_(True))))
                if not warehouses and not db.scalar(select(Warehouse.id).where(
                        Warehouse.tenant_id == site.tenant_id).limit(1)):
                    warehouse = Warehouse(tenant_id=site.tenant_id, code="DEFAULT", name="Default location", is_default=True)
                    db.add(warehouse)
                    db.flush()
                    warehouses = [warehouse]
                    for variant in data["variants"]:
                        quantities = variant.get("quantities", {})
                        if "default" in quantities:
                            quantities[warehouse.id] = quantities.pop("default")
                if not data["variants"]:
                    data["variants"] = [{"sku": str(form.get("sku", "")), "barcode": str(form.get("barcode", "")),
                        "price_minor": minor(form.get("price")), "compare_at_minor": minor(form.get("compare_at_price")) or None,
                        "cost_minor": minor(form.get("cost")), "track_inventory": form.get("track_inventory") == "on",
                        "continue_selling_when_out_of_stock": form.get("continue_selling_when_out_of_stock") == "on",
                        "quantities": {w.id: int(form.get("quantity_" + w.id) or form.get("quantity_default") or 0) for w in warehouses}}]
                elif not data["options"] and len(data["variants"]) == 1:
                    data["variants"][0].update(sku=str(form.get("sku", "")), barcode=str(form.get("barcode", "")),
                        price_minor=minor(form.get("price")), compare_at_minor=minor(form.get("compare_at_price")) or None,
                        cost_minor=minor(form.get("cost")), track_inventory=form.get("track_inventory") == "on",
                        continue_selling_when_out_of_stock=form.get("continue_selling_when_out_of_stock") == "on",
                        quantities={w.id: int(form.get("quantity_" + w.id) or form.get("quantity_default") or 0) for w in warehouses})
                for variant in data["variants"]:
                    if not variant.get("sku"):
                        variant.pop("sku", None)
                for upload in form.getlist("images_upload"):
                    if not hasattr(upload, "read") or not upload.filename:
                        continue
                    image_data = prepare_image_bytes(await upload.read(MAX_IMAGE_BYTES + 1))
                    media = SiteMedia(tenant_id=site.tenant_id, site_id=site.id, title=upload.filename[:200],
                        alt=data["name"][:400], storage_key=new_id() + ".webp", content_type="image/webp",
                        size=len(image_data), data=image_data, is_placeholder=False)
                    db.add(media)
                    db.flush()
                    data["images"].append({"media_id": media.id, "image_url": f"/site-media/{site.id}/{media.id}", "alt": media.alt})
                if product_id:
                    product = catalog.update_product(db, site, product_id, data,
                        expected_version=int(form.get("version", 0)), user_id=user_id)
                else:
                    product = catalog.create_product(db, site, data, user_id=user_id)
                db.commit()
                return redirect(site_id, f"products/{product.id}", "Product saved")
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/products/new", methods=["POST"])
    async def post(session, request, site_id: str):
        return await save_product(session, request, site_id)

    @rt("/admin/sites/{site_id}/products/bulk", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                if form.get("action") == "bulk_edit":
                    selected = list(db.scalars(select(Product.id).where(
                        Product.tenant_id == site.tenant_id,
                        Product.id.in_(form.getlist("product_ids")))))
                    return RedirectResponse(f"/admin/sites/{site.id}/products/bulk-edit?" +
                        urlencode({"ids": ",".join(selected)}), status_code=303)
                catalog.bulk_products(db, site, form.getlist("product_ids"), str(form.get("action", "")),
                    user_id=user_id, value=str(form.get("value_collection", "")) if form.get("action") == "add_to_collection" else str(form.get("value", "")))
                db.commit()
            return redirect(site_id, notice="Products updated")
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/products/export", methods=["GET"])
    def get(session, request, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                rows = products_for(db, site, dict(request.query_params))
                data = catalog.export_csv(db, site, [p for p, _ in rows])
                return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="products.csv"', "Cache-Control": "no-store"})
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/products/import", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                content.owned_site(db, site_id, actor(session), publish=True)
            return shell("Import products", *ui.assets(), ui.page_header("Import products", back_href=f"/admin/sites/{site_id}/products"),
                ui.card(P("Upload a Shopify-compatible CSV. Review row errors before importing."),
                Form(csrf(session), ui.field("CSV file", "file", kind="file", accept=".csv", required=""),
                     Button("Preview import", cls="a-button a-button-primary"), method="post", enctype="multipart/form-data")))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/products/import", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            upload = form.get("file")
            if upload and hasattr(upload, "read"):
                raw = await upload.read(5 * 1024 * 1024 + 1)
                if len(raw) > 5 * 1024 * 1024:
                    raise CommerceError("CSV files must be smaller than 5 MB.")
                text = raw.decode("utf-8-sig")
            else:
                text = str(form.get("csv_text", ""))
            if len(text) > 5 * 1024 * 1024:
                raise CommerceError("CSV files must be smaller than 5 MB.")
            confirm = form.get("confirm") == "yes"
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                result = catalog.import_csv(db, site, text, user_id=user_id, dry_run=not confirm)
                if confirm:
                    db.commit()
                return shell("Import preview", *ui.assets(), ui.page_header("Import complete" if confirm else "Import preview", back_href=f"/admin/sites/{site_id}/products"),
                    ui.import_preview(result), Form(csrf(session), ui.area("CSV data", "csv_text", text, hidden=""),
                        Input(type="hidden", name="confirm", value="yes"), Button("Import products", cls="a-button a-button-primary"), method="post") if not confirm else A("View products", href=f"/admin/sites/{site_id}/products"))
        except (CommerceError, ValueError, UnicodeError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/products/{product_id}", methods=["GET"])
    def get(session, site_id: str, product_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                product = content.catalog_product(db, site, product_id)
                if not product:
                    raise CommerceError("Product not found.")
                return render_editor(db, session, site, product)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/products/{product_id}", methods=["POST"])
    async def post(session, request, site_id: str, product_id: str):
        return await save_product(session, request, site_id, product_id)

    @rt("/admin/sites/{site_id}/products/{product_id}/actions", methods=["POST"])
    async def post(session, request, site_id: str, product_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                action = str(form.get("action"))
                if action == "duplicate":
                    product = catalog.duplicate_product(db, site, product_id, user_id=user_id)
                    path = f"products/{product.id}"
                else:
                    if action == "archive":
                        catalog.archive_product(db, site, product_id, expected_version=int(form.get("version", 0)), user_id=user_id)
                    elif action == "delete":
                        catalog.delete_product(db, site, product_id, expected_version=int(form.get("version", 0)), user_id=user_id)
                    else:
                        raise CommerceError("Choose a supported product action.")
                    path = "products"
                db.commit()
            return redirect(site_id, path, "Product updated")
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/collections", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                rows = [(c, len(catalog.collection_products(db, site, c))) for c in collections_for(db, site)]
                return shell("Collections", ui.collection_index(site, rows))
        except CommerceError as exc:
            return error(exc)

    def render_collection(db, session, site, collection):
        products = list(db.scalars(select(Product).where(Product.tenant_id == site.tenant_id).order_by(Product.name)))
        selected = [p.id for p in catalog.collection_products(db, site, collection)] if collection else []
        return shell("Edit collection" if collection else "Create collection", ui.collection_editor(
            site, collection, products, selected, csrf(session), len(selected)),
            custom_data_card(db, site, "collection", collection.id, csrf(session)) if collection else None)

    @rt("/admin/sites/{site_id}/collections/new", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                return render_collection(db, session, site, None)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/collections/preview", methods=["POST"])
    async def post(session, request, site_id: str):
        from starlette.responses import JSONResponse
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                collection = Collection(tenant_id=site.tenant_id,
                    title="Preview", handle="preview", kind="automated",
                    rules_json=json_field(form, "rules_json", {}))
                rows = catalog.collection_products(db, site, collection)
                return JSONResponse({"count": len(rows), "products": [{"id": p.id, "title": p.name} for p in rows]})
        except (CommerceError, ValueError) as exc:
            return error(exc)

    async def save_collection(session, request, site_id, collection_id=None):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                data = {k: str(form.get(k, "")) for k in ("title", "handle", "description", "image_url", "seo_title", "seo_description", "kind", "sort_order")}
                data.update(create_redirect=form.get("create_redirect", "on") == "on", is_published=form.get("is_published") == "on",
                    product_ids=json_field(form, "product_ids_json", []), rules_json=json_field(form, "rules_json", {}))
                if not data["handle"]:
                    import re
                    data["handle"] = re.sub(r"[^a-z0-9]+", "-", data["title"].lower()).strip("-")
                collection = catalog.save_collection(db, site, data, collection_id=collection_id,
                    expected_version=int(form.get("version", 0)) if collection_id else None)
                db.commit()
                return redirect(site_id, f"collections/{collection.id}", "Collection saved")
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/collections/new", methods=["POST"])
    async def post(session, request, site_id: str):
        return await save_collection(session, request, site_id)

    @rt("/admin/sites/{site_id}/collections/{collection_id}", methods=["GET"])
    def get(session, site_id: str, collection_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                collection = db.scalar(select(Collection).where(Collection.id == collection_id,
                    Collection.tenant_id == site.tenant_id))
                if not collection:
                    raise CommerceError("Collection not found.")
                return render_collection(db, session, site, collection)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/collections/{collection_id}", methods=["POST"])
    async def post(session, request, site_id: str, collection_id: str):
        return await save_collection(session, request, site_id, collection_id)

    @rt("/admin/sites/{site_id}/inventory", methods=["GET"])
    def get(session, site_id: str, q: str = "", low_stock: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                warehouses = list(db.scalars(select(Warehouse).where(Warehouse.tenant_id == site.tenant_id, Warehouse.is_active.is_(True)).order_by(Warehouse.name)))
                settings = db.scalar(select(CatalogueSettings).where(CatalogueSettings.tenant_id == site.tenant_id, CatalogueSettings.site_id == site.id))
                threshold = settings.low_stock_threshold if settings else 5
                rows = []
                for p, v in db.execute(select(Product, ProductVariant).join(ProductVariant,
                    ProductVariant.product_id == Product.id).where(Product.tenant_id == site.tenant_id,
                    ProductVariant.tenant_id == site.tenant_id, ProductVariant.is_active.is_(True)).order_by(Product.name, ProductVariant.sort_order)):
                    if q and q.casefold() not in (p.name + " " + v.name + " " + v.sku).casefold():
                        continue
                    stocks = {s.warehouse_id: (s.quantity, s.allocated) for s in db.scalars(select(Stock).join(Warehouse).where(Stock.variant_id == v.id,
                    Warehouse.tenant_id == site.tenant_id, Warehouse.is_active.is_(True)))}
                    if low_stock and sum(quantity - allocated for quantity, allocated in stocks.values()) > threshold:
                        continue
                    rows.append((p, v, stocks))
                history = list(db.execute(select(InventoryAdjustment, ProductVariant.name).join(ProductVariant,
                    InventoryAdjustment.variant_id == ProductVariant.id).where(
                    InventoryAdjustment.tenant_id == site.tenant_id)
                    .order_by(InventoryAdjustment.created_at.desc(), InventoryAdjustment.id.desc()).limit(100)))
                return shell("Inventory", ui.inventory_page(site, rows, warehouses, history, csrf(session), threshold, q, bool(low_stock)))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/inventory", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                catalog.adjust_inventory(db, site, str(form.get("variant_id", "")),
                    str(form.get("warehouse_id", "")), int(form.get("quantity", 0)),
                    mode=str(form.get("mode", "adjust")), reason=str(form.get("reason", "correction")),
                    note=str(form.get("note", "")), user_id=user_id)
                db.commit()
            return redirect(site_id, "inventory", "Inventory adjusted")
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/inventory/settings", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                threshold = int(form.get("threshold", 5))
                if not 0 <= threshold <= 1_000_000:
                    raise CommerceError("Enter a threshold between 0 and 1000000.")
                settings = db.scalar(select(CatalogueSettings).where(CatalogueSettings.tenant_id == site.tenant_id, CatalogueSettings.site_id == site.id))
                if settings is None:
                    settings = CatalogueSettings(tenant_id=site.tenant_id, site_id=site.id)
                    db.add(settings)
                settings.low_stock_threshold = threshold
                db.commit()
            return redirect(site_id, "inventory", "Threshold saved")
        except (CommerceError, ValueError) as exc:
            return error(exc)
