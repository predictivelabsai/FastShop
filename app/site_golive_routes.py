"""Merchant and operator surfaces for reviewed go-live transitions."""

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
    Small,
    Summary,
    Textarea,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.responses import RedirectResponse

from app import content, site_golive, stripe_connections
from app.admin_ui import empty_state
from app.config import settings
from app.db import SessionLocal
from app.models import Membership, SiteCommerceSettings, SiteStripeConnection, User
from app.services import CommerceError


def register_golive_routes(rt, actor, csrf, check_csrf, shell, error):
    def redirect(site_id: str, notice: str):
        return RedirectResponse(
            f"/admin/sites/{site_id}/golive?" + urlencode({"notice": notice[:500]}),
            status_code=303,
        )

    def checklist(report, *, scope: str):
        rows = []
        for check in report.checks:
            if check.scope != scope:
                continue
            rows.append(Div(
                Div(
                    Small("Pass" if check.passed else "Fix", cls="g-result g-pass" if check.passed else "g-result g-fail"),
                    Div(H3(check.label), P(check.explanation)),
                    cls="g-check-copy",
                ),
                A("Review setting", href=check.action_url, cls="g-check-action") if not check.passed else None,
                cls="g-check g-check-pass" if check.passed else "g-check g-check-fail",
            ))
        return Div(*rows, cls="g-checklist")

    @rt("/admin/sites/{site_id}/golive", methods=["GET"])
    def get(session, site_id: str, notice: str = ""):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                role = db.scalar(select(Membership.role).where(
                    Membership.tenant_id == site.tenant_id,
                    Membership.user_id == user_id,
                ))
                current_user = db.get(User, user_id)
                publish_report = site_golive.assess(db, site)
                commerce_report = site_golive.assess(db, site, commerce_requested=True)
                config = db.scalar(select(SiteCommerceSettings).where(
                    SiteCommerceSettings.site_id == site.id,
                    SiteCommerceSettings.tenant_id == site.tenant_id,
                ))
                tax_mode = "live" if config and config.mode == "live" else "test"
                tax = stripe_connections.tax_status(db, site, tax_mode)
                db.commit()
                operator = bool(
                    role == "admin"
                    and current_user
                    and current_user.email.lower() == settings.admin_email
                )
                audits = site_golive.audit_trail(db, site)
                users = {
                    user.id: user for user in db.scalars(select(User).where(
                        User.id.in_({entry.user_id for entry in audits}) if audits else False
                    ))
                }
                publish_controls = (
                    Form(
                        csrf(session),
                        Input(type="hidden", name="version", value=site.version),
                        Label("Review reason", Textarea(
                            name="reason",
                            rows=3,
                            minlength=10,
                            maxlength=500,
                            required=True,
                            placeholder="What was reviewed, and why is this site ready?",
                        )),
                        Label(Input(type="checkbox", name="confirmed", required=True),
                              " I reviewed the checklist and the public snapshots."),
                        Label(Input(type="checkbox", name="override"),
                              " Development operator override — publish with failing checks and record every failure.")
                        if operator and not settings.is_production else None,
                        Button("Publish site", cls="e-button"),
                        method="post",
                        action=f"/admin/sites/{site.id}/golive/publish",
                        cls="e-form",
                    )
                    if site.status != "published" else
                    P("This site is published. Draft editing remains available without changing the public snapshots.", cls="g-state g-state-live")
                )
                domain_disabled = site.status != "published"
                domain_form = Form(
                    csrf(session),
                    Input(type="hidden", name="version", value=site.version),
                    Label("Custom hostname", Input(
                        name="hostname",
                        value=site.hostname or "",
                        placeholder="shop.example.com",
                        maxlength=253,
                        disabled=domain_disabled,
                    )),
                    P("Enter a hostname only. DNS and TLS are provisioned at the platform; this form never calls a DNS provider. Clear the field to unbind."),
                    Button("Save custom domain", cls="e-button", disabled=domain_disabled),
                    method="post",
                    action=f"/admin/sites/{site.id}/golive/domain",
                    cls="e-form",
                )
                commerce_mode = config.mode if config else "disabled"
                live_connection = db.scalar(select(SiteStripeConnection).where(
                    SiteStripeConnection.site_id == site.id,
                    SiteStripeConnection.tenant_id == site.tenant_id,
                    SiteStripeConnection.mode == "live",
                ))
                live_ready = False
                if live_connection and live_connection.verified_at and live_connection.account_id:
                    try:
                        live_secret, live_webhook, live_publishable = stripe_connections.decrypted(
                            db, site, live_connection
                        )
                        live_ready = bool(
                            config and site.status == "published" and site.hostname
                            and live_secret.startswith(("sk_live_", "rk_live_"))
                            and live_publishable.startswith("pk_live_")
                            and (live_connection.webhook_endpoint_id or live_webhook)
                        )
                    except CommerceError:
                        live_ready = False
                commerce_form = (Form(
                    csrf(session),
                    Input(type="hidden", name="version", value=site.version),
                    Input(type="hidden", name="config_version", value=config.version if config else ""),
                    Input(type="hidden", name="enabled", value="0" if commerce_mode == "sandbox" else "1"),
                    Button(
                        "Disable sandbox commerce" if commerce_mode == "sandbox" else "Enable sandbox commerce",
                        cls=None if commerce_mode == "sandbox" else "e-button",
                        disabled=config is None,
                    ),
                    method="post",
                    action=f"/admin/sites/{site.id}/golive/commerce",
                ) if commerce_mode != "live" else
                    P("Use the live payments control below to switch back to test mode."))
                audit_nodes = []
                for entry in audits:
                    event = site_golive.audit_event(entry)
                    failed = [item["label"] for item in event.get("checks", []) if not item.get("passed")]
                    person = users.get(entry.user_id)
                    audit_nodes.append(Details(
                        Summary(
                            f"{event.get('action', 'transition')} · {event.get('decision', 'recorded')} · "
                            f"{entry.created_at.strftime('%Y-%m-%d %H:%M UTC')}"
                        ),
                        P("Actor: " + (person.name or person.email if person else entry.user_id)),
                        P("Reason: " + str(event.get("reason", "Not recorded"))),
                        P("Failed checks: " + ", ".join(failed)) if failed else P("All recorded checks passed."),
                        P("Development override recorded.", cls="g-override") if event.get("overridden") else None,
                        cls="g-audit",
                    ))
                return shell(
                    "Go-live review",
                    A("← Site settings", href=f"/admin/sites/{site.id}"),
                    P(f"{site.name} · Site status: {site.status} · Commerce: {commerce_mode}", cls="g-summary"),
                    P("Live payments on" if commerce_mode == "live" else "Live payments off, test mode",
                      cls="g-state g-state-live" if commerce_mode == "live" else "g-state"),
                    stripe_connections.stripe_tax_status_line(tax, tax_mode),
                    Div(H2("Live payments", id="live-payments"),
                        P("A verified live Stripe connection is required to switch modes. The checks below are advisories and do not prevent live payments."),
                        checklist(commerce_report, scope="commerce"),
                        Form(csrf(session), Input(type="hidden", name="version", value=site.version),
                             Input(type="hidden", name="config_version", value=config.version if config else ""),
                             Input(type="hidden", name="enabled", value="0" if commerce_mode == "live" else "1"),
                             Button("Switch back to test mode" if commerce_mode == "live" else "Turn on live payments",
                                    cls="e-button", disabled=not live_ready if commerce_mode != "live" else False),
                             method="post", action=f"/admin/sites/{site.id}/golive/live",
                             onsubmit="return confirm('Real cards will be charged. Continue?')" if commerce_mode != "live" else None,
                             cls="e-form"),
                        P("Connect and verify live Stripe keys to enable this control.", cls="e-help") if not live_ready and commerce_mode != "live" else None,
                        A("Manage Stripe connection", href=f"/admin/sites/{site.id}/commerce/payments"),
                        cls="e-card g-stage"),
                    A("Open operator live-payment ceremony →",
                      href=f"/admin/platform/sites/{site.id}/live-credentials") if operator else None,
                    P(notice, role="status", cls="e-note") if notice else None,
                    Div(
                        H2("Publish the reviewed site", id="publish"),
                        P("Publication uses only stored site state. Merchants cannot bypass failed hard checks."),
                        checklist(publish_report, scope="publish"),
                        publish_controls,
                        cls="e-card g-stage",
                    ),
                    Div(
                        H2("Bind the custom domain", id="domain"),
                        P(f"The bounded preview stays at /sites/{site.slug}/. Custom-host traffic opens only after publication."),
                        domain_form,
                        cls="e-card g-stage",
                    ),
                    Div(
                        H2("Enable sandbox commerce", id="commerce"),
                        P("Catalog and checkout checks run only for this request. This slice cannot accept live credentials or live payments."),
                        checklist(commerce_report, scope="commerce"),
                        Div(A("Configure sandbox commerce", href=f"/admin/sites/{site.id}/commerce"), commerce_form, cls="e-actions"),
                        cls="e-card g-stage",
                    ),
                    Div(H2("Payment and go-live activity"),
                        *audit_nodes if audit_nodes else [empty_state("No go-live decisions yet",
                            "Publication and commerce decisions will appear here after a review.",
                            A("Review publication", href="#publish", cls="e-button"))],
                        cls="e-card g-stage"),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/golive/publish", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            if form.get("confirmed") != "on":
                raise CommerceError("Confirm that you reviewed the checklist and public snapshots.")
            with SessionLocal() as db:
                decision = site_golive.publish_site(
                    db,
                    site_id,
                    actor(session),
                    int(form.get("version", 0)),
                    str(form.get("reason", "")),
                    override=form.get("override") == "on",
                )
                db.commit()
            return redirect(site_id, decision.message)
        except (CommerceError, ValueError) as exc:
            return redirect(site_id, str(exc))

    @rt("/admin/sites/{site_id}/golive/domain", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                decision = site_golive.bind_hostname(
                    db,
                    site_id,
                    actor(session),
                    int(form.get("version", 0)),
                    str(form.get("hostname", "")),
                )
                db.commit()
            return redirect(site_id, decision.message)
        except (CommerceError, ValueError, IntegrityError) as exc:
            message = "That hostname was bound by another site. Reload and choose another." if isinstance(exc, IntegrityError) else str(exc)
            return redirect(site_id, message)

    @rt("/admin/sites/{site_id}/golive/commerce", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                decision = site_golive.set_sandbox_commerce(
                    db,
                    site_id,
                    actor(session),
                    int(form.get("version", 0)),
                    form.get("enabled") == "1",
                    config_version=int(form.get("config_version")) if form.get("config_version") else None,
                )
                db.commit()
            return redirect(site_id, decision.message)
        except (CommerceError, ValueError) as exc:
            return redirect(site_id, str(exc))

    @rt("/admin/sites/{site_id}/golive/live", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                decision = site_golive.switch_live_payments(
                    db, site_id, actor(session), int(form.get("version", 0)),
                    int(form.get("config_version")) if form.get("config_version") else None,
                    form.get("enabled") == "1",
                )
                db.commit()
            return redirect(site_id, decision.message)
        except (CommerceError, ValueError) as exc:
            return redirect(site_id, str(exc))
