"""Settings -> Integrations -> import from Shopify, WooCommerce or WordPress.

Merchants connect their old store themselves. Credentials are encrypted per store and take
precedence over the operator environment settings the importers used before.
"""

from urllib.parse import urlencode

from fasthtml.common import H2, A, Button, Div, Form, Input, Label, Li, Ol, P
from starlette.responses import PlainTextResponse, RedirectResponse

from app import content, site_integrations
from app.db import SessionLocal
from app.services import CommerceError


def _shopify(values):
    from app.integrations.shopify import _valid_shop, _valid_token

    shop = values["shop"].strip().lower().removeprefix("https://").rstrip("/")
    if not _valid_shop(shop):
        raise CommerceError("Enter your Shopify domain, such as yourstore.myshopify.com.")
    if not _valid_token(values["token"].strip()):
        raise CommerceError("Enter the Admin API access token from your Shopify custom app.")
    return {"shop": shop, "token": values["token"].strip()}, {"shop": shop}


def _woocommerce(values):
    from app.integrations.woocommerce import _valid_origin

    url = values["url"].strip().rstrip("/")
    key, secret = values["consumer_key"].strip(), values["consumer_secret"].strip()
    if not _valid_origin(url):
        raise CommerceError("Enter your store's https address without a path, such as https://yourstore.com.")
    if not key.startswith("ck_") or not secret.startswith("cs_"):
        raise CommerceError("Enter the consumer key (ck_…) and consumer secret (cs_…) from WooCommerce.")
    return ({"url": url, "consumer_key": key, "consumer_secret": secret},
            {"url": url, "consumer_key": site_integrations.mask(key)})


def _wordpress(values):
    from app.integrations.wordpress import _valid_rest_base

    base = values["base_url"].strip().rstrip("/")
    if base and not base.endswith("/wp-json/wp/v2"):
        base += "/wp-json/wp/v2"
    username, password = values["username"].strip(), values["application_password"].strip()
    if not _valid_rest_base(base):
        raise CommerceError("Enter your WordPress site's https address, such as https://yoursite.com.")
    if bool(username) != bool(password):
        raise CommerceError("Enter both a username and an application password, or leave both empty.")
    return ({"base_url": base, "username": username, "application_password": password},
            {"base_url": base, "username": username})


IMPORTERS = {
    "shopify": {
        "label": "Shopify",
        "fields": (("shop", "Shopify domain", "text", "yourstore.myshopify.com"),
                   ("token", "Admin API access token", "password", "")),
        "steps": ("In Shopify admin, open Settings → Apps and sales channels → Develop apps.",
                  "Create an app, give it read access to products and content, and install it.",
                  "Copy the Admin API access token and paste it here with your domain."),
        "clean": _shopify,
    },
    "woocommerce": {
        "label": "WooCommerce",
        "fields": (("url", "Store address", "url", "https://yourstore.com"),
                   ("consumer_key", "Consumer key", "text", "ck_…"),
                   ("consumer_secret", "Consumer secret", "password", "cs_…")),
        "steps": ("In WordPress admin, open WooCommerce → Settings → Advanced → REST API.",
                  "Add a key with Read permission.",
                  "Copy the consumer key and secret and paste them here."),
        "clean": _woocommerce,
    },
    "wordpress": {
        "label": "WordPress",
        "fields": (("base_url", "Site address", "url", "https://yoursite.com"),
                   ("username", "Username (optional)", "text", ""),
                   ("application_password", "Application password (optional)", "password", "")),
        "steps": ("Public pages and posts import with just your site address.",
                  "For drafts and private content, open Users → Profile in WordPress and create an "
                  "application password.",
                  "Paste your username and that application password here."),
        "clean": _wordpress,
    },
}


def register_importer_credential_routes(rt, actor, csrf, check_csrf, shell):
    def page_url(site_id, platform, message=""):
        query = "?" + urlencode({"notice": message[:250]}) if message else ""
        return f"/admin/sites/{site_id}/settings/integrations/importers/{platform}{query}"

    def owned(db, session, site_id, platform):
        if platform not in IMPORTERS:
            raise CommerceError("Choose Shopify, WooCommerce or WordPress.")
        return content.owned_site(db, site_id, actor(session), publish=True)

    @rt("/admin/sites/{site_id}/settings/integrations/importers/{platform}", methods=["GET"])
    def get(session, site_id: str, platform: str):
        with SessionLocal() as db:
            try:
                site = owned(db, session, site_id, platform)
            except CommerceError as exc:
                return PlainTextResponse(str(exc), status_code=400)
            spec = IMPORTERS[platform]
            row = site_integrations.credential_row(db, site, platform)
            hints = row.masked_hints_json if row and row.status == "connected" else None
            status = ("Connected: " + ", ".join(str(v) for v in hints.values() if v)
                      if hints else "Not connected")
            return shell(
                f"Import from {spec['label']}",
                Div(A("← Integrations", href=f"/admin/sites/{site.id}/integrations"), cls="e-actions"),
                Div(H2(f"Connect {spec['label']}"), P(status, role="status"),
                    Ol(*[Li(step) for step in spec["steps"]]),
                    Form(csrf(session),
                         *[Label(label, Input(name=name, type=kind, placeholder=hint,
                                              autocomplete="off" if kind != "password" else "new-password",
                                              spellcheck="false"))
                           for name, label, kind, hint in spec["fields"]],
                         Button("Save connection", cls="e-button primary"),
                         method="post", action=page_url(site.id, platform), cls="e-form"),
                    P("Saved details are encrypted and never shown again. Importing always starts "
                      "with a preview; nothing changes until you apply it.", cls="g-help"),
                    Form(csrf(session), Button("Disconnect", cls="e-button danger"), method="post",
                         action=page_url(site.id, platform) + "/disconnect") if hints else None,
                    cls="e-card"),
            )

    @rt("/admin/sites/{site_id}/settings/integrations/importers/{platform}", methods=["POST"])
    async def post(request, session, site_id: str, platform: str):
        form = await request.form()
        with SessionLocal() as db:
            try:
                check_csrf(session, form)
                site = owned(db, session, site_id, platform)
                spec = IMPORTERS[platform]
                values = {name: str(form.get(name, "")) for name, *_ in spec["fields"]}
                payload, hints = spec["clean"](values)
                try:
                    site_integrations.save(db, site, platform, payload, "sandbox", hints)
                except CommerceError as exc:
                    if "encryption" in str(exc).lower():
                        raise CommerceError("Secure storage for connections isn't set up on this "
                                            "server yet. Ask your FastShop operator to turn it on.") from exc
                    raise
                db.commit()
            except CommerceError as exc:
                db.rollback()
                return RedirectResponse(page_url(site_id, platform, str(exc)), status_code=303)
        return RedirectResponse(f"/admin/sites/{site_id}/integrations/import?" + urlencode(
            {"notice": f"{spec['label']} connected. Run a preview import to check it."}), status_code=303)

    @rt("/admin/sites/{site_id}/settings/integrations/importers/{platform}/disconnect", methods=["POST"])
    async def disconnect(request, session, site_id: str, platform: str):
        form = await request.form()
        with SessionLocal() as db:
            try:
                check_csrf(session, form)
                site = owned(db, session, site_id, platform)
                site_integrations.disconnect(db, site, platform)
                db.commit()
            except CommerceError as exc:
                return RedirectResponse(page_url(site_id, platform, str(exc)), status_code=303)
        return RedirectResponse(page_url(site_id, platform, "Disconnected."), status_code=303)
