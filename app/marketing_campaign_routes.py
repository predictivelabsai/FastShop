"""Tenant-scoped campaign editor and confirmation routes."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlencode

from fasthtml.common import (
    H2,
    H3,
    A,
    Button,
    Div,
    Form,
    Input,
    Label,
    NotStr,
    P,
    Small,
    Table,
    Tbody,
    Td,
    Textarea,
    Th,
    Thead,
    Tr,
)
from sqlalchemy import func, select
from starlette.responses import RedirectResponse

from app import content, marketing_email
from app.db import SessionLocal
from app.integrations.commerce_email import dispatch_mail
from app.models import MarketingCampaign, MarketingCampaignDelivery, User
from app.services import CommerceError


def register_marketing_campaign_routes(rt, actor, csrf, check_csrf, shell, error):
    def base(site_id):
        return f"/admin/sites/{site_id}/marketing/campaigns"

    def editor(site, campaign, csrf_field, *, notice=""):
        address = marketing_email._store_address(site)
        campaign_id = campaign.id if campaign else ""
        action = base(site.id) + ("/" + campaign_id if campaign_id else "")
        return (
            A("← Campaigns", href=base(site.id)),
            P(notice, role="status") if notice else None,
            P("Add the store's physical address in store settings before sending.", cls="e-note")
            if not address else None,
            Form(
                csrf_field(),
                Label("Campaign name", Input(name="name", maxlength=160, required=True,
                    value=campaign.name if campaign else "")),
                Label("Subject", Input(name="subject", maxlength=200, required=True,
                    value=campaign.subject if campaign else "")),
                Label("Preheader", Input(name="preheader", maxlength=250,
                    value=campaign.preheader if campaign else "")),
                Label("Email body", Textarea(
                    campaign.body_html if campaign else "<p>Write your message here.</p>",
                    name="body_html", rows=14, maxlength=20000, required=True,
                )),
                P(Small("Allowed formatting: paragraphs, headings, lists, emphasis, quotations and safe links.")),
                Button("Save campaign" if campaign and campaign.status == "scheduled" else "Save draft",
                       cls="a-button"),
                method="post", action=action, cls="e-form a-campaign-editor",
            ),
            Div(
                Form(csrf_field(), Button("Preview email", cls="a-button a-button-secondary"),
                    method="get", action=action + "/preview") if campaign else None,
                Form(csrf_field(), Button("Send test to me", cls="a-button a-button-secondary"),
                    method="post", action=action + "/test") if campaign else None,
                A("Review recipients and send", href=action + "/send-confirm", cls="a-button")
                if campaign and campaign.status == "draft" else None,
                cls="a-campaign-actions",
            ) if campaign else None,
        )

    def count_deliveries(db, campaign_ids):
        rows = db.execute(select(
            MarketingCampaignDelivery.campaign_id,
            MarketingCampaignDelivery.status,
            func.count(),
        ).where(MarketingCampaignDelivery.campaign_id.in_(campaign_ids)).group_by(
            MarketingCampaignDelivery.campaign_id, MarketingCampaignDelivery.status,
        ))
        result = {campaign_id: {"queued": 0, "sent": 0, "failed": 0, "cancelled": 0}
                  for campaign_id in campaign_ids}
        for campaign_id, status, count in rows:
            result[campaign_id][status] = count
        return result

    @rt("/admin/sites/{site_id}/marketing/campaigns", methods=["GET"])
    def list_campaigns(session, request, site_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                campaigns = list(db.scalars(select(MarketingCampaign).where(
                    MarketingCampaign.site_id == site.id,
                    MarketingCampaign.tenant_id == site.tenant_id,
                ).order_by(MarketingCampaign.updated_at.desc(), MarketingCampaign.id)))
                counts = count_deliveries(db, [campaign.id for campaign in campaigns])
                rows = []
                for campaign in campaigns:
                    status = counts[campaign.id]
                    when = campaign.sent_at or campaign.scheduled_at
                    rows.append(Tr(
                        Td(A(campaign.name, href=base(site.id) + "/" + campaign.id)),
                        Td(campaign.status.title()),
                        Td(str(campaign.recipient_count)),
                        Td(str(status["sent"])),
                        Td(str(status["failed"])),
                        Td(when.strftime("%d %b %Y %H:%M UTC") if when else ""),
                    ))
                address_warning = P(
                    "Add the store's physical address in store settings before sending campaigns.",
                    cls="e-note", role="status",
                ) if not marketing_email._store_address(site) else None
                page = (
                    A("← Marketing", href=f"/admin/sites/{site.id}/marketing"),
                    P(notice, role="status") if notice else None,
                    address_warning,
                    A("Create campaign", href=base(site.id) + "/new", cls="a-button"),
                    Div(Table(Thead(Tr(
                        Th("Campaign"), Th("Status"), Th("Recipients"), Th("Sent"),
                        Th("Failed"), Th("Scheduled or sent"),
                    )), Tbody(*rows), cls="a-campaign-table") if rows else
                        P("No email campaigns yet."), cls="e-card a-campaign-list"),
                )
                return shell("Email campaigns", *page)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/campaigns/new", methods=["GET"])
    def new_campaign(session, request, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                return shell("New campaign", *editor(site, None, lambda: csrf(session)))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/campaigns", methods=["POST"])
    async def create_campaign(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                campaign = marketing_email.create_campaign(db, site, actor(session), dict(form))
                campaign_id = campaign.id
                db.commit()
            return RedirectResponse(base(site_id) + "/" + campaign_id, status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/campaigns/{campaign_id}", methods=["GET"])
    def edit_campaign(session, request, site_id: str, campaign_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                campaign = marketing_email.owned_campaign(db, site, campaign_id)
                if campaign.status not in {"draft", "scheduled"}:
                    delivery = marketing_email.campaign_counts(db, campaign)
                    page = (
                        A("← Campaigns", href=base(site.id)),
                        H2(campaign.name),
                        P("Status: " + campaign.status.title()),
                        P(f"{campaign.recipient_count} recipients · {delivery['sent']} sent · {delivery['failed']} failed"),
                        P(notice, role="status") if notice else None,
                        A("Preview email", href=base(site.id) + "/" + campaign.id + "/preview", cls="a-button"),
                    )
                else:
                    page = editor(site, campaign, lambda: csrf(session), notice=notice)
                    if campaign.status == "scheduled":
                        page = (P("Scheduled for " + campaign.scheduled_at.strftime("%d %b %Y %H:%M UTC")
                                  if campaign.scheduled_at else "Campaign scheduled.", cls="e-note"), *page)
                return shell("Email campaign", *page)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/campaigns/{campaign_id}", methods=["POST"])
    async def update_campaign(session, request, site_id: str, campaign_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                marketing_email.update_campaign(db, site, campaign_id, dict(form))
                db.commit()
            return RedirectResponse(base(site_id) + "/" + campaign_id, status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/campaigns/{campaign_id}/preview", methods=["GET"])
    def preview_campaign(session, request, site_id: str, campaign_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                campaign = marketing_email.owned_campaign(db, site, campaign_id)
                preview_html = marketing_email._html_email(
                    site, campaign.body_html, preheader=campaign.preheader,
                )
                return shell(
                    "Campaign preview",
                    A("← Edit campaign", href=base(site.id) + "/" + campaign.id),
                    H2(campaign.subject),
                    Div(NotStr(preview_html), cls="e-card a-campaign-preview"),
                    H3("Plain-text email"),
                    Div(campaign.body_text, cls="e-card a-campaign-plain-preview"),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/campaigns/{campaign_id}/test", methods=["POST"])
    async def send_test(session, request, site_id: str, campaign_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                campaign = marketing_email.owned_campaign(db, site, campaign_id)
                user = db.get(User, actor(session))
                mail = marketing_email.send_test(db, site, campaign, user)
                message_id = mail.id
                recipient = user.email
                db.commit()
            dispatch_mail(message_id)
            notice = "Test email queued for " + recipient + "."
            return RedirectResponse(base(site_id) + "/" + campaign_id + "?" + urlencode({"notice": notice}),
                                    status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/campaigns/{campaign_id}/send-confirm", methods=["GET"])
    def confirm_send(session, request, site_id: str, campaign_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                campaign = marketing_email.owned_campaign(db, site, campaign_id)
                if campaign.status != "draft":
                    raise CommerceError("Only a draft campaign can be sent or scheduled.")
                count = marketing_email.subscribed_count(db, site)
                recipient_label = "recipient" if count == 1 else "recipients"
                page = (
                    A("← Edit campaign", href=base(site.id) + "/" + campaign.id),
                    H2(campaign.subject),
                    P(f"This email will be sent to {count} currently subscribed {recipient_label}."),
                    P("Unsubscribed and pending addresses are excluded. Subscription status is checked again before each send."),
                    P("The store's physical address is missing. Add it in store settings before sending.",
                      cls="e-note", role="status") if not marketing_email._store_address(site) else None,
                    Form(
                        csrf(session),
                        Input(type="hidden", name="recipient_count", value=str(count)),
                        Button("Send now", cls="a-button"),
                        method="post", action=base(site.id) + "/" + campaign.id + "/send",
                        cls="e-form a-campaign-confirm",
                    ),
                    Form(
                        csrf(session),
                        Input(type="hidden", name="recipient_count", value=str(count)),
                        Label("Schedule at (UTC)", Input(type="datetime-local", name="scheduled_at", required=True)),
                        Button("Schedule campaign", cls="a-button a-button-secondary"),
                        method="post", action=base(site.id) + "/" + campaign.id + "/schedule",
                        cls="e-form a-campaign-schedule",
                    ),
                )
                return shell("Confirm campaign send", *page)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/campaigns/{campaign_id}/send", methods=["POST"])
    async def send_now(session, request, site_id: str, campaign_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                expected = int(form.get("recipient_count", "-1"))
                if expected != marketing_email.subscribed_count(db, site):
                    raise CommerceError("The subscribed audience changed. Review the recipient count and confirm again.")
                campaign, count = marketing_email.schedule_campaign(db, site, campaign_id)
                db.commit()
            return RedirectResponse(base(site_id) + "/" + campaign_id + "?" + urlencode({
                "notice": f"Campaign sending to {count} recipients.",
            }), status_code=303)
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/campaigns/{campaign_id}/schedule", methods=["POST"])
    async def schedule(session, request, site_id: str, campaign_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            scheduled_at = datetime.fromisoformat(str(form.get("scheduled_at", "")))
            scheduled_at = scheduled_at.replace(tzinfo=UTC) if scheduled_at.tzinfo is None else scheduled_at.astimezone(UTC)
            if scheduled_at <= datetime.now(UTC):
                raise CommerceError("Choose a future schedule time.")
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                expected = int(form.get("recipient_count", "-1"))
                if expected != marketing_email.subscribed_count(db, site):
                    raise CommerceError("The subscribed audience changed. Review the recipient count and confirm again.")
                campaign, count = marketing_email.schedule_campaign(
                    db, site, campaign_id, scheduled_at=scheduled_at,
                )
                db.commit()
            notice = f"Campaign scheduled for {scheduled_at:%d %b %Y %H:%M UTC} for {count} recipients."
            return RedirectResponse(base(site_id) + "/" + campaign_id + "?" + urlencode({"notice": notice}),
                                    status_code=303)
        except (CommerceError, ValueError) as exc:
            return error(exc)
