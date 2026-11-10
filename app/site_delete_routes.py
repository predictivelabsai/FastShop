"""Recoverable deletion and restoration of tenant stores."""

from datetime import UTC, datetime

from fasthtml.common import (
    H2,
    A,
    Button,
    Div,
    Form,
    Input,
    Label,
    P,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Tr,
)
from sqlalchemy import select
from starlette.responses import RedirectResponse

from app.admin_gap_models import AdminActivity
from app.db import SessionLocal
from app.models import (
    CheckoutAttempt,
    Membership,
    Site,
    SiteCommerceSettings,
    SiteStripeConnection,
    User,
)
from app.services import CommerceError


def register_site_delete_routes(rt, actor, csrf, check_csrf, shell, error):
    def managed_site(db, site_id, user_id):
        site = db.scalar(
            select(Site)
            .join(Membership, Membership.tenant_id == Site.tenant_id)
            .where(
                Site.id == site_id,
                Membership.user_id == user_id,
                Membership.role.in_(("admin", "merchant")),
                Site.status != "deleted",
            )
        )
        if not site:
            raise CommerceError("Site not found or access denied.")
        return site

    @rt("/admin/sites/{site_id}/settings/delete", methods=["GET"])
    def confirmation(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = managed_site(db, site_id, actor(session))
                return shell(
                    "Delete store",
                    A("← General settings", href=f"/admin/sites/{site.id}/settings/general"),
                    Div(
                        H2("Delete this store"),
                        P(f"Type {site.name} exactly to confirm."),
                        P(
                            "This store will be hidden and its hostname released. You can restore it from platform administration."
                        ),
                        Form(
                            csrf(session),
                            Input(type="hidden", name="version", value=site.version),
                            Label(
                                f"Type {site.name} to confirm deletion",
                                Input(
                                    name="confirmation", required=True, autocomplete="off",
                                    placeholder=site.name,
                                ),
                            ),
                            Button("Delete this store", cls="e-button danger", type="submit"),
                            method="post",
                            action=f"/admin/sites/{site.id}/settings/delete",
                            cls="e-form",
                        ),
                        cls="e-card danger-zone",
                        data_delete_confirmation=True,
                    ),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/delete", methods=["POST"])
    async def delete(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            with SessionLocal() as db:
                site = managed_site(db, site_id, user_id)
                site = db.scalar(
                    select(Site)
                    .where(Site.id == site.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if str(form.get("version", "")) != str(site.version):
                    raise CommerceError(
                        "Store settings changed. Reload before deleting this store."
                    )
                if str(form.get("confirmation", "")) != site.name:
                    raise CommerceError("The store name did not match. No changes were made.")
                commerce = db.scalar(
                    select(SiteCommerceSettings).where(
                        SiteCommerceSettings.site_id == site.id,
                        SiteCommerceSettings.tenant_id == site.tenant_id,
                    )
                )
                live_connection = db.scalar(
                    select(SiteStripeConnection.id).where(
                        SiteStripeConnection.site_id == site.id,
                        SiteStripeConnection.tenant_id == site.tenant_id,
                        SiteStripeConnection.mode == "live",
                    )
                )
                if (commerce and commerce.mode == "live") or live_connection:
                    raise CommerceError(
                        "Switch live payments off in this store's Payments settings first."
                    )
                from app.checkout_services import ACTIVE

                active_attempt = db.scalar(
                    select(CheckoutAttempt.id)
                    .where(
                        CheckoutAttempt.site_id == site.id,
                        CheckoutAttempt.tenant_id == site.tenant_id,
                        CheckoutAttempt.state.in_(ACTIVE),
                        CheckoutAttempt.expires_at > datetime.now(UTC),
                    )
                    .limit(1)
                )
                if active_attempt:
                    raise CommerceError(
                        "A checkout is in progress. Wait for it to finish before deleting this store."
                    )
                site.status = "deleted"
                site.hostname = None
                site.version += 1
                if commerce:
                    commerce.mode = "disabled"
                    commerce.version += 1
                db.add(
                    AdminActivity(
                        tenant_id=site.tenant_id,
                        site_id=site.id,
                        actor_id=user_id,
                        object_type="site",
                        object_id=site.id,
                        action="deleted",
                        summary="Store deleted and retained for recovery.",
                    )
                )
                db.commit()
            return RedirectResponse("/admin/sites?notice=Store%20deleted", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/platform/deleted-stores", methods=["GET"])
    def deleted_stores(session):
        try:
            with SessionLocal() as db:
                user = db.get(User, actor(session))
                from app.config import settings

                if not user or user.email.lower() != settings.admin_email:
                    raise CommerceError("Platform operator access is required.")
                sites = list(
                    db.scalars(
                        select(Site).where(Site.status == "deleted").order_by(Site.name, Site.id)
                    )
                )
                rows = [
                    Tr(
                        Td(site.name),
                        Td(site.slug),
                        Td(site.tenant_id),
                        Td(
                            Form(
                                csrf(session),
                                Input(type="hidden", name="version", value=site.version),
                                Button("Restore", type="submit", cls="e-button"),
                                method="post",
                                action=f"/admin/platform/deleted-stores/{site.id}/restore",
                            )
                        ),
                    )
                    for site in sites
                ]
                return shell(
                    "Deleted stores",
                    A("← Sites", href="/admin/sites"),
                    Div(
                        Table(
                            Thead(Tr(Th("Store"), Th("Slug"), Th("Tenant"), Th("Action"))),
                            Tbody(*rows),
                        ),
                        cls="e-card",
                    )
                    if rows
                    else Div(P("There are no deleted stores."), cls="e-card"),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/platform/deleted-stores/{site_id}/restore", methods=["POST"])
    async def restore(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                user = db.get(User, actor(session))
                from app.config import settings

                if not user or user.email.lower() != settings.admin_email:
                    raise CommerceError("Platform operator access is required.")
                site = db.scalar(
                    select(Site)
                    .where(Site.id == site_id, Site.status == "deleted")
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if not site:
                    raise CommerceError("Deleted store not found.")
                if str(form.get("version", "")) != str(site.version):
                    raise CommerceError("Store changed. Reload before restoring it.")
                site.status = "draft"
                site.hostname = None
                site.version += 1
                commerce = db.scalar(
                    select(SiteCommerceSettings).where(
                        SiteCommerceSettings.site_id == site.id,
                        SiteCommerceSettings.tenant_id == site.tenant_id,
                    )
                )
                if commerce:
                    commerce.mode = "disabled"
                    commerce.version += 1
                db.add(
                    AdminActivity(
                        tenant_id=site.tenant_id,
                        site_id=site.id,
                        actor_id=user.id,
                        object_type="site",
                        object_id=site.id,
                        action="restored",
                        summary="Store restored as a draft without a hostname.",
                    )
                )
                db.commit()
            return RedirectResponse("/admin/platform/deleted-stores", status_code=303)
        except CommerceError as exc:
            return error(exc)
