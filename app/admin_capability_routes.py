"""Legacy merchant capabilities presented inside store Settings and the v2 shell."""

from urllib.parse import urlencode

from fasthtml.common import A, Button, Div, Form, P, Script
from starlette.responses import RedirectResponse

from app import content, ui
from app.admin_ui import card
from app.db import SessionLocal
from app.integrations import fasterp
from app.services import CommerceError


def register_admin_capability_routes(rt, actor, csrf, check_csrf, shell, error):
    """Register alongside register_admin_routes using the same security callbacks."""

    @rt("/admin/sites/{site_id}/settings/integrations", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                base = f"/admin/sites/{site.id}"
                return shell("Integrations", *ui.integrations_hub(
                    db, base=base + "/settings/integrations"),
                    card(Div(A("Store connectors and import plans", href=base + "/integrations"),
                             A("Payment settings", href=base + "/settings/payments"),
                             cls="a-actions")))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/integrations/{provider}", methods=["GET"])
    def get(session, site_id: str, provider: str, message: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                base = f"/admin/sites/{site.id}/settings/integrations"
                if provider == "stripe":
                    children = ui.stripe_guide(base=base)
                elif provider == "woocommerce":
                    children = (*ui.woocommerce_guide(base=base), card(A(
                        "Open store connectors", href=f"/admin/sites/{site.id}/integrations")))
                elif provider == "paypal":
                    children = ui.paypal_guide(db, site_id=site.id,
                                              tenant_ids={site.tenant_id}, base=base)
                elif provider == "fasterp":
                    children = (*ui.integration_page(db, message[:300],
                                                      tenant_ids={site.tenant_id}),
                                card(Form(csrf(session), Button("Synchronise pending orders"),
                                          action=base + "/fasterp/sync", method="post")))
                else:
                    raise CommerceError("Provider not found.")
                return shell("Integration setup", *children)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/integrations/fasterp/sync", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                delivered, failed = fasterp.deliver_pending(db, tenant_ids={site.tenant_id})
                db.commit()
            message = f"Sync finished: {delivered} delivered, {failed} deferred or failed."
            return RedirectResponse(
                f"/admin/sites/{site.id}/settings/integrations/fasterp?"
                + urlencode({"message": message}), status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/channels", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                return shell("Channels", ui.admin_channels(db, tenant_ids={site.tenant_id}))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/developers", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                content.owned_site(db, site_id, actor(session), publish=True)
                return shell("Developers", ui.developers_page())
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/assistant", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                csrf(session)
                return shell("Assistant", P("Ask about orders, stock and FastERP sync."),
                    ui.assistant("merchant", session["csrf_token"],
                                 f"/admin/sites/{site.id}/assistant", embedded=True, inline=True),
                    Script(src=ui.static_asset("app.js"), defer=True))
        except CommerceError as exc:
            return error(exc)
