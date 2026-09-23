"""Read-only placeholder/pending-content inventory for a site (brief §9, deliverable #3).

Enumerates everything a merchant must replace or approve before launch — placeholder
copy and media, unapproved benefit statements, unset prices, missing social links and
unreviewed merchant fields — so the "list every placeholder" deliverable is generated
from the live content instead of maintained by hand.
"""

from __future__ import annotations

from sqlalchemy import select

from app.content import site_pages
from app.models import Product, ProductVariant, VariantChannelListing
from app.site_samples import pending_reviews

_MARKER = "placeholder"


def _has_marker(value) -> bool:
    return isinstance(value, str) and _MARKER in value.lower()


def placeholder_inventory(db, site) -> list[dict]:
    """Every item still awaiting real content or approval, newest concerns first."""
    items: list[dict] = []
    config = site.settings_json or {}

    def add(area: str, location: str, detail: str, status: str = "placeholder") -> None:
        items.append({"area": area, "location": location, "detail": detail, "status": status})

    if _has_marker(config.get("address")):
        add("Legal", "Settings · Company address", "Placeholder legal address")
    if _has_marker(config.get("science_date")):
        add("Science", "Settings · Research figures date",
            "Re-check the counter figures against hydrogenclinicalresearch.com and set the date", "pending")
    for social in config.get("socials", []):
        if not social.get("url"):
            add("Social", f"Footer · {social.get('label', 'Social')}",
                "Link points to # — add the real profile URL")
    for claim in config.get("claims", []):
        if not claim.get("approved"):
            add("Compliance", "Settings · Benefit statements",
                f"Statement pending regulatory approval: “{str(claim.get('text', ''))[:60]}”", "pending")
    for label in pending_reviews(config):
        add("Merchant", "Sample review", f"{label} not yet reviewed", "pending")

    for page in site_pages(db, site):
        document = page.draft_json or {}
        for section in document.get("sections", []):
            if not isinstance(section, dict):
                continue
            location = f"{page.path} · {section.get('type')}"
            for key in ("eyebrow", "heading", "body"):
                text = section.get(key, "")
                if isinstance(text, str) and "PLACEHOLDER" in text:
                    add("Content", location, f"Placeholder copy: “{text.strip()[:60]}”")
                    break
            for key in ("image", "video", "mobile_video", "poster"):
                if _has_marker(section.get(key)):
                    add("Media", location, f"Placeholder {key}: {section.get(key)}")
            for src in section.get("gallery", []) or []:
                if _has_marker(src):
                    add("Media", f"{page.path} · gallery", f"Placeholder image: {src}")

    for product in db.scalars(select(Product).where(
            Product.tenant_id == site.tenant_id, Product.is_published.is_(True))):
        if _has_marker(product.image_url):
            add("Media", f"Product · {product.name}", f"Placeholder image: {product.image_url}")
        priced = db.scalar(
            select(VariantChannelListing.id)
            .join(ProductVariant, ProductVariant.id == VariantChannelListing.variant_id)
            .where(ProductVariant.product_id == product.id,
                   VariantChannelListing.channel_id == site.channel_id))
        if not priced:
            add("Pricing", f"Product · {product.name}",
                "No channel price set — confirm final price and pack size", "pending")
    return items
