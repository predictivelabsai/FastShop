"""Merchant Stripe Payments settings for a site."""

from __future__ import annotations

from urllib.parse import urlencode

from fasthtml.common import H2, H3, A, Button, Div, Form, Input, Label, Option, P, Select
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse

from app import content, stripe_connections
from app.config import settings
from app.db import SessionLocal
from app.models import SiteCommerceSettings
from app.services import CommerceError


def register_stripe_connection_routes(rt, actor, csrf, check_csrf, shell, error):
    def back(site_id, notice):
        return RedirectResponse(f"/admin/sites/{site_id}/commerce/payments?" +
                                urlencode({"notice": str(notice)[:300]}), status_code=303)

    def public_webhook_url(site):
        if site.hostname:
            return f"https://{site.hostname}/api/v1/commerce/{site.id}/stripe-webhook"
        if settings.public_url.startswith("https://"):
            return f"{settings.public_url}/api/v1/commerce/{site.id}/stripe-webhook"
        return ""

    @rt("/admin/sites/{site_id}/commerce/payments", methods=["GET"])
    def get(session, site_id: str, notice: str = ""):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id)
                config = db.scalar(select(SiteCommerceSettings).where(
                    SiteCommerceSettings.site_id == site.id,
                    SiteCommerceSettings.tenant_id == site.tenant_id,
                ))
                webhook_url = public_webhook_url(site)
                records = {mode: stripe_connections.connection_for(db, site, mode)
                           for mode in ("test", "live")}
                selected = "live" if config and config.mode == "live" else "test"
                tax_status = None
                if records[selected]:
                    try:
                        tax_status = stripe_connections.test_connection(db, site, selected)["tax_settings_active"]
                    except CommerceError:
                        tax_status = None
                cards = []
                for mode, record in records.items():
                    cards.append(Div(H3("Live mode" if mode == "live" else "Test mode"),
                        P("Connected to " + record.account_name + f" ({record.account_id}) · {record.account_country}"
                           if record and record.account_name else
                           "Connected to " + record.account_id + f" · {record.account_country}"
                           if record else "Not connected"),
                        P(f"Secret key ending {record.key_last_four} · publishable key ending {record.publishable_last_four}"
                          if record else "Keys are encrypted and are never shown again."),
                        P("Webhook endpoint registered" if record and record.webhook_endpoint_id else
                          "Webhook secret entered manually" if record else "Webhook not configured"),
                        P("Last verified " + record.verified_at.isoformat() if record and record.verified_at else "Not verified"),
                        P(("Live charges remain blocked until the platform operator verifies and accepts this connection."
                           if mode == "live" else "Test payments use Stripe test mode."), cls="e-note"),
                        Form(csrf(session), Input(type="hidden", name="mode", value=mode),
                             Button("Test connection", cls="e-button secondary"), method="post",
                             action=f"/admin/sites/{site.id}/commerce/payments/test") if record else None,
                        Form(csrf(session), Input(type="hidden", name="mode", value=mode),
                             Button("Disconnect", cls="e-button secondary"), method="post",
                             action=f"/admin/sites/{site.id}/commerce/payments/disconnect") if record else None,
                        cls="e-card"))
                publishable_configured = bool(records[selected])
                return shell("Payments", A("← Site settings", href=f"/admin/sites/{site.id}/settings"),
                    P(site.name + " · Stripe payments"),
                    P(notice, role="status", cls="e-note") if notice else None,
                    Div(H2("Connections"), *cards, cls="e-grid"),
                    Div(H2("Connect Stripe"),
                        P("Enter keys from your Stripe Dashboard. FastShop verifies the account and stores each value encrypted. Secrets cannot be viewed after saving."),
                        P("Stripe automatic tax is enabled at checkout by default unless merchant-configured local tax or gift tender applies. " +
                          ("Stripe Tax is active for this account." if tax_status is True else
                           "Stripe Tax is not active for this account." if tax_status is False else
                           "Stripe Tax status is currently unavailable. You can still save the connection.")),
                        A("Open Stripe Tax settings", href="https://dashboard.stripe.com/settings/tax",
                          target="_blank", rel="noreferrer") if tax_status is not True else None,
                        Form(csrf(session),
                            Label("Connection mode", Select(Option("Test mode", value="test", selected=selected == "test"),
                                Option("Live mode", value="live", selected=selected == "live"), name="mode")),
                            Label("Secret key or restricted key", Input(name="secret_key", type="password", autocomplete="new-password", required=True, placeholder="sk_test_… / sk_live_… / rk_test_… / rk_live_…")),
                            Label("Publishable key", Input(name="publishable_key", type="password", autocomplete="new-password", required=True, placeholder="pk_test_… / pk_live_…")),
                            Label("Webhook signing secret (manual fallback)", Input(name="webhook_secret", type="password", autocomplete="new-password", required=not bool(webhook_url), placeholder="whsec_…")),
                            P("Webhook URL: " + (webhook_url if webhook_url else "No public HTTPS URL is configured. Enter a webhook signing secret from Stripe manually."), cls="e-note"),
                            Button("Save and verify connection", cls="e-button"), method="post",
                            action=f"/admin/sites/{site.id}/commerce/payments/connect", cls="e-form"),
                        P("Selected publishable key: configured" if publishable_configured else "Selected publishable key: not configured", cls="e-help"),
                        cls="e-card"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/commerce/payments/connect", methods=["POST"])
    async def connect(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            mode = str(form.get("mode", ""))

            def store_connection():
                with SessionLocal() as db:
                    site = content.owned_site(db, site_id, user_id)
                    stripe_connections.save(db, site, user_id, mode,
                        str(form.get("secret_key", "")), str(form.get("publishable_key", "")),
                        str(form.get("webhook_secret", "")), webhook_url=public_webhook_url(site))
                    db.commit()

            await run_in_threadpool(store_connection)
            return back(site_id, "Stripe connection verified and saved.")
        except CommerceError as exc:
            return back(site_id, exc)

    @rt("/admin/sites/{site_id}/commerce/payments/test", methods=["POST"])
    async def test(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            mode = str(form.get("mode", ""))

            def verify_connection():
                with SessionLocal() as db:
                    site = content.owned_site(db, site_id, user_id)
                    return stripe_connections.test_connection(db, site, mode)

            status = await run_in_threadpool(verify_connection)
            return back(site_id, "Stripe " + status["mode"] + " connection is healthy.")
        except CommerceError as exc:
            return back(site_id, exc)

    @rt("/admin/sites/{site_id}/commerce/payments/disconnect", methods=["POST"])
    async def disconnect(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            mode = str(form.get("mode", ""))

            def disconnect_connection():
                with SessionLocal() as db:
                    site = content.owned_site(db, site_id, user_id)
                    stripe_connections.disconnect(db, site, mode)
                    db.commit()

            await run_in_threadpool(disconnect_connection)
            return back(site_id, "Stripe connection disconnected.")
        except CommerceError as exc:
            return back(site_id, exc)
