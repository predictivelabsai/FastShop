"""A neutral shell provider used only to exercise theme-pack settings contracts."""

from app import site_editable_settings

PACK_FIELDS = [
    {"id": "shelltest.footer.newsletter_title", "group": "footer", "label": "Newsletter title",
     "type": "text", "default": "Letters from us"},
    {"id": "shelltest.footer.help_links", "group": "footer", "label": "Help links",
     "type": "repeater", "default": [], "max": 8,
     "fields": [{"id": "label", "label": "Link label", "type": "text"},
                {"id": "href", "label": "Link URL", "type": "url"}]},
    {"id": "shelltest.consent.heading", "group": "consent", "label": "Consent heading",
     "type": "text", "default": "Your privacy choices"},
    {"id": "shelltest.consent.privacy_href", "group": "consent", "label": "Privacy URL",
     "type": "url", "default": "/pages/privacy-policy"},
]


def register_shelltest(monkeypatch, *, with_renderer=False):
    monkeypatch.setitem(site_editable_settings._PROVIDERS, "shelltest", lambda: PACK_FIELDS)
    if with_renderer:
        from fasthtml.common import Div, P

        from app import site_theme

        def storefront(db, site, page, base, csrf, canonical, *, preview=False, editor=False, message=""):
            config = site.settings_json if preview else site.published_settings_json
            shell = (config or {}).get("shelltest", {})
            footer = shell.get("footer", {})
            consent = shell.get("consent", {})
            return Div(P(footer.get("newsletter_title", "")),
                       P(consent.get("heading", "")), cls="shelltest-renderer")

        def commerce(site, request, title, *children):
            return Div(*children, cls="shelltest-commerce-renderer")

        monkeypatch.setitem(site_theme.THEME_PACKS, "shelltest", {
            "storefront": storefront, "commerce": commerce,
        })
