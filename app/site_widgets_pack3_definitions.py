"""Commerce and marketing widget declarations and cross-field validation."""
from __future__ import annotations

import re

from app.services import CommerceError
from app.site_widgets_pack1 import ICON_PATHS


def phone(value):
    if value and (not re.fullmatch(r"\+?[0-9 ()\-.]{5,40}", value) or not 5 <= len(re.sub(r"\D", "", value)) <= 15):
        raise CommerceError("Enter a valid phone number with 5 to 15 digits.")
    return value


def email(value):
    if value and not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", value):
        raise CommerceError("Enter a valid email address.")
    return value


def validate_pack_widget(block):
    if block["type"] == "pricing_table":
        for item in block.get("items", []):
            if len([line for line in item.get("features", "").splitlines() if line.strip()]) > 12:
                raise CommerceError("A plan may contain up to 12 features.")
    if block["type"] == "star_rating" and block.get("scope") == "product" and not block.get("product"):
        raise CommerceError("Choose a product for product reviews.")
    return block


def pack_definitions(definition):
    def text(kind="text", **extra):
        return {"kind": kind, "max_length": 4000 if kind == "textarea" else 2048 if kind in {"url", "image"} else 240, **extra}

    def choice(*values):
        return {"kind": "select", "options": values}

    def number(low, high):
        return {"kind": "range", "min": low, "max": high, "unit": ""}

    def items(default, fields, low, high):
        return {"defaults": default, "fields": fields, "min": low, "max": high, "required": tuple(fields)}

    toggle = {"kind": "toggle"}
    link = text("url", https=True, allow_relative=True)
    plan = {"name": "Your plan", "price": "Your price", "description": "Describe who this plan is for.", "features": "Your first feature\nYour next feature", "button_label": "Learn more", "link": "", "highlighted": False, "badge": ""}
    row = {"feature": "Your feature", **{f"cell_{i}": "yes" if i == 1 else "no" if i == 2 else "" for i in range(1, 5)}}
    badge = {"icon": "truck", "heading": "Your delivery promise", "text": "Add your delivery details."}
    message = {"text": "Your announcement", "link": ""}
    step = {"heading": "Your first step", "body": "Explain what happens here.", "icon": ""}
    hour = {"day": "Monday", "hours": "Add your opening hours"}
    return {
        "featured_collection": definition("Featured collection", "Commerce", {"collection": "", "layout": "grid", "limit": 6, "columns": 3, "show_description": True, "show_image": True, "link_label": "View all"}, {"collection": text("collection"), "layout": choice("grid", "scroll"), "limit": number(1, 12), "columns": number(1, 4), "show_description": toggle, "show_image": toggle, "link_label": text()}, icon="featured-collection", description="Feature a real collection with its active products"),
        "pricing_table": definition("Pricing table", "Commerce", {"heading": "Choose your plan", "items": [plan.copy(), {**plan, "name": "Another plan", "highlighted": True, "badge": "Your highlight"}]}, {"heading": text()}, items(plan, {"name": text(), "price": text(help="Display text only, for example EUR 19 / month."), "description": text("textarea"), "features": text("textarea", help="One feature per line, up to 12."), "button_label": text(), "link": link, "highlighted": toggle, "badge": text()}, 1, 4), icon="pricing-table", description="Present up to four plans with your own price text"),
        "comparison_table": definition("Comparison table", "Commerce", {"options": 2, "caption": "Compare your options", **{f"option_{i}": f"Option {i}" for i in range(1, 5)}, "items": [row.copy()]}, {"options": number(1, 4), "caption": text(), **{f"option_{i}": text() for i in range(1, 5)}}, items(row, {"feature": text(), **{f"cell_{i}": text(help="Use yes, no or your own text.") for i in range(1, 5)}}, 1, 20), icon="comparison-table", description="Compare features in an accessible scrolling table"),
        "trust_badges": definition("Trust badges", "Marketing", {"layout": "row", "items": [badge.copy(), {"icon": "chat", "heading": "Your support details", "text": "Tell visitors how to contact you."}]}, {"layout": choice("row", "grid")}, items(badge, {"icon": choice(*ICON_PATHS), "heading": text(), "text": text("textarea", max_length=500)}, 2, 6), icon="trust-badges", description="Share your own delivery, service and shop promises"),
        "announcement_bar": definition("Announcement bar", "Marketing", {"scheme": "accent", "dismissible": False, "interval": 5, "items": [message.copy()]}, {"scheme": choice("accent", "neutral", "subtle"), "dismissible": toggle, "interval": number(3, 15)}, items(message, {"text": text(), "link": link}, 1, 3), icon="announcement-bar", description="Share up to three announcements with optional rotation"),
        "promo_banner": definition("Promo banner", "Marketing", {"heading": "Your promotion", "body": "Share the details of your offer.", "image": "", "image_alt": "", "button_label": "Learn more", "link": "", "discount_code": ""}, {"heading": text(), "body": text("textarea"), "image": text("image", https=True, allow_relative=True), "image_alt": text(), "button_label": text(), "link": link, "discount_code": text("discount_code")}, icon="promo-banner", description="Promote an offer with an optional active discount code"),
        "steps": definition("Steps", "Content", {"heading": "How it works", "items": [step.copy(), {**step, "heading": "Your next step"}]}, {"heading": text()}, items(step, {"heading": text(), "body": text("textarea"), "icon": choice("", *ICON_PATHS)}, 2, 8), icon="steps", description="Explain a process in a numbered sequence"),
        "star_rating": definition("Star rating", "Social proof", {"scope": "site", "product": "", "link": "", "link_label": "Read reviews"}, {"scope": choice("site", "product"), "product": text("product"), "link": link, "link_label": text()}, icon="star-rating", description="Show the aggregate of approved real customer reviews"),
        "contact_card": definition("Contact card", "Content", {"heading": "Contact us", "address": "Add your address", "phone": "", "email": "", "map_link": "", "items": [hour.copy()]}, {"heading": text(), "address": text("textarea"), "phone": text(validate=phone), "email": text(validate=email), "map_link": text("url", https=True)}, items(hour, {"day": text(), "hours": text()}, 0, 7), icon="contact-card", description="Share your address, contact details and opening hours"),
    }
