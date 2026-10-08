"""Operator-only Stripe live credential and acceptance ceremony routes."""

from __future__ import annotations

import hmac
from urllib.parse import urlencode

from fasthtml.common import H2, H3, A, Button, Div, Form, Input, Label, P, Small, Span, Textarea
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse

from app import live_credentials, site_golive
from app.config import settings
from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway
from app.models import Membership, Site, SiteCommerceSettings, User
from app.services import CommerceError


def register_live_credential_routes(rt, actor, csrf, check_csrf, shell, error):
    def redirect(site_id: str, notice: str):
        return RedirectResponse(
            f"/admin/platform/sites/{site_id}/live-credentials?" +
            urlencode({"notice": str(notice)[:500]}), status_code=303)

    def operator_sites(db, user_id: str):
        user = db.get(User, user_id)
        if not user or not hmac.compare_digest(user.email.lower(), settings.admin_email):
            raise CommerceError("Platform operator access is required.")
        return list(db.scalars(select(Site).join(
            Membership, Membership.tenant_id == Site.tenant_id
        ).where(Membership.user_id == user_id, Membership.role == "admin").order_by(Site.name)))

    @rt("/admin/platform/live-credentials", methods=["GET"])
    def get(session):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                rows = []
                for site in operator_sites(db, user_id):
                    config = db.scalar(select(SiteCommerceSettings).where(
                        SiteCommerceSettings.site_id == site.id,
                        SiteCommerceSettings.tenant_id == site.tenant_id))
                    state = live_credentials.state_for(db, site, config)
                    label = ("Live accepted" if state.accepted else
                             "Verified, awaiting acceptance" if state.verified else
                             "Stored, verification required" if state.configured else "Not configured")
                    rows.append(Div(
                        Div(H2(site.name), Span(label, cls="i-state " +
                            ("i-state-ready" if state.accepted else "i-state-missing")), cls="i-heading"),
                        P(f"Site status: {site.status} · Commerce: {config.mode if config else 'disabled'}"),
                        A("Open live payment ceremony →",
                          href=f"/admin/platform/sites/{site.id}/live-credentials"), cls="e-card"))
                return shell("Live payment integrations",
                    A("← Sites & content", href="/admin/sites"),
                    P("Only the configured platform operator can store, verify, accept, or disable live Stripe credentials."),
                    Div(*rows, cls="e-grid") if rows else P("No operator-owned sites are available."))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/platform/sites/{site_id}/live-credentials", methods=["GET"])
    def get(session, site_id: str, notice: str = ""):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                site = live_credentials.require_operator(db, site_id, user_id)
                config = db.scalar(select(SiteCommerceSettings).where(
                    SiteCommerceSettings.site_id == site.id,
                    SiteCommerceSettings.tenant_id == site.tenant_id))
                if not config:
                    raise CommerceError("Save sandbox commerce settings before configuring live payments.")
                state = live_credentials.state_for(db, site, config)
                creator = db.get(User, state.created_by) if state.created_by else None
                report = site_golive.assess(db, site, commerce_requested=True)
                checks = [Div(
                    Span("Pass" if item.passed else "Fix",
                         cls="g-result g-pass" if item.passed else "g-result g-fail"),
                    Div(H3(item.label), P(item.explanation)),
                    cls="g-check g-check-pass" if item.passed else "g-check g-check-fail")
                    for item in report.checks]
                identity = (P(
                    f"{state.masked_key} · key ID {state.credential_id} · created by "
                    f"{creator.name or creator.email if creator else state.created_by} · "
                    f"{state.created_at.strftime('%Y-%m-%d %H:%M UTC') if state.created_at else 'time unavailable'}",
                    cls="g-state") if state.configured else
                    P("No live credentials are stored for this site.", cls="g-state"))
                credential_form = Form(csrf(session),
                    Label("Stripe live secret key", Input(type="password", name="secret_key",
                        required=True, autocomplete="new-password", placeholder="sk_live_…")),
                    Label("Webhook signing secret", Input(type="password", name="webhook_secret",
                        required=True, autocomplete="new-password", placeholder="whsec_…")),
                    P("Saving replaces the encrypted values and clears verification. Values are never redisplayed."),
                    Button("Store encrypted credentials", cls="e-button"), method="post",
                    action=f"/admin/platform/sites/{site.id}/live-credentials/store", cls="e-form")
                verify_form = Form(csrf(session),
                    Input(type="hidden", name="credential_id", value=state.credential_id),
                    Button("Verify against Stripe live API", cls="e-button",
                           disabled=not state.configured or state.accepted),
                    P("This operator-driven read-only account lookup has a bounded timeout and never runs at boot."),
                    method="post", action=f"/admin/platform/sites/{site.id}/live-credentials/verify",
                    cls="e-form")
                acceptance_form = Form(csrf(session),
                    Input(type="hidden", name="site_version", value=site.version),
                    Input(type="hidden", name="config_version", value=config.version),
                    Input(type="hidden", name="credential_id", value=state.credential_id),
                    Label("Acceptance reason", Textarea(name="reason", rows=3, minlength=10,
                        maxlength=500, required=True,
                        placeholder="What was verified, by whom, and why is live payment acceptance approved?")),
                    Label(Input(type="checkbox", name="confirmed", required=True),
                          " I confirm the published site, bound domain, sandbox checkout, and verified live account are ready."),
                    Button("Accept live payments", cls="e-button", disabled=state.accepted
                           or not state.verified or not report.passed or config.mode != "sandbox"),
                    method="post", action=f"/admin/platform/sites/{site.id}/live-credentials/accept",
                    cls="e-form")
                disable_form = Form(csrf(session),
                    Input(type="hidden", name="site_version", value=site.version),
                    Input(type="hidden", name="config_version", value=config.version),
                    Label("Disable reason", Textarea(name="reason", rows=2, minlength=10,
                        maxlength=500, required=True)),
                    Button("Disable live payments"),
                    P("Disabling reverts checkout to sandbox and requires new verification and acceptance before live can return."),
                    method="post", action=f"/admin/platform/sites/{site.id}/live-credentials/disable",
                    cls="e-form") if state.accepted else None
                return shell("Stripe live payment acceptance",
                    A("← Live payment integrations", href="/admin/platform/live-credentials"),
                    P(f"{site.name} · Site: {site.status} · Commerce: {config.mode}"),
                    P(notice, role="status", cls="e-note") if notice else None,
                    Div(H2("Stored credential"), identity,
                        Small("Webhook secret: configured" if state.configured else "Webhook secret: not configured"),
                        credential_form, cls="e-card"),
                    Div(H2("Provider verification"),
                        P("Verified" if state.verified else "Not verified", cls="g-state"),
                        verify_form, cls="e-card"),
                    Div(H2("Acceptance checklist"), Div(*checks, cls="g-checklist"),
                        acceptance_form if not state.accepted else
                        P("Live payments are approved by the platform operator.", cls="g-state g-state-live"),
                        disable_form, cls="e-card"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/platform/sites/{site_id}/live-credentials/store", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                live_credentials.store(db, site_id, actor(session),
                    str(form.get("secret_key", "")), str(form.get("webhook_secret", "")))
                db.commit()
            return redirect(site_id, "Live credentials stored encrypted. Verify them before acceptance.")
        except CommerceError as exc:
            return redirect(site_id, exc)

    @rt("/admin/platform/sites/{site_id}/live-credentials/verify", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)

            def verify():
                with SessionLocal() as db:
                    site = live_credentials.require_operator(db, site_id, user_id)
                    credential = live_credentials.credential_for(db, site)
                    if not credential or credential.id != str(form.get("credential_id", "")):
                        raise CommerceError("Live credentials changed. Reload before verification.")
                    key, webhook = live_credentials.decrypted(db, site, credential)
                    StripeGateway.for_live_validation(key, webhook).validate_live_account()
                    live_credentials.mark_verified(db, site.id, user_id, credential.id)
                    db.commit()

            await run_in_threadpool(verify)
            return redirect(site_id, "Stripe live account verified. Acceptance is still required.")
        except CommerceError as exc:
            return redirect(site_id, exc)

    @rt("/admin/platform/sites/{site_id}/live-credentials/accept", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            if form.get("confirmed") != "on":
                raise CommerceError("Confirm every live-payment acceptance statement.")
            with SessionLocal() as db:
                live_credentials.accept(db, site_id, actor(session),
                    int(form.get("site_version", 0)), int(form.get("config_version", 0)),
                    str(form.get("credential_id", "")), str(form.get("reason", "")))
                db.commit()
            return redirect(site_id, "Live payments accepted for this site.")
        except (CommerceError, ValueError) as exc:
            return redirect(site_id, exc)

    @rt("/admin/platform/sites/{site_id}/live-credentials/disable", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                live_credentials.disable(db, site_id, actor(session),
                    int(form.get("site_version", 0)), int(form.get("config_version", 0)),
                    str(form.get("reason", "")))
                db.commit()
            return redirect(site_id, "Live payments disabled; checkout reverted to sandbox.")
        except (CommerceError, ValueError) as exc:
            return redirect(site_id, exc)
