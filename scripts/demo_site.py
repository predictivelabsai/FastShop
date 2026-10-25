"""Explicit fictional editorial fixture for local examples and regression tests.

This fixture is never provisioned by application startup.
"""

from __future__ import annotations

import copy

from sqlalchemy import delete, select

from app.content import create_page, create_site, site_pages
from app.models import (
    Category,
    Product,
    ProductType,
    ProductVariant,
    Site,
    SiteMenu,
    Tenant,
    User,
    VariantChannelListing,
)

ASSET = "/static/sample-placeholder.webp"
ARTICLES = [
    {"slug": slug, "title": title, "category": "Guides",
     "parts": [("Start here", "Compare the materials, read the instructions and choose what suits your day."),
               ("Check the details", "Our fictional examples illustrate an editorial layout. Confirm product information before publishing.")]}
    for slug, title in [("choosing-everyday-goods", "Choosing everyday goods"),
                        ("caring-for-your-bottle", "Caring for your bottle"),
                        ("reading-product-labels", "Reading product labels")]
]


def seed_demo_site(db, admin_email):
    """Create the sample once; preserve edits on subsequent calls."""
    existing = db.scalar(select(Site).where(Site.slug == "northstar"))
    if existing:
        return existing
    owner = db.scalar(select(User).where(User.email == admin_email))
    if owner is None:
        owner = User(email=admin_email, name="Sample owner")
        db.add(owner)
        db.flush()
    site = create_site(db, owner.id, "Northstar Goods", "northstar")
    db.get(Tenant, site.tenant_id).plan = "pro"
    site.status = "preview"
    config = copy.deepcopy(site.settings_json)
    config.update(email="hello@northstar.example.test", address="PLACEHOLDER: company address",
                  socials=[{"label": "Instagram", "url": ""}, {"label": "Facebook", "url": ""}],
                  claims=[{"key": "daily", "text": "Supports everyday wellbeing.*", "approved": False}],
                  facts=[{"value": "2", "label": "sample collections"}],
                  navigation=[{"label": label, "path": path} for label, path in
                              [("Shop", "/shop"), ("Materials", "/pages/science"),
                               ("Learn", "/blogs/learn"), ("About us", "/pages/about-us"),
                               ("Contact", "/pages/contact")]])
    site.settings_json = config
    site.published_settings_json = copy.deepcopy(config)

    def section(kind, heading="", body="", **kwargs):
        return {"type": kind, "heading": heading, "body": body, **kwargs}

    def page(path, title, kind, sections, **metadata):
        document = {"title": title, "description": "A fictional Northstar Goods example.",
                    "sections": sections, **metadata}
        if kind == "article":
            document["blog"] = {"state": "published"}
        existing = next((p for p in site_pages(db, site) if p.path == path), None)
        if existing:
            from app.content import validate_document
            existing.draft_json = validate_document(document)
            return existing
        return create_page(db, site, title, path, kind, document)

    page("/", "Goods for everyday life", "home", [
        section("hero", "Goods for everyday life", "Useful things, thoughtfully chosen.", image=ASSET),
        section("products", "Explore the collection"), section("facts", "At a glance"),
        section("reviews", "Customer stories", "Sample layout awaiting real reviews."),
        section("articles", "The journal")])
    product_type = ProductType(tenant_id=site.tenant_id, slug="physical", name="Physical product")
    db.add(product_type)
    db.flush()
    for slug, name, category_slug, category_name, price in [
        ("daily-vitamins", "Daily vitamins", "daily-vitamins", "Daily Vitamins", 2495),
        ("trail-bottle", "Trail Bottle", "bottles", "Bottles", None),
    ]:
        category = Category(tenant_id=site.tenant_id, slug=category_slug, name=category_name)
        db.add(category)
        db.flush()
        product = Product(tenant_id=site.tenant_id, product_type_id=product_type.id,
                          category_id=category.id, slug=slug, name=name, image_url=ASSET, is_published=True)
        db.add(product)
        db.flush()
        variant = ProductVariant(tenant_id=site.tenant_id, product_id=product.id,
                                 sku="SAMPLE-" + slug, name="Original")
        db.add(variant)
        db.flush()
        if price is not None:
            db.add(VariantChannelListing(variant_id=variant.id, channel_id=site.channel_id,
                                         currency="USD", price_minor=price))
        product_page = page("/products/" + slug, name, "product", [
            section("product", name, "Fictional product for local demonstrations.", image=ASSET,
                    subscription_preview=price is not None),
            section("faq", "Product details", items=[{"heading": "Care", "body": "Follow the supplied label."}]),
            section("split", "Made for your day", image=ASSET), section("claims", "Product statements"),
            section("reviews", "Customer stories")])
        product_page.product_id = product.id
        page("/collections/" + category_slug, category_name, "collection",
             [section("products", category_name, category=category_slug)])
    page("/pages/science", "Materials and information", "science", [
        section("text", "Read the source", "Check product information before making a decision."),
        section("research", "Reference examples", items=[{"heading": "Product information", "body": "Fictional reference.", "url": "https://example.test/materials"}])])
    page("/blogs/learn", "The journal", "blog", [section("articles", "Guides")])
    for article in ARTICLES:
        page("/blogs/learn/" + article["slug"], article["title"], "article",
             [section("text", h, b) for h, b in article["parts"]] +
             [section("references", "References", items=[{"heading": "Sample source", "url": "https://example.test/guide"}])],
             category=article["category"], image=ASSET)
    for item in site_pages(db, site):
        item.published_json = copy.deepcopy(item.draft_json)
    from app.site_blog import backfill_categories
    from app.site_media import backfill_media
    from app.site_menus import adapt_navigation
    db.execute(delete(SiteMenu).where(SiteMenu.site_id == site.id))
    adapt_navigation(db, site)
    backfill_media(db, site)
    backfill_categories(db, site)
    db.flush()
    return site
