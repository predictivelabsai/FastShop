"""The store's Integrations page: every connection grouped by what it does for the merchant."""
from __future__ import annotations

from fasthtml.common import H2, A, Div, Input, Label, P, Span, Strong

from app.admin_icons import lucide
from app.admin_ui import badge, page_header
from app.models import Site

_TONES = {"Connected": "success", "Live": "success", "Test mode": "info", "Sandbox": "info",
          "Platform key": "info", "Needs attention": "warning",
          "Set": "success", "Ready": "success", "Available": "success"}


def _row(icon, name, description, status, href, action):
    return A(
        Span(lucide(icon, 20), cls="p-app-icon"),
        Span(Strong(name), Span(description, cls="p-app-description"), cls="p-app-text"),
        badge(status, _TONES.get(status, "neutral")),
        Span(action, cls="p-app-action"),
        href=href, cls="p-app-row", data_filter_item=" ".join((name, description)).lower(),
    )


def _section(title, description, *rows, status=None):
    return Div(Div(Div(H2(title), status, cls="p-app-section-title"), P(description), cls="p-app-section-head"),
               Div(*rows, cls="p-app-rows"), cls="a-card p-app-section", data_filter_section="")


def page(db, site: Site):
    from sqlalchemy import select

    from app import connectors
    from app.models import SiteCommerceSettings

    base = f"/admin/sites/{site.id}"
    commerce = db.scalar(select(SiteCommerceSettings).where(
        SiteCommerceSettings.site_id == site.id, SiteCommerceSettings.tenant_id == site.tenant_id))
    mode = commerce.mode if commerce else ""
    live = bool(commerce and mode == "live" and commerce.live_accepted_at and commerce.live_credential_id)
    # Live payments need operator approval in FastShop, so the badge says whether that has happened.
    checkout = ("Live payments: approved", "success") if live else ("Live payments: not approved", "neutral")
    stripe = "Live" if live else "Test mode" if mode == "sandbox" else "Not connected"
    ga4 = "Set" if (site.settings_json or {}).get("ga4_measurement_id") else "Not set"
    importers = {connector.platform: connector for connector in connectors.listed_connectors()}

    def importer(platform):
        connector = importers.get(platform)
        return "Connected" if connector and connector.credential_state(site).configured else "Not connected"

    def manage(status):
        return "Manage" if status not in {"Not connected", "Not set"} else "Set up"

    from app import assistant_provider

    model = assistant_provider.status(db, site)
    model_hints = model["hints"]
    model_description = (f"{model_hints.get('provider', '')} · {model_hints.get('model', '')}"
                         if model["state"] == "store" else
                         "Bring your own xAI, OpenAI, OpenRouter or OpenAI-compatible API key.")
    sections = [
        _section("Payments", "Take card and wallet payments at checkout.",
                 _row("credit-card", "Stripe", "Cards, Apple Pay, Google Pay and PayPal through Stripe.", stripe,
                      base + "/commerce", manage(stripe)),
                 status=badge(*checkout)),
        _section("Analytics and tracking", "Measure visits and conversions. Tags load only after visitor consent.",
                 _row("chart-line", "Google Analytics 4", "Visits, behaviour and purchases.", ga4,
                      base + "/settings/general", manage(ga4))),
        _section("Sales channels and feeds", "List your products on other platforms.",
                 _row("rss", "Google Merchant Center", "A product feed of your published catalogue.",
                      "Available", base + "/integrations/import#integration-connections", "Open"))
        if "csv" in importers else None,
        _section("AI assistant", "The model behind the store assistant in the admin and the theme editor.",
                 _row("sparkles", "AI model", model_description, model["label"],
                      base + "/settings/assistant",
                      "Manage" if model["state"] in {"store", "unreadable"} else "Set up")),
        _section("Import and migration", "Bring products, customers, orders and content across. "
                                         "Every import is previewed before anything is written.",
                 *[_row(icon, name, description, importer(platform), base + "/integrations/import", "Open")
                   for platform, icon, name, description in (
                       ("shopify", "store", "Shopify", "Collections, products, customers and orders."),
                       ("woocommerce", "shopping-cart", "WooCommerce", "Categories, products, customers and orders."),
                       ("wordpress", "file-text", "WordPress", "Posts, pages, categories, tags and media."),
                       ("csv", "table-2", "CSV catalogue", "Upload a spreadsheet of products and variants."),
                   ) if platform in importers]),
    ]
    return (
        page_header("Integrations"),
        Div(Label(Span("Search integrations", cls="a-sr-only"), lucide("search", 16),
                  Input(type="search", placeholder="Search integrations", data_filter_input="",
                        autocomplete="off"), cls="p-app-search"),
            cls="p-app-toolbar"),
        *[section for section in sections if section is not None],
        P("No integrations match your search.", cls="p-app-none", hidden=True, data_filter_none=""),
        Div(H2("Coming to FastShop"),
            P("Email providers, shipping carriers and label printing, product reviews, email marketing "
              "platforms and accounting."), cls="p-app-roadmap"),
    )
