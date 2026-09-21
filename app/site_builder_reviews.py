"""Explicit merchant approval for model-proposed catalog/configuration changes."""

import copy
import re
from datetime import UTC, datetime

from sqlalchemy import select

from app import commerce, content, site_samples
from app.models import (
    Category,
    Product,
    ProductType,
    ProductVariant,
    SiteBuilderTurn,
    VariantChannelListing,
)
from app.services import CommerceError


def review_context(db, site):
    config = commerce.settings_for(db, site)
    merchant = site_samples.values_for(site, config) if config else {
        key: site.settings_json.get(key, "") if key in {"company", "address", "email"} else "" for key in site_samples.FIELDS}
    catalog = []
    for variant, product, listing in db.execute(select(ProductVariant, Product, VariantChannelListing)
        .join(Product, Product.id == ProductVariant.product_id)
        .outerjoin(VariantChannelListing, (VariantChannelListing.variant_id == ProductVariant.id) & (VariantChannelListing.channel_id == site.channel_id))
        .where(Product.tenant_id == site.tenant_id, ProductVariant.tenant_id == site.tenant_id)
        .order_by(ProductVariant.id).limit(100)):
        catalog.append({"variant_id": variant.id, "product_id": product.id, "product": product.name,
            "variant": variant.name, "price_minor": listing.price_minor if listing else None})
    return {"merchant": merchant, "commerce_version": config.version if config else None,
            "commerce_mode": config.mode if config else "disabled", "catalog": catalog}


def validate_proposals(proposals):
    if not isinstance(proposals, list) or len(proposals) > 3:
        raise CommerceError("Propose at most three merchant changes at a time.")
    for proposal in proposals:
        if not isinstance(proposal, dict):
            raise CommerceError("Invalid merchant proposal.")
        kind = proposal.get("kind")
        if kind == "merchant" and set(proposal) == {"kind", "values"}:
            values = proposal["values"]
            if not isinstance(values, dict) or not values or set(values) - set(site_samples.FIELDS):
                raise CommerceError("Unsupported merchant proposal fields.")
            for key, value in values.items():
                if key.endswith("_minor"):
                    if type(value) is not int or not 0 <= value <= 1_000_000:
                        raise CommerceError("Shipping proposals use whole USD cents.")
                elif not isinstance(value, str) or not value.strip() or len(value) > 300:
                    raise CommerceError("Merchant proposal text is invalid.")
                if key == "origin_country" and value not in commerce.EU_COUNTRIES:
                    raise CommerceError("Warehouse proposals must use an EU country.")
        elif kind == "price" and set(proposal) == {"kind", "variant_id", "price_minor"}:
            if not isinstance(proposal["variant_id"], str) or type(proposal["price_minor"]) is not int or not 0 <= proposal["price_minor"] <= 100_000_000:
                raise CommerceError("Price proposals need a product option and whole USD cents.")
        elif kind == "product" and set(proposal) == {"kind", "name", "slug", "variants"}:
            if not isinstance(proposal["name"], str) or not 1 <= len(proposal["name"].strip()) <= 220 or not isinstance(proposal["slug"], str) or not re.fullmatch(r"[a-z0-9-]{1,100}", proposal["slug"]):
                raise CommerceError("Enter a product name and lowercase URL slug.")
            variants = proposal["variants"]
            if not isinstance(variants, list) or not 1 <= len(variants) <= 12:
                raise CommerceError("A product proposal needs one to twelve variants.")
            for variant in variants:
                if not isinstance(variant, dict) or set(variant) != {"name", "price_minor"} or not isinstance(variant["name"], str) or not 1 <= len(variant["name"].strip()) <= 180 or type(variant["price_minor"]) is not int or not 0 <= variant["price_minor"] <= 100_000_000:
                    raise CommerceError("Each proposed variant needs a name and whole-cent price.")
        else:
            raise CommerceError("That action cannot be approved through the site builder.")
    return proposals


def create_product(db, site, proposal):
    if db.scalar(select(Product.id).where(Product.tenant_id == site.tenant_id, Product.slug == proposal["slug"])):
        raise CommerceError("This product URL already exists. Choose another slug.")
    category = db.scalar(select(Category).where(Category.tenant_id == site.tenant_id))
    product_type = db.scalar(select(ProductType).where(ProductType.tenant_id == site.tenant_id))
    if not category:
        category = Category(tenant_id=site.tenant_id, name="Our collection", slug="collection")
        db.add(category)
    if not product_type:
        product_type = ProductType(tenant_id=site.tenant_id, name="Physical product", slug="physical")
        db.add(product_type)
    db.flush()
    product = Product(tenant_id=site.tenant_id, category_id=category.id, product_type_id=product_type.id,
        name=proposal["name"].strip(), slug=proposal["slug"], is_published=True)
    db.add(product)
    db.flush()
    for i, item in enumerate(proposal["variants"]):
        variant = ProductVariant(tenant_id=site.tenant_id, product_id=product.id, name=item["name"].strip(),
            sku="BUILDER-" + product.id + "-" + str(i), sort_order=i)
        db.add(variant)
        db.flush()
        db.add(VariantChannelListing(variant_id=variant.id, channel_id=site.channel_id, currency="USD", price_minor=item["price_minor"]))
    page = content.create_page(db, site, product.name, "/products/" + product.slug, "product", {"title": product.name,
        "sections": [{"type": "product", "heading": product.name, "body": "Add reviewed product details before publication."}]})
    page.product_id = product.id


def decide(db, site_id, user_id, turn_id, *, approve):
    with db.begin_nested():
        return _decide(db, site_id, user_id, turn_id, approve=approve)


def _decide(db, site_id, user_id, turn_id, *, approve):
    from app import site_builder_services as builder
    site = content.owned_site(db, site_id, user_id, publish=True)
    site = builder.lock_site(db, site.id, user_id, site.version)
    turn = db.scalar(select(SiteBuilderTurn).where(SiteBuilderTurn.id == turn_id,
        SiteBuilderTurn.site_id == site.id, SiteBuilderTurn.tenant_id == site.tenant_id).with_for_update().execution_options(populate_existing=True))
    if not turn or turn.status != "complete" or not turn.response_json.get("proposals"):
        raise CommerceError("Merchant proposal not found.")
    response = copy.deepcopy(turn.response_json)
    if response.get("review_status") != "pending":
        return response.get("review_status", "unavailable")
    if approve:
        if response.get("review_context") != review_context(db, site) or response.get("review_site_version") != site.version:
            raise CommerceError("Merchant data changed after this proposal. Ask for a fresh proposal before approving.")
        proposals = validate_proposals(response["proposals"])
        for proposal in proposals:
            if proposal["kind"] == "merchant":
                config = commerce.settings_for(db, site, create=True)
                values = site_samples.values_for(site, config) | proposal["values"]
                if values["shipping_minor"] is None:
                    raise CommerceError("Enter or propose a shipping fee first; no unapproved default will be applied.")
                reviewed = {key for key, value in site.settings_json.get("sample_fields", {}).items() if value.get("reviewed")}
                site_samples.save_samples(db, site, user_id, values, reviewed | set(proposal["values"]), config.version)
                settings = copy.deepcopy(site.settings_json)
                for key in proposal["values"]:
                    settings["sample_fields"][key]["source"] = "ai_proposed"
                site.settings_json = settings
            elif proposal["kind"] == "price":
                variant = db.scalar(select(ProductVariant).where(ProductVariant.id == proposal["variant_id"], ProductVariant.tenant_id == site.tenant_id).with_for_update())
                if not variant:
                    raise CommerceError("This product option does not belong to the site.")
                listing = db.scalar(select(VariantChannelListing).where(VariantChannelListing.variant_id == variant.id,
                    VariantChannelListing.channel_id == site.channel_id).with_for_update())
                if listing:
                    listing.price_minor = proposal["price_minor"]
                else:
                    db.add(VariantChannelListing(variant_id=variant.id, channel_id=site.channel_id, currency="USD", price_minor=proposal["price_minor"]))
            else:
                create_product(db, site, proposal)
        site.version += 1
    response.update(review_status="approved" if approve else "rejected", reviewed_by=user_id, reviewed_at=datetime.now(UTC).isoformat())
    turn.response_json = response
    db.flush()
    return response["review_status"]
