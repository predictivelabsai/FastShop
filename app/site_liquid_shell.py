"""Liquid header, footer and consent fields for the visual editor (defaults from the seed copy)."""
from __future__ import annotations

from app.site_editable_settings import register_shell

PAYMENT_MARKS = {
    "Visa": "payment-visa.svg",
    "Mastercard": "payment-mastercard.svg",
    "PayPal": "payment-paypal.svg",
    "Apple Pay": "payment-applepay.svg",
    "Google Pay": "payment-google-pay-on-white.svg",
}
CONSENT_DEFAULTS = {
    "heading": "Your privacy, your choice.",
    "body": "Essential storage keeps this site working. Preferences, analytics and marketing "
            "are off unless you choose them.",
    "accept": "Accept all",
    "decline": "Decline",
    "save": "Save preferences",
    "privacy_label": "Privacy policy",
    "privacy_href": "/pages/privacy-policy",
    "categories": "Preferences",
    "essential": "Essential (always on)",
    "preferences": "Preferences: remembers products you viewed on this device",
    "analytics": "Analytics",
    "marketing": "Marketing",
}
LABEL_DEFAULTS = {
    "skip": "Skip to content",
    "request": "Request your code",
    "dismiss": "Dismiss first order offer",
    "account": "My account",
    "bag": "Open bag",
    "menu": "Open menu",
    "close": "Close menu",
    "cookies": "Cookie preferences",
    "copyright": "",
}


def _text(id, label, default, *, area=False, group, help="", max_length=2000):
    return {"id": id, "label": label, "type": "textarea" if area else "text",
            "default": default, "group": group, "help": help, "max_length": max_length}


def _links(id, label, default, group, *, hash=False):
    fields = [{"id": "label", "label": "Label", "type": "text", "default": "", "max_length": 200},
              {"id": "href", "label": "Link", "type": "url", "default": "/", "max_length": 500}]
    if hash:
        fields.append({"id": "hash", "label": "Anchor on page (optional)", "type": "text",
                       "default": "", "max_length": 100})
    return {"id": id, "label": label, "type": "repeater", "default": default, "group": group,
            "fields": fields, "min": 0, "max": 20, "help": ""}


def fields():
    from app.site_seed_liquid import CONTENT

    offer, footer, nav = CONTENT["offer"], CONTENT["footer"], CONTENT["nav"]
    o, f, n, c, lab = "liquid_shell.offer.", "liquid_shell.footer.", "liquid_shell.nav.", \
        "liquid_consent.", "liquid_shell.labels."
    from app.account_copy import DEFAULT_ACCOUNT_COPY
    from app.subscription_portal_copy import DEFAULT_PORTAL_COPY

    return [
        *[_text("liquid_shell.portal." + key, key.replace("_", " ").capitalize(), default,
                group="portal") for key, default in DEFAULT_PORTAL_COPY.items()],
        *[{"id": "account_copy." + key, "label": key.replace("_", " ").capitalize(),
           "type": "text", "default": default, "group": "pages", "max_length": 2000}
          for key, default in DEFAULT_ACCOUNT_COPY.items()],
        {"id": "liquid_shell.header.logo", "label": "Store logo", "type": "image",
         "default": "/static/h24you/liquid/logo-clean-light.png", "group": "header",
         "max_length": 2048},
        {"id": "liquid_shell.header.favicon", "label": "Store favicon", "type": "image",
         "default": "/static/h24you/liquid/logo-clean-dark.png", "group": "header",
         "max_length": 2048},
        *[_text("liquid_shell.pages." + key, label, default, group="pages") for key, label, default in (
            ("cart_title", "Cart heading", "Your bag"),
            ("account_title", "Account heading", "My account"),
            ("orders_title", "Orders heading", "Your orders"),
            ("subscriptions_title", "Subscriptions heading", "Subscriptions"),
            ("not_found_title", "Not found heading", "Page not found"),
            ("message_title", "Message heading", "Please check your details"),
            ("shop_link", "Shop link", "Shop all"),
            ("home_link", "Home link", "Back to home"),
            ("cart_empty", "Empty cart message", "Your cart is still waiting for its first fizz. Have a look at the tablets or the bottle, and we will keep a spot warm here."),
            ("subtotal", "Subtotal label", "Subtotal"),
            ("checkout", "Checkout button", "Continue to checkout"),
            ("not_found_message", "Not found message", "The page you were looking for has moved or no longer exists."),
            ("order_summary", "Order summary heading", "Order summary"),
            ("shipping_next", "Shipping and tax message", "Shipping and destination sales tax are calculated next."),
            ("quantity_label", "Quantity label", "Quantity"),
            ("discount_label", "Discount code label", "Discount code"),
            ("discount_help", "Discount code help", "Save your code here. Eligibility and savings are checked at checkout using your email. An empty code removes it."),
            ("save_discount", "Save discount button", "Save discount code"),
            ("monthly_subscription", "Monthly subscription label", "Monthly subscription · Deliver every month"),
            ("resume_checkout", "Resume checkout link", "Resume existing checkout"),
            ("free_shipping", "Free shipping message", "You've got free shipping"),
            ("free_shipping_remaining", "Free shipping progress message", "Spend {amount} more for free shipping"),
            ("quantity_remove", "Quantity removal label", "Quantity (0 removes)"),
            ("update_quantity", "Update quantity button", "Update quantity"),
            ("subscribe_save", "Subscription purchase option", "Subscribe & save 10%"),
            ("one_time", "One-time purchase option", "One-time purchase"),
            ("update", "Update button", "Update"),
            ("remove", "Remove button", "Remove"),
        )],
        _text(o + "title", "Offer bar text", offer["title"], group="header"),
        _text(lab + "request", "Offer bar button", LABEL_DEFAULTS["request"], group="header"),
        _text(o + "body", "Offer form text", offer["body"], area=True, group="header"),
        _text(o + "placeholder", "Email placeholder", offer["placeholder"], group="header"),
        _text(o + "consent", "Marketing consent text", offer["consent"], area=True, group="header"),
        _text(o + "privacyLabel", "Consent privacy link label", offer["privacyLabel"], group="header"),
        {**_text(o + "privacyHref", "Consent privacy link", offer["privacyHref"], group="header",
                 help="Use # to show the label without a link."), "type": "url"},
        _text(o + "cta", "Signup button", offer["cta"], group="header"),
        _text(o + "thanks", "Signup thank-you message", offer["thanks"], area=True, group="header"),
        _text(n + "shop.label", "Shop menu label", nav["shop"]["label"], group="header"),
        _links(n + "shop.items", "Shop menu links", nav["shop"]["items"], "header"),
        _text(n + "why.label", "Second menu label", nav["why"]["label"], group="header"),
        _links(n + "why.items", "Second menu links", nav["why"]["items"], "header"),
        _text(n + "about.label", "About link label", nav["about"]["label"], group="header"),
        {**_text(n + "about.href", "About link", nav["about"]["href"], group="header"), "type": "url"},
        _links(n + "helpLinks", "Mobile menu help links", nav["helpLinks"], "header"),
        _text(lab + "skip", "Skip link (screen readers)", LABEL_DEFAULTS["skip"], group="header"),
        _text(lab + "dismiss", "Dismiss offer label (screen readers)", LABEL_DEFAULTS["dismiss"],
              group="header"),
        _text(lab + "account", "Account icon label (screen readers)", LABEL_DEFAULTS["account"],
              group="header"),
        _text(lab + "bag", "Bag icon label (screen readers)", LABEL_DEFAULTS["bag"], group="header"),
        _text(lab + "menu", "Menu button label (screen readers)", LABEL_DEFAULTS["menu"], group="header"),
        _text(lab + "close", "Close menu label (screen readers)", LABEL_DEFAULTS["close"],
              group="header"),
        _text(f + "newsletterTitle", "Newsletter heading", footer["newsletterTitle"], group="footer"),
        _text(f + "newsletterBody", "Newsletter text", footer["newsletterBody"], area=True,
              group="footer"),
        _links(f + "links", "Footer links", footer["links"], "footer"),
        _text(f + "helpTitle", "Help column heading", footer["helpTitle"], group="footer"),
        _links(f + "helpLinks", "Help column links", footer["helpLinks"], "footer", hash=True),
        _text(f + "company", "Company name", footer["company"], group="footer"),
        _text(f + "address", "Company address", footer["address"], area=True, group="footer"),
        {**_text("email", "Contact email", footer["email"], group="footer"), "max_length": 320},
        _links(f + "social", "Social links", footer["social"], "footer"),
        {"id": f + "payments", "label": "Payment marks", "type": "list", "group": "footer",
         "default": footer["payments"], "choices": list(PAYMENT_MARKS),
         "help": "Comma-separated, from: " + ", ".join(PAYMENT_MARKS) + "."},
        _text("footer", "Footer disclaimer", CONTENT["fdaDisclaimer"], area=True, group="footer",
              help="Required beside approved benefit statements."),
        _text(lab + "cookies", "Cookie preferences button", LABEL_DEFAULTS["cookies"], group="footer"),
        _text(lab + "copyright", "Copyright line", "", group="footer",
              help="Leave blank for © <year> <company name>. All rights reserved."),
        *[_text(c + key, label, CONSENT_DEFAULTS[key], area=key == "body", group="consent")
          for key, label in (("heading", "Banner heading"), ("body", "Banner message"),
                             ("accept", "Accept button"), ("decline", "Decline button"),
                             ("save", "Save button"), ("privacy_label", "Policy link label"))],
        {**_text(c + "privacy_href", "Policy link", CONSENT_DEFAULTS["privacy_href"], group="consent",
                 help="Hidden while the policy page is empty. The banner always shows."), "type": "url"},
        *[_text(c + key, label, CONSENT_DEFAULTS[key], group="consent")
          for key, label in (("categories", "Categories toggle"), ("essential", "Essential category"),
                             ("preferences", "Preferences category"), ("analytics", "Analytics category"),
                             ("marketing", "Marketing category"))],
    ]


register_shell("liquid", fields)
