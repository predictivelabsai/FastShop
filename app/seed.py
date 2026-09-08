"""Deterministic demonstration catalog for local and first production boot."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Attribute,
    AttributeValue,
    Category,
    Channel,
    Membership,
    Product,
    ProductType,
    ProductVariant,
    Review,
    Stock,
    Tenant,
    User,
    VariantChannelListing,
    Voucher,
    Warehouse,
)

TENANT_SLUG = "fastshop-demo"
CHANNEL_SLUG = "europe"

PRODUCTS = (
    {
        "slug": "harbour-runner",
        "name": "Harbour Runner",
        "subtitle": "Everyday trainers built for long city miles.",
        "category": "footwear",
        "image": "https://images.unsplash.com/photo-1542291026-7eec264c27ff?auto=format&fit=crop&w=1200&q=85",
        "price": 8900,
        "compare": 11900,
        "featured": True,
        "variants": (("HR-BLU-41", "Blue / 41", {"Colour": "Ocean", "Size": "41"}), ("HR-BLU-42", "Blue / 42", {"Colour": "Ocean", "Size": "42"})),
    },
    {
        "slug": "field-overshirt",
        "name": "Field Overshirt",
        "subtitle": "A soft organic-cotton layer for shifting weather.",
        "category": "apparel",
        "image": "https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?auto=format&fit=crop&w=1200&q=85",
        "price": 7200,
        "compare": None,
        "featured": True,
        "variants": (("FO-SGE-M", "Sage / M", {"Colour": "Sage", "Size": "M"}), ("FO-SGE-L", "Sage / L", {"Colour": "Sage", "Size": "L"})),
    },
    {
        "slug": "studio-headphones",
        "name": "Studio Headphones",
        "subtitle": "Balanced sound, calm design, thirty-hour battery.",
        "category": "technology",
        "image": "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=1200&q=85",
        "price": 14900,
        "compare": 17900,
        "featured": True,
        "variants": (("SH-SND", "Sand", {"Colour": "Sand"}), ("SH-BLK", "Black", {"Colour": "Black"})),
    },
    {
        "slug": "signal-watch",
        "name": "Signal Watch",
        "subtitle": "A precise, understated watch for work and weekends.",
        "category": "accessories",
        "image": "https://images.unsplash.com/photo-1523275335684-37898b6baf30?auto=format&fit=crop&w=1200&q=85",
        "price": 12900,
        "compare": None,
        "featured": True,
        "variants": (("SW-GRY", "Grey", {"Colour": "Grey"}),),
    },
    {
        "slug": "morning-mug",
        "name": "Morning Mug",
        "subtitle": "Hand-finished stoneware with a generous handle.",
        "category": "home",
        "image": "https://images.unsplash.com/photo-1514228742587-6b1558fcca3d?auto=format&fit=crop&w=1200&q=85",
        "price": 2400,
        "compare": None,
        "featured": False,
        "variants": (("MM-CRM", "Cream", {"Colour": "Cream"}),),
    },
    {
        "slug": "commuter-pack",
        "name": "Commuter Pack",
        "subtitle": "Weather-ready organisation for a laptop and daily carry.",
        "category": "accessories",
        "image": "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?auto=format&fit=crop&w=1200&q=85",
        "price": 9800,
        "compare": None,
        "featured": False,
        "variants": (("CP-BLK", "Black", {"Colour": "Black"}), ("CP-OLV", "Olive", {"Colour": "Olive"})),
    },
)


def seed(session: Session, admin_email: str = "admin@fastshop.example") -> Tenant:
    existing = session.scalar(select(Tenant).where(Tenant.slug == TENANT_SLUG))
    if existing:
        return existing

    tenant = Tenant(slug=TENANT_SLUG, name="Northstar Goods")
    session.add(tenant)
    session.flush()
    channel = Channel(
        tenant_id=tenant.id,
        slug=CHANNEL_SLUG,
        name="Europe",
        currency="EUR",
        country_code="EE",
        locale="en",
        prices_include_tax=True,
    )
    product_type = ProductType(
        tenant_id=tenant.id, slug="physical", name="Physical product", is_shipping_required=True
    )
    warehouse = Warehouse(
        tenant_id=tenant.id, code="TLL", name="Tallinn fulfilment centre", country_code="EE"
    )
    admin = User(email=admin_email.lower(), name="FastShop Admin")
    session.add_all([channel, product_type, warehouse, admin])
    session.flush()
    session.add(Membership(tenant_id=tenant.id, user_id=admin.id, role="admin"))

    categories: dict[str, Category] = {}
    labels = {
        "footwear": "Footwear",
        "apparel": "Apparel",
        "technology": "Technology",
        "accessories": "Accessories",
        "home": "Home",
    }
    for order, (slug, name) in enumerate(labels.items()):
        category = Category(
            tenant_id=tenant.id,
            slug=slug,
            name=name,
            description=f"Considered {name.lower()} for modern work and everyday life.",
            sort_order=order,
        )
        session.add(category)
        categories[slug] = category
    session.flush()

    colour = Attribute(tenant_id=tenant.id, slug="colour", name="Colour", input_type="swatch")
    size = Attribute(tenant_id=tenant.id, slug="size", name="Size", input_type="dropdown")
    session.add_all([colour, size])
    session.flush()
    for attribute, values in ((colour, ("Ocean", "Sage", "Sand", "Black", "Grey", "Cream", "Olive")), (size, ("M", "L", "41", "42"))):
        for value in values:
            session.add(
                AttributeValue(
                    attribute_id=attribute.id,
                    slug=value.lower(),
                    name=value,
                    value=value,
                )
            )

    for index, item in enumerate(PRODUCTS):
        product = Product(
            tenant_id=tenant.id,
            product_type_id=product_type.id,
            category_id=categories[item["category"]].id,
            slug=item["slug"],
            name=item["name"],
            subtitle=item["subtitle"],
            description=(
                f"{item['subtitle']} Selected for durable materials, practical details, "
                "and a clear supply chain. Includes a 30-day return window."
            ),
            seo_title=f"{item['name']} — Northstar Goods",
            seo_description=item["subtitle"],
            image_url=item["image"],
            is_published=True,
            is_featured=item["featured"],
        )
        session.add(product)
        session.flush()
        for variant_index, (sku, name, attributes) in enumerate(item["variants"]):
            variant = ProductVariant(
                tenant_id=tenant.id,
                product_id=product.id,
                sku=sku,
                name=name,
                attributes_json=attributes,
                weight_grams=700,
                sort_order=variant_index,
            )
            session.add(variant)
            session.flush()
            session.add_all(
                [
                    VariantChannelListing(
                        variant_id=variant.id,
                        channel_id=channel.id,
                        currency=channel.currency,
                        price_minor=item["price"],
                        compare_at_minor=item["compare"],
                        cost_minor=item["price"] // 2,
                    ),
                    Stock(
                        warehouse_id=warehouse.id,
                        variant_id=variant.id,
                        quantity=12 + index * 3 + variant_index,
                        allocated=0,
                    ),
                ]
            )

    session.add_all(
        [
            Voucher(
                tenant_id=tenant.id,
                code="WELCOME10",
                kind="percentage",
                value=10,
                minimum_minor=5000,
                usage_limit=1000,
            ),
            Review(
                tenant_id=tenant.id,
                product_id=session.scalar(select(Product.id).where(Product.slug == "harbour-runner")),
                author_name="Marta",
                rating=5,
                title="Comfort from the first day",
                body="Light, supportive, and the colour is even better in person.",
                is_approved=True,
            ),
        ]
    )
    session.flush()
    return tenant

