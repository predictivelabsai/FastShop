"""Merchant Stripe Payments settings for a site."""

from __future__ import annotations

from urllib.parse import urlencode

from fasthtml.common import (
    H2,
    H3,
    A,
    Button,
    Details,
    Div,
    Form,
    Input,
    Label,
    P,
    Script,
    Span,
    Summary,
)
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse

from app import content, stripe_connections
from app.config import settings
from app.db import SessionLocal
from app.models import SiteCommerceSettings
from app.services import CommerceError


def register_stripe_connection_routes(rt, actor, csrf, check_csrf, shell, error):
    def back(site_id, message, *, failed=False):
        key = "failure" if failed else "notice"
        return RedirectResponse(f"/admin/sites/{site_id}/commerce/payments?" +
                                urlencode({key: str(message)[:300]}), status_code=303)

    def public_webhook_url(site):
        if site.hostname:
            return f"https://{site.hostname}/api/v1/commerce/{site.id}/stripe-webhook"
        if settings.public_url.startswith("https://"):
            return f"{settings.public_url}/api/v1/commerce/{site.id}/stripe-webhook"
        return ""

    @rt("/admin/sites/{site_id}/commerce/payments", methods=["GET"])
    def get(session, site_id: str, notice: str = "", failure: str = ""):
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
                tax = stripe_connections.tax_status(db, site, selected)
                db.commit()
                cards = []
                for mode, record in records.items():
                    mode_name = "Live mode" if mode == "live" else "Test mode"
                    account = (record.account_name or record.account_id) if record else ""
                    verified = bool(record and record.verified_at)
                    card_actions = []
                    if record:
                        card_actions.extend([
                            Form(csrf(session), Input(type="hidden", name="mode", value=mode),
                                 Button("Test connection", cls="e-button"), method="post",
                                 action=f"/admin/sites/{site.id}/commerce/payments/test"),
                            Form(csrf(session), Input(type="hidden", name="mode", value=mode),
                                 Button("Disconnect", cls="e-button secondary"), method="post",
                                 action=f"/admin/sites/{site.id}/commerce/payments/disconnect"),
                        ])
                    card_actions.append(A("Update keys" if record else "Connect", href="#connect-stripe",
                                          cls="e-button primary", **{"data-mode": mode}))
                    cards.append(Div(
                        Div(H3(mode_name), Span("Connected" if record else "Not connected",
                                                cls="payment-pill connected" if record else "payment-pill"),
                            cls="payment-card-heading"),
                        P(account, cls="payment-account") if record else P("Link your Stripe account to accept payments."),
                        P("Live charges stay blocked until this connection is reviewed and accepted.",
                          cls="payment-live-note") if mode == "live" else None,
                        Div(P("Webhook", Span("Endpoint registered" if record and record.webhook_endpoint_id
                                               else "Secret added manually" if record else "Not configured")),
                            P("Verification", Span("Verified" if verified else "Not verified")),
                            cls="payment-status-list"),
                        P(f"Keys ending {record.key_last_four} / {record.publishable_last_four} · encrypted"
                          if record else "Keys are encrypted and never shown again.", cls="e-help"),
                        P("Stripe connection verified and saved.", role="status", cls="payment-card-confirmation")
                        if record and mode == "test" and "verified and saved" in notice else None,
                        Div(*card_actions, cls="payment-card-actions"),
                        cls="e-card payment-status-card"))

                tax_line = stripe_connections.stripe_tax_status_line(tax, selected)
                expected_key = "pk_live_… / sk_live_… / rk_live_…" if selected == "live" else "pk_test_… / sk_test_… / rk_test_…"
                error_panel = Div(H3("Stripe could not verify these keys"), P(failure),
                                  role="alert", cls="payment-error") if failure else None
                return shell("Payments", A("← Site settings", href=f"/admin/sites/{site.id}/settings"),
                    P(site.name + " · Stripe connection settings", cls="payment-intro"),
                    Div(H2("Connections"), Div(*cards, cls="payment-status-grid"), cls="payment-connections"),
                    Div(H2("Connect Stripe", id="connect-stripe"),
                        P("Stripe Tax: on by default."),
                        tax_line,
                        A("Open Stripe Tax settings", href="https://dashboard.stripe.com/settings/tax",
                          target="_blank", rel="noopener", cls="payment-tax-link"),
                        error_panel,
                        Form(csrf(session),
                            Input(type="hidden", name="mode", value=selected, id="stripe-connection-mode"),
                            P("Step 1", cls="payment-step-label"),
                            Div(Label(Input(type="radio", name="payment_mode", value="test", checked=selected == "test"), Span("Test"),
                                       cls="payment-mode-option"),
                                Label(Input(type="radio", name="payment_mode", value="live", checked=selected == "live"), Span("Live"),
                                       cls="payment-mode-option"),
                                cls="payment-mode-switch", role="radiogroup", aria_label="Connection mode"),
                            P("Step 2 · Open your Stripe API keys", cls="payment-step-label"),
                            A("Open Stripe API keys", href="https://dashboard.stripe.com/test/apikeys" if selected == "test"
                              else "https://dashboard.stripe.com/apikeys", target="_blank", rel="noopener",
                              cls="e-button", id="stripe-keys-link"),
                            P("Step 3 · Paste your API keys", cls="payment-step-label"),
                            Div(Label("Publishable key", Input(name="publishable_key", type="password",
                                autocomplete="new-password", required=True, placeholder="pk_test_…" if selected == "test" else "pk_live_…",
                                pattern="pk_test_[A-Za-z0-9_]{8,}" if selected == "test" else "pk_live_[A-Za-z0-9_]{8,}")),
                                Label("Secret key or restricted key", Input(name="secret_key", type="password",
                                autocomplete="new-password", required=True, placeholder="sk_test_… or rk_test_…" if selected == "test" else "sk_live_… or rk_live_…",
                                pattern="(sk|rk)_test_[A-Za-z0-9_]{8,}" if selected == "test" else "(sk|rk)_live_[A-Za-z0-9_]{8,}")),
                                cls="payment-key-fields"),
                            P("Use " + expected_key + ". Keys are encrypted and never shown again.", cls="e-help", id="payment-key-hint"),
                            Details(Summary("Advanced · webhook settings"),
                                P("Stripe creates the webhook endpoint automatically when a public HTTPS URL is available."),
                                Div(Label("Webhook URL", Input(value=webhook_url, readonly=True, id="stripe-webhook-url")
                                          if webhook_url else P("No public HTTPS URL is configured.")),
                                    Button("Copy URL", type="button", cls="e-button", id="copy-webhook-url") if webhook_url else None,
                                    cls="payment-webhook-row"),
                                Label("Webhook signing secret (manual fallback)", Input(name="webhook_secret", type="password",
                                    autocomplete="new-password", placeholder="whsec_…", required=not bool(webhook_url))),
                                cls="payment-advanced"),
                            Button("Connect Stripe", cls="e-button primary payment-submit"),
                            method="post", action=f"/admin/sites/{site.id}/commerce/payments/connect", cls="e-form payment-connect-form"),
                        Script("""(() => {
const form=document.querySelector('.payment-connect-form'); if(!form)return;
const radios=form.querySelectorAll('input[name=payment_mode]');
const publishable=form.querySelector('[name=publishable_key]'), secret=form.querySelector('[name=secret_key]');
const selectedMode=form.querySelector('#stripe-connection-mode');
const link=document.getElementById('stripe-keys-link'), hint=document.getElementById('payment-key-hint');
function update(){const chosen=form.querySelector('[name=payment_mode]:checked')||radios[0]; if(!chosen)return; const live=chosen.value==='live'; selectedMode.value=live?'live':'test';
const suffix=live?'live':'test'; link.href=live?'https://dashboard.stripe.com/apikeys':'https://dashboard.stripe.com/test/apikeys';
publishable.placeholder='pk_'+suffix+'_…'; secret.placeholder='sk_'+suffix+'_… or rk_'+suffix+'_…';
publishable.pattern='pk_'+suffix+'_[A-Za-z0-9_]{8,}'; secret.pattern='(sk|rk)_'+suffix+'_[A-Za-z0-9_]{8,}';
hint.textContent='Use pk_'+suffix+'_… / sk_'+suffix+'_… / rk_'+suffix+'_…. Keys are encrypted and never shown again.';}
radios.forEach(r=>r.addEventListener('change',update)); update();
document.querySelectorAll('.payment-card-actions a[data-mode]').forEach(a=>a.addEventListener('click',()=>{
const radio=form.querySelector('input[name=payment_mode][value='+a.dataset.mode+']'); if(radio){radio.checked=true; radio.dispatchEvent(new Event('change',{bubbles:true}));}
}));
const copy=document.getElementById('copy-webhook-url'); if(copy)copy.addEventListener('click',async()=>{await navigator.clipboard.writeText(document.getElementById('stripe-webhook-url').value); copy.textContent='Copied';});
})();"""),
                        cls="e-card payment-connect-card"))
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
            return back(site_id, exc, failed=True)

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
                    result = stripe_connections.test_connection(db, site, mode)
                    db.commit()
                    return result

            status = await run_in_threadpool(verify_connection)
            return back(site_id, "Stripe " + status["mode"] + " connection is healthy.")
        except CommerceError as exc:
            return back(site_id, exc, failed=True)

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
            return back(site_id, exc, failed=True)
