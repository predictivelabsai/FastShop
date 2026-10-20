"""Independent, insert-only Liquid Metal preview fixture for M1."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from sqlalchemy import select

from app.commerce import settings_for
from app.content import create_page
from app.models import (
    Channel,
    Membership,
    ProductVariant,
    Site,
    SiteMenu,
    Stock,
    Tenant,
    User,
    Warehouse,
)
from app.site_catalog import create_catalog_product

ASSETS = "/static/h24you/liquid/"
CONTENT = json.loads(Path(__file__).with_name("site_liquid_content.json").read_text("utf-8"))


def block(kind, identity, **values):
    return {"id": identity, "type": kind, "version": 1, **values}


def documents():
    c = CONTENT
    metal, shop = c["metalContent"], c["shopContent"]
    product_items = []
    for key, slug, image, alt in [
        ("tablets", "hydrogen-tablets", "tablets.jpg", "A hydrogen tablet dissolving in water"),
        (
            "bottle",
            "h2-4-you-one",
            "bottle-drink.jpg",
            "placeholder: silver hydrogen water bottle held in a person’s hands",
        ),
    ]:
        p = c["products"][key]
        bottle = c["bottleProduct"] if key == "bottle" else p
        product_items.append(
            {
                "theme": "product",
                "heading": bottle["name"],
                "label": bottle["subtitle"],
                "body": p["blurb"],
                "url": "/products/" + slug,
                "image": ASSETS + image,
                "alt": alt,
                "value": p["priceLabel"],
            }
        )
    product_items.extend(
        {"theme": "spec", "label": s["label"], "value": s["value"]} for s in metal["bottleSpecs"]
    )
    product_items.extend(
        {"theme": "flavor", "label": f["label"], "value": f["id"]}
        for f in c["products"]["tablets"]["flavors"]
    )
    home = [
        block(
            "liquid_hero",
            "hero",
            heading=metal["headline"],
            body=metal["subheadline"],
            image=ASSETS + "metal-water-hero.jpg",
            alt="",
            button="Shop",
            link="/#products",
        ),
        block(
            "liquid_stats",
            "science",
            body="".join(
                s if isinstance(s, str) else s["italic"] for s in metal["science"]["source"]
            ),
            items=[
                {
                    "label": s["label"],
                    "value": (f"{s['value']:,}" if s["value"] == 2000 else str(s["value"]))
                    + s["suffix"],
                    "body": s.get("note", ""),
                }
                for s in metal["science"]["counters"]
            ],
        ),
        block(
            "liquid_collection",
            "products",
            heading="The collection",
            image=ASSETS + "bottle-drink.jpg",
            alt="placeholder: lifestyle photo, person holding hydrogen water bottle",
            button="Shop all",
            link="/shop",
            items=product_items,
        ),
        block(
            "liquid_benefits",
            "why",
            heading=metal["benefitsTitle"],
            body=c["fdaDisclaimer"],
            items=[
                {"heading": b["title"], "body": b["text"], "theme": b["icon"]}
                for b in metal["benefits"]
            ],
        ),
        block(
            "liquid_reviews",
            "reviews",
            heading=metal["reviewsTitle"],
            items=[
                {
                    "heading": r["headline"],
                    "body": r["text"],
                    "label": r["name"],
                    "value": str(r["rating"]),
                    "theme": r["location"],
                    "alt": r["product"],
                    "image": ASSETS + f"review-avatar-{i + 1}.jpg",
                }
                for i, r in enumerate(c["metalReviews"])
            ],
        ),
        block(
            "liquid_articles",
            "learn",
            heading=c["learnSection"]["heading"],
            body=c["learnSection"]["intro"],
            items=[
                {
                    "heading": a["title"],
                    "label": a["category"],
                    "url": a["href"] + ("#" + a["hash"] if a.get("hash") else ""),
                    "image": ASSETS
                    + {
                        "learn-1": "learn-1.jpg",
                        "learn-2": "tablets.jpg",
                        "learn-3": "learn-3.jpg",
                    }[a["image"]],
                    "alt": a["alt"],
                }
                for a in c["articles"]
            ],
        ),
    ]
    cards = []
    for card in shop["cards"]:
        cards.append(
            {
                "heading": card["name"],
                "label": card["label"],
                "body": card["description"],
                "url": card["href"],
                "theme": card["badge"],
                "value": card.get("priceLabel", "$95.20"),
                "image": ASSETS
                + ("bottle-desk.jpg" if card["id"] == "one" else "tablet-box-unflavoured.jpg"),
                "alt": "placeholder: " + card["name"],
            }
        )
    catalog = [
        block(
            "text",
            "shop-heading",
            layout="liquid-shop-heading",
            heading=shop["title"],
            eyebrow=shop["home"],
            body=shop["intro"],
        ),
        block("liquid_catalog", "catalog", items=cards),
        block(
            "liquid_choices",
            "choices",
            heading=shop["startTitle"],
            items=[
                {"heading": v["title"], "body": v["text"], "label": v["product"], "url": v["href"]}
                for v in shop["choices"]
            ],
        ),
        block("liquid_trust", "trust", items=[{"label": v} for v in shop["trust"]]),
    ]
    return [("/", "Your water, upgraded.", "home", home), ("/shop", "Shop", "content", catalog)]


def seed_h24you_liquid(db, admin_email):
    """Never refresh existing documents, catalogue, commerce or merchant settings."""
    site = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
    if site:
        return site
    tenant = Tenant(slug="h24you-liquid", name="H2 | 4 YOU Liquid")
    db.add(tenant)
    db.flush()
    admin = db.scalar(select(User).where(User.email == admin_email))
    if admin:
        db.add(Membership(tenant_id=tenant.id, user_id=admin.id, role="admin"))
    channel = Channel(
        tenant_id=tenant.id,
        slug="us",
        name="United States",
        currency="USD",
        country_code="US",
        locale="en",
        prices_include_tax=False,
    )
    db.add(channel)
    db.flush()
    config = {
        "theme_pack": "liquid",
        "default_locale": "en",
        "logo": ASSETS + "h24you_must.svg",
        "logo_light": ASSETS + "h24you_valge.svg",
        "footer": CONTENT["fdaDisclaimer"],
        "liquid_shell": {key: copy.deepcopy(CONTENT[key]) for key in ("offer", "footer", "nav")},
        "email": CONTENT["metalContent"]["contactEmail"],
        "sandbox_notes": "Shipping, stock, tax categories and origin are test fixtures.",
    }
    site = Site(
        tenant_id=tenant.id,
        channel_id=channel.id,
        slug="h24you-liquid",
        name="H2 | 4 YOU",
        status="preview",
        settings_json=config,
        published_settings_json=copy.deepcopy(config),
    )
    db.add(site)
    db.flush()
    products = []
    for index, (slug, price, variants) in enumerate(
        [
            (
                "hydrogen-tablets",
                2800,
                [f["label"] for f in CONTENT["products"]["tablets"]["flavors"]],
            ),
            ("h2-4-you-one", 25000, ["Silver"]),
            ("taste-the-range", 9520, ["Unflavored, Raspberry, Pineapple, Peach"]),
        ]
    ):
        card = CONTENT["shopContent"]["cards"][index]
        product = create_catalog_product(
            db,
            site,
            {
                "name": card["name"],
                "slug": slug,
                "subtitle": card["label"],
                "description": card["description"],
                "image_url": ASSETS
                + ("bottle-desk.jpg" if index == 1 else "tablet-box-unflavoured.jpg"),
                "category_slug": "collection",
                "category_name": "The collection",
                "variants": [{"name": v, "price_minor": price} for v in variants],
            },
        )
        products.append(product)
    warehouse = Warehouse(
        tenant_id=tenant.id, code="LIQUID-SANDBOX", name="Liquid sandbox fixture", country_code="EE"
    )
    db.add(warehouse)
    db.flush()
    for variant in db.scalars(select(ProductVariant).where(ProductVariant.tenant_id == tenant.id)):
        db.add(Stock(warehouse_id=warehouse.id, variant_id=variant.id, quantity=100))
    commerce = settings_for(db, site, create=True)
    commerce.mode = "sandbox"
    commerce.customer_accounts_enabled = True
    commerce.shipping_minor = 1000  # Test-only fee. Helen's actual rate remains unconfirmed.
    commerce.free_shipping_threshold_minor = CONTENT["FREE_SHIPPING_THRESHOLD"] * 100
    commerce.origin_json = {
        "country": "EE",
        "line1": "Sandbox fixture",
        "city": "Tartu",
        "postal_code": "51013",
    }
    commerce.product_tax_codes_json = {p.id: "txcd_00000000" for p in products}
    commerce.subscription_product_ids_json = [products[0].id]
    commerce.tax_registration_reviewed = True  # Sandbox fixture only, never live acceptance.
    for path, title, kind, blocks in documents():
        page = create_page(
            db,
            site,
            title,
            path,
            kind,
            {
                "version": 1,
                "title": title,
                "description": CONTENT["metalContent"]["subheadline"]
                if path == "/"
                else CONTENT["shopContent"]["intro"],
                "blocks": blocks,
            },
        )
        page.published_json = copy.deepcopy(page.draft_json)
    # Only implemented targets enter validated menus. M2/M3 links remain in shell settings.
    for name in ("header", "footer"):
        items = [{"id": "shop", "label": "Shop all", "kind": "page", "path": "/shop"}]
        db.add(
            SiteMenu(
                tenant_id=tenant.id,
                site_id=site.id,
                name=name,
                items_json=items,
                published_items_json=copy.deepcopy(items),
            )
        )
    db.flush()
    return site
