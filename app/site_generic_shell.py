"""Default-theme copy fields shared by the generic storefront renderer."""

from app.site_editable_settings import register_shell

DEFAULTS = {
    "cta": "Explore",
    "placeholder_image": "Image placeholder",
    "collection_empty": "No items are available yet.",
    "video_toggle": "Pause motion",
    "hero_video_alt": "Hero background video",
    "placeholder_media": "Media placeholder",
    "facts_source": "Source: ",
    "facts_source_name": "Research source",
    "facts_date_prefix": "Figures checked on ",
    "facts_date": "Date to be confirmed",
    "claims_pending": "No benefit statements have been added yet.",
    "reviews_eyebrow": "Customer reviews",
    "reviews_empty_eyebrow": "Share your experience",
    "reviews_sample": "Sample section. No customer reviews are shown.",
    "article_read": "Read the story",
    "research_all": "All",
    "research_exercise": "Exercise",
    "research_reviews": "Reviews",
    "research_meta": "Meta-analyses",
    "research_source": "Source",
    "research_study": "Study",
    "research_read": "Read the source",
    "team_photo": "Photo placeholder",
    "team_learn": "Learn more",
    "contact_heading": "Contact",
    "contact_name": "Your name",
    "contact_email": "Email address",
    "contact_message": "Your message",
    "contact_honeypot": "Leave this field empty",
    "contact_consent": "Your details will be used to reply to this message.",
    "privacy": "Privacy policy",
    "contact_send": "Send message",
    "product_alt": "Product image placeholder",
    "gallery_alt": "Gallery image placeholder",
    "gallery_view": "View gallery image",
    "product_gallery_caption": "Product images",
    "card_product_alt": "placeholder product image",
    "draft_price_suffix": " · draft price",
    "card_price_confirm": "Price to be confirmed",
    "price_unit": " / item",
    "one_time_price_suffix": " · one-time price",
    "checkout_status_prefix": "Checkout: ",
    "live_checkout": "Live checkout",
    "sandbox_checkout": "Sandbox checkout",
    "split_video_alt": "Placeholder motion study",
    "editorial_image_alt": "Editorial placeholder photography",
    "product_eyebrow": "Product",
    "price_confirm": "Price to be confirmed",
    "price_notice": "Final price and options will be shown before payment.",
    "choose_option": "Choose an option",
    "quantity": "Quantity",
    "purchase_option": "Purchase option",
    "one_time": "One-time purchase",
    "subscribe": "Subscribe",
    "shipping_notice": "Shipping and applicable taxes are calculated before payment.",
    "monthly_notice": "Recurring delivery details are shown before payment.",
    "add_bag": "Add to bag",
    "add_cart_phase2": "Add to cart",
    "live_payments": "Live payments are enabled.",
    "sandbox_payments": "Sandbox payments are enabled.",
    "design_preview": "Checkout is not available in this preview.",
    "skip": "Skip to content",
    "menu": "Menu",
    "shop": "Shop",
    "all_products": "All items",
    "account": "Account",
    "bag": "Bag",
    "bag_empty": "Bag (0)",
    "cart_aria": "Cart, zero items",
    "offer_eyebrow": "Store update",
    "offer_body": "Sign up to hear when new items are available.",
    "offer_preview": "Preview the offer",
    "footer_explore": "Explore",
    "footer_help": "Help",
    "terms": "Terms",
    "privacy_policy": "Privacy policy",
    "faq": "FAQ",
    "returns": "Returns",
    "copyright": "© ",
    "cookies": "Cookie preferences",
    "offer_close": "Close",
    "offer_confirm": "Confirm your email address to continue.",
    "offer_email": "Email address",
    "offer_submit": "Send confirmation link",
    "offer_preview_body": "Sign-up is unavailable in this preview.",
    "email_placeholder": "you@example.com",
}

_HEADER = "cta video_toggle contact_heading contact_name contact_email contact_message contact_honeypot contact_consent contact_send privacy skip menu shop all_products account bag bag_empty cart_aria offer_eyebrow offer_body offer_preview offer_close offer_confirm offer_email offer_submit offer_preview_body email_placeholder".split()
_FOOTER = "footer_explore footer_help terms privacy_policy faq returns copyright cookies".split()
GROUPS = {
    "header": _HEADER,
    "footer": _FOOTER,
    "store labels": [key for key in DEFAULTS if key not in {*_HEADER, *_FOOTER}],
}


def fields():
    result = [
        {
            "id": f"generic_labels.{key}",
            "label": key.replace("_", " ").capitalize(),
            "type": "textarea" if key.endswith(("body", "consent", "notice", "message")) else "text",
            "default": DEFAULTS[key],
            "group": group,
            "max_length": 2000,
        }
        for group, keys in GROUPS.items()
        for key in keys
    ]
    from app.account_copy import DEFAULT_ACCOUNT_COPY

    result.extend(
        {
            "id": f"account_copy.{key}",
            "label": key.replace("_", " ").capitalize(),
            "type": "textarea" if key.endswith(("message", "notice", "instruction")) else "text",
            "default": value,
            "group": "store labels",
            "max_length": 2000,
        }
        for key, value in DEFAULT_ACCOUNT_COPY.items()
    )
    return result


register_shell("generic", fields)
