"""Small tenant-scoped catalog editor for Phase 1 product presentation."""

import re

from fasthtml.common import H2, A, Button, Div, Form, Input, Label, P
from sqlalchemy import select
from starlette.responses import RedirectResponse

from app import content, site_builder_services
from app.db import SessionLocal
from app.models import Category, Product, ProductType, ProductVariant, VariantChannelListing
from app.services import CommerceError


def price_minor(value):
    minor = int(value)
    if not 0 <= minor <= 100_000_000:
        raise CommerceError("Enter a valid price in cents.")
    return minor


def register_catalog_routes(rt, actor, csrf, check_csrf, shell, error):
    @rt("/admin/sites/{site_id}/products", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                products = list(db.scalars(select(Product).where(Product.tenant_id == site.tenant_id)))
                cards = []
                for product in products:
                    variants = list(db.scalars(select(ProductVariant).where(ProductVariant.product_id == product.id, ProductVariant.tenant_id == site.tenant_id).order_by(ProductVariant.sort_order)))
                    fields = []
                    for variant in variants:
                        price = db.scalar(select(VariantChannelListing).where(VariantChannelListing.variant_id == variant.id, VariantChannelListing.channel_id == site.channel_id))
                        fields.append(Div(Label("Variant name", Input(name=f"variant_{variant.id}", value=variant.name)),
                            Label("USD price in cents (blank if unconfirmed)", Input(name=f"price_{variant.id}", type="number", min="0", step="1", value=price.price_minor if price else ""))))
                    cards.append(Div(H2(product.name), Form(csrf(session), Input(type="hidden", name="product_id", value=product.id),
                        Label("Product name", Input(name="name", value=product.name, required=True)),
                        Label("Image URL", Input(name="image", value=product.image_url)), *fields,
                        Button("Save catalog details", cls="e-button"), method="post", cls="e-form"), cls="e-card"))
                return shell("Products", A("← Site", href=f"/admin/sites/{site.id}"), P("Catalog changes update product displays immediately. Checkout remains closed in Phase 1."),
                    Div(*cards, cls="e-grid"), H2("Add a product"), Form(csrf(session),
                        Label("Name", Input(name="name", required=True, maxlength=220)),
                        Label("URL slug", Input(name="slug", required=True, pattern="[a-z0-9-]+")),
                        Label("Image URL", Input(name="image")), Label("Variant names, separated by commas", Input(name="variants", value="Original")),
                        Label("USD price in cents", Input(name="price", type="number", min="0", step="1")),
                        Button("Create product and draft page", cls="e-button"), method="post", cls="e-form"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/products", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                site = site_builder_services.lock_site(db, site.id, actor(session), site.version)
                name = str(form.get("name", "")).strip()
                if not name or len(name) > 220:
                    raise CommerceError("Enter a product name under 220 characters.")
                image_url = content.safe_url(str(form.get("image", "")), media=True)
                content.validate_media_ownership(db, site, {"image": image_url})
                if form.get("product_id"):
                    product = content.catalog_product(db, site, str(form["product_id"]))
                    if not product:
                        raise CommerceError("Product not found.")
                    product.name, product.image_url = name, image_url
                    variants = list(db.scalars(select(ProductVariant).where(ProductVariant.product_id == product.id, ProductVariant.tenant_id == site.tenant_id)))
                    for variant in variants:
                        variant.name = str(form.get(f"variant_{variant.id}", variant.name)).strip()[:180]
                        value = str(form.get(f"price_{variant.id}", "")).strip()
                        listing = db.scalar(select(VariantChannelListing).where(VariantChannelListing.variant_id == variant.id, VariantChannelListing.channel_id == site.channel_id))
                        if not value:
                            if listing:
                                db.delete(listing)
                        elif listing:
                            listing.price_minor = price_minor(value)
                        else:
                            db.add(VariantChannelListing(variant_id=variant.id, channel_id=site.channel_id, currency="USD", price_minor=price_minor(value)))
                else:
                    slug = str(form.get("slug", "")).strip()
                    if not re.fullmatch(r"[a-z0-9-]{1,100}", slug):
                        raise CommerceError("Use lowercase letters, numbers and hyphens in the slug.")
                    if db.scalar(select(Product.id).where(Product.tenant_id == site.tenant_id, Product.slug == slug)):
                        raise CommerceError("That product slug is already in use.")
                    category = db.scalar(select(Category).where(Category.tenant_id == site.tenant_id))
                    if not category:
                        category = Category(tenant_id=site.tenant_id, slug="collection", name="Our collection")
                        db.add(category)
                        db.flush()
                    product_type = db.scalar(select(ProductType).where(ProductType.tenant_id == site.tenant_id))
                    if not product_type:
                        product_type = ProductType(tenant_id=site.tenant_id, slug="physical", name="Physical product")
                        db.add(product_type)
                        db.flush()
                    product = Product(tenant_id=site.tenant_id, product_type_id=product_type.id, category_id=category.id,
                        slug=slug, name=name, image_url=image_url, is_published=True)
                    db.add(product)
                    db.flush()
                    names = [n.strip() for n in str(form.get("variants", "Original")).split(",") if n.strip()]
                    if not 1 <= len(names) <= 30:
                        raise CommerceError("Enter between one and thirty variants.")
                    for i, variant_name in enumerate(names):
                        variant = ProductVariant(tenant_id=site.tenant_id, product_id=product.id, sku=f"{slug}-{i}", name=variant_name[:180], sort_order=i)
                        db.add(variant)
                        db.flush()
                        if form.get("price"):
                            db.add(VariantChannelListing(variant_id=variant.id, channel_id=site.channel_id, currency="USD", price_minor=price_minor(form["price"])))
                    page = content.create_page(db, site, name, "/products/" + slug, "product", {"title": name, "sections": [{"type": "product", "heading": name, "image": image_url, "body": "Tell your product's story."}]})
                    page.product_id = product.id
                site.version += 1
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/products", status_code=303)
        except (CommerceError, ValueError) as exc:
            return error(exc)
