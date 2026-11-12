"""Admin settings and public recovery endpoint for checkout email automation."""

from __future__ import annotations

from fasthtml.common import (
    H2,
    A,
    Button,
    Div,
    Form,
    Input,
    Label,
    P,
    Small,
    Textarea,
)
from sqlalchemy import select
from starlette.responses import RedirectResponse, Response

from app import content, marketing_automation, marketing_email
from app.db import SessionLocal
from app.models import Site
from app.services import CommerceError


def register_marketing_automation_routes(rt, actor, csrf, check_csrf, shell, error):
    def admin_base(site_id):
        return f"/admin/sites/{site_id}/marketing/automations"

    def default_values(automation):
        return {
            "enabled": automation.enabled if automation else False,
            "delay_hours": automation.delay_hours if automation else 4,
            "subject": automation.subject if automation else "Your basket is waiting",
            "body_html": automation.body_html if automation else (
                "<p>You left a basket in our shop. You can return to it when you are ready.</p>"
            ),
            "include_recovery_link": automation.include_recovery_link if automation else True,
        }

    def automation_form(site, automation, csrf_field, recovered_count, *, notice=""):
        values = default_values(automation)
        address = marketing_email._store_address(site)
        return (
            A("← Marketing", href=f"/admin/sites/{site.id}/marketing"),
            P(notice, role="status") if notice else None,
            P("Add the store's physical address in store settings before sending.",
              cls="e-note", role="status") if not address else None,
            Div(
                H2("Automation is on" if values["enabled"] else "Automation is off"),
                cls="e-card a-automation-status",
            ),
            P(f"Recovered orders: {recovered_count}", cls="a-automation-recovered"),
            Form(
                csrf_field(),
                Label(Input(type="checkbox", name="enabled", checked=values["enabled"]),
                      " Enable abandoned checkout email"),
                Label("Delay after checkout (hours)", Input(
                    type="number", name="delay_hours", min=1, max=72, required=True,
                    value=str(values["delay_hours"]),
                )),
                Label("Subject", Input(name="subject", type="text", maxlength=200, required=True,
                                        value=values["subject"])),
                Label("Email body", Textarea(values["body_html"], name="body_html", rows=12,
                                              maxlength=20000, required=True)),
                Small("Use paragraphs, headings, lists, emphasis, quotations and safe links."),
                Label(Input(type="checkbox", name="include_recovery_link",
                            checked=values["include_recovery_link"]),
                      " Include a secure cart recovery link"),
                Button("Save automation", cls="a-button"),
                method="post", action=admin_base(site.id), cls="e-form a-automation-form",
            ),
            P("Only customers with an active marketing subscription or checkout consent receive this email. "
              "Subscription status is checked again before sending."),
        )

    @rt("/admin/sites/{site_id}/marketing/automations", methods=["GET"])
    def get_automation(session, request, site_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                automation = marketing_automation.automation_for(db, site)
                recovered = marketing_automation.recovered_order_count(db, site)
                page = automation_form(site, automation, lambda: csrf(session), recovered, notice=notice)
                return shell("Abandoned checkout emails", *page)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/automations", methods=["POST"])
    async def save(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                marketing_automation.save_automation(db, site, dict(form))
                db.commit()
            return RedirectResponse(admin_base(site_id) + "?notice=Automation+saved", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/sites/{slug}/cart/recover", methods=["GET"])
    def recover(session, request, slug: str, token: str = ""):
        try:
            with SessionLocal() as db:
                site = db.scalar(select(Site).where(
                    Site.slug == slug, Site.status.in_(("preview", "published")),
                ))
                if not site:
                    raise CommerceError("This basket recovery link is unavailable.")
                from app.checkout_services import lock_site

                lock_site(db, site)
                marketing_automation.restore_cart(db, site, token, session)
                db.commit()
                root = request.scope.get("site_base", "/sites/" + site.slug)
                return RedirectResponse(root + "/cart", status_code=303, headers={
                    "Cache-Control": "private, no-store",
                    "Referrer-Policy": "no-referrer",
                })
        except CommerceError as exc:
            return Response(str(exc), status_code=400, headers={
                "Cache-Control": "private, no-store",
                "Referrer-Policy": "no-referrer",
            })
