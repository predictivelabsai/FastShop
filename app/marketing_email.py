"""Safe email content, campaign audience snapshots and campaign outbox work."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urlencode

from sqlalchemy import func, select

from app import checkout_services
from app.customer_services import customer_base
from app.db import SessionLocal
from app.models import (
    CommerceMail,
    MarketingCampaign,
    MarketingCampaignDelivery,
    MarketingSubscriber,
    Site,
)
from app.rich_text import BLOCK_TAGS, DROP_CONTENT_TAGS, sanitise_rich_text
from app.services import CommerceError

CAMPAIGN_BATCH_SIZE = 50


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.dropped: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self.dropped:
            if tag in DROP_CONTENT_TAGS:
                self.dropped.append(tag)
            return
        if tag in DROP_CONTENT_TAGS:
            self.dropped.append(tag)
        elif tag in BLOCK_TAGS or tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.dropped:
            if tag == self.dropped[-1]:
                self.dropped.pop()
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "a":
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.dropped:
            self.parts.append(data)


def sanitize_email_html(value: str) -> str:
    """Keep email's size limit and use the shared rich-text allow-list."""
    raw = str(value or "")
    if len(raw) > 20_000:
        raise CommerceError("Email body must be no longer than 20,000 characters.")
    return sanitise_rich_text(raw)


def plain_text_from_html(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(str(value or ""))
    parser.close()
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def campaign_values(values: dict) -> dict:
    name = str(values.get("name", "")).strip()[:160]
    subject = str(values.get("subject", "")).strip()[:200]
    preheader = str(values.get("preheader", "")).strip()[:250]
    body_html = sanitize_email_html(values.get("body_html", ""))
    body_text = plain_text_from_html(body_html)
    if not name or not subject or not body_text:
        raise CommerceError("Enter a campaign name, subject and email body.")
    if any(char in subject for char in "\r\n"):
        raise CommerceError("Subject must fit on one line.")
    return {"name": name, "subject": subject, "preheader": preheader,
            "body_html": body_html, "body_text": body_text}


def owned_campaign(db, site, campaign_id, *, lock=False):
    query = select(MarketingCampaign).where(
        MarketingCampaign.id == campaign_id,
        MarketingCampaign.site_id == site.id,
        MarketingCampaign.tenant_id == site.tenant_id,
    )
    if lock:
        query = query.with_for_update()
    campaign = db.scalar(query)
    if not campaign:
        raise CommerceError("Campaign not found.")
    return campaign


def create_campaign(db, site, actor_id, values):
    cleaned = campaign_values(values)
    campaign = MarketingCampaign(tenant_id=site.tenant_id, site_id=site.id,
        creator_id=actor_id, **cleaned)
    db.add(campaign)
    db.flush()
    return campaign


def update_campaign(db, site, campaign_id, values):
    campaign = owned_campaign(db, site, campaign_id, lock=True)
    if campaign.status not in {"draft", "scheduled"}:
        raise CommerceError("Only draft or scheduled campaigns can be edited.")
    for key, value in campaign_values(values).items():
        setattr(campaign, key, value)
    db.flush()
    return campaign


def subscribed_count(db, site) -> int:
    return int(db.scalar(select(func.count(MarketingSubscriber.id)).where(
        MarketingSubscriber.tenant_id == site.tenant_id,
        MarketingSubscriber.site_id == site.id,
        MarketingSubscriber.status == "subscribed",
    )) or 0)


def campaign_counts(db, campaign) -> dict[str, int]:
    values = db.execute(select(MarketingCampaignDelivery.status, func.count()).where(
        MarketingCampaignDelivery.campaign_id == campaign.id,
        MarketingCampaignDelivery.site_id == campaign.site_id,
        MarketingCampaignDelivery.tenant_id == campaign.tenant_id,
    ).group_by(MarketingCampaignDelivery.status))
    result = {key: 0 for key in ("queued", "sent", "failed", "cancelled")}
    result.update({status: count for status, count in values})
    return result


def _snapshot_audience(db, site, campaign):
    subscribers = db.scalars(select(MarketingSubscriber).where(
        MarketingSubscriber.tenant_id == site.tenant_id,
        MarketingSubscriber.site_id == site.id,
        MarketingSubscriber.status == "subscribed",
    ).order_by(MarketingSubscriber.id)).all()
    for subscriber in subscribers:
        db.add(MarketingCampaignDelivery(
            tenant_id=site.tenant_id, site_id=site.id,
            campaign_id=campaign.id, subscriber_id=subscriber.id,
        ))
    db.flush()
    campaign.recipient_count = len(subscribers)
    return len(subscribers)


def _queue_campaign_batch(db, site, campaign, *, limit=CAMPAIGN_BATCH_SIZE):
    deliveries = db.scalars(select(MarketingCampaignDelivery).where(
        MarketingCampaignDelivery.tenant_id == site.tenant_id,
        MarketingCampaignDelivery.site_id == site.id,
        MarketingCampaignDelivery.campaign_id == campaign.id,
        MarketingCampaignDelivery.status == "queued",
        MarketingCampaignDelivery.message_id.is_(None),
    ).order_by(MarketingCampaignDelivery.id).limit(limit).with_for_update()).all()
    queued = 0
    for delivery in deliveries:
        subscriber = db.scalar(select(MarketingSubscriber).where(
            MarketingSubscriber.id == delivery.subscriber_id,
            MarketingSubscriber.site_id == site.id,
            MarketingSubscriber.tenant_id == site.tenant_id,
        ))
        if not subscriber or subscriber.status != "subscribed":
            delivery.status = "cancelled"
            continue
        mail = CommerceMail(
            tenant_id=site.tenant_id, site_id=site.id, customer_id=None, user_id=None,
            kind="marketing_campaign",
            dedupe_key=f"marketing-campaign:{campaign.id}:{subscriber.id}",
            available_at=datetime.now(UTC),
            reference_json={"campaign_id": campaign.id, "subscriber_id": subscriber.id,
                            "recipient_email": subscriber.email},
        )
        db.add(mail)
        db.flush()
        delivery.message_id = mail.id
        queued += 1
    db.flush()
    return queued


def schedule_campaign(db, site, campaign_id, *, scheduled_at=None, now=None):
    """Lock an audience snapshot and schedule or immediately begin a campaign."""
    checkout_services.lock_site(db, site)
    campaign = owned_campaign(db, site, campaign_id, lock=True)
    if campaign.status != "draft":
        raise CommerceError("This campaign has already been scheduled or sent.")
    recipients = _snapshot_audience(db, site, campaign)
    if recipients < 1:
        raise CommerceError("Add at least one subscribed email address before sending.")
    now = now or datetime.now(UTC)
    if scheduled_at and scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=UTC)
    if scheduled_at and scheduled_at > now:
        campaign.status = "scheduled"
        campaign.scheduled_at = scheduled_at
    else:
        campaign.status = "sending"
        campaign.scheduled_at = None
        _queue_campaign_batch(db, site, campaign)
    db.flush()
    return campaign, recipients


def _marketing_delivery_for_mail(db, mail):
    reference = mail.reference_json or {}
    if mail.kind == "marketing_campaign":
        return db.scalar(select(MarketingCampaignDelivery).where(
            MarketingCampaignDelivery.campaign_id == reference.get("campaign_id"),
            MarketingCampaignDelivery.subscriber_id == reference.get("subscriber_id"),
            MarketingCampaignDelivery.site_id == mail.site_id,
            MarketingCampaignDelivery.tenant_id == mail.tenant_id,
        ))
    return None


def sync_mail_result(db, mail) -> None:
    """Mirror terminal outbox state onto marketing delivery counts."""
    if mail.kind == "marketing_abandoned":
        from app.marketing_automation import sync_mail_result as sync_automation_result

        sync_automation_result(db, mail)
        return
    delivery = _marketing_delivery_for_mail(db, mail)
    if not delivery:
        return
    if mail.status == "sent":
        delivery.status = "sent"
        delivery.sent_at = datetime.now(UTC)
    elif mail.status == "cancelled":
        delivery.status = "cancelled"
    elif mail.status in {"failed", "awaiting_configuration"} and mail.attempts >= 5:
        delivery.status = "failed"
    campaign = db.scalar(select(MarketingCampaign).where(
        MarketingCampaign.id == delivery.campaign_id,
        MarketingCampaign.site_id == mail.site_id,
        MarketingCampaign.tenant_id == mail.tenant_id,
    ))
    if campaign and campaign.status == "sending":
        counts = campaign_counts(db, campaign)
        if counts["queued"] == 0 and sum(counts.values()) >= campaign.recipient_count:
            campaign.status = "sent"
            campaign.sent_at = datetime.now(UTC)


def _store_address(site) -> str:
    for values in (site.published_settings_json or {}, site.settings_json or {}):
        address = str(values.get("address") or values.get("company_address") or "").strip()
        if address:
            return address
    return ""


def _unsubscribe_url(site, subscriber) -> str:
    return customer_base(site) + "/unsubscribe?" + urlencode({"token": subscriber.unsubscribe_token})


def _html_email(site, body_html, *, preheader="", extra_html="", unsubscribe_url=""):
    preheader_html = ("<div style=\"display:none;max-height:0;overflow:hidden\">" +
                      html.escape(preheader) + "</div>") if preheader else ""
    footer = ""
    address = _store_address(site)
    if address:
        footer += "<p>" + html.escape(address) + "</p>"
    if unsubscribe_url:
        footer += '<p><a href="' + html.escape(unsubscribe_url, quote=True) + '">Unsubscribe</a></p>'
    return ("<!doctype html><html><body>" + preheader_html + body_html + extra_html +
            ("<footer>" + footer + "</footer>" if footer else "") + "</body></html>")


def _text_email(site, body_text, *, preheader="", extra_text="", unsubscribe_url=""):
    parts = []
    if preheader:
        parts.append(preheader)
    parts.append(body_text)
    if extra_text:
        parts.append(extra_text)
    address = _store_address(site)
    if address:
        parts.append(address)
    if unsubscribe_url:
        parts.append("Unsubscribe: " + unsubscribe_url)
    return "\n\n".join(parts)


def render_campaign_message(db, row, site, user=None):
    reference = row.reference_json or {}
    campaign = db.scalar(select(MarketingCampaign).where(
        MarketingCampaign.id == reference.get("campaign_id"),
        MarketingCampaign.site_id == site.id,
        MarketingCampaign.tenant_id == site.tenant_id,
    ))
    if not campaign:
        return None
    if row.kind == "marketing_campaign_test":
        if not user:
            return None
        html_body = _html_email(site, campaign.body_html, preheader=campaign.preheader)
        text_body = _text_email(site, campaign.body_text, preheader=campaign.preheader)
        return "[Test] " + campaign.subject, text_body, html_body, {}
    if campaign.status != "sending":
        return None
    subscriber = db.scalar(select(MarketingSubscriber).where(
        MarketingSubscriber.id == reference.get("subscriber_id"),
        MarketingSubscriber.site_id == site.id,
        MarketingSubscriber.tenant_id == site.tenant_id,
        MarketingSubscriber.status == "subscribed",
    ))
    if not subscriber:
        return None
    url = _unsubscribe_url(site, subscriber)
    html_body = _html_email(site, campaign.body_html, preheader=campaign.preheader,
                            unsubscribe_url=url)
    text_body = _text_email(site, campaign.body_text, preheader=campaign.preheader,
                            unsubscribe_url=url)
    headers = {
        "List-Unsubscribe": "<" + url + ">",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    return campaign.subject, text_body, html_body, headers


def send_test(db, site, campaign, admin_user):
    if not admin_user.email:
        raise CommerceError("Your account does not have an email address for the test.")
    mail = CommerceMail(
        tenant_id=site.tenant_id, site_id=site.id, customer_id=None, user_id=admin_user.id,
        kind="marketing_campaign_test",
        dedupe_key=f"marketing-campaign-test:{campaign.id}:{datetime.now(UTC).timestamp()}",
        available_at=datetime.now(UTC),
        reference_json={"campaign_id": campaign.id, "recipient_email": admin_user.email},
    )
    db.add(mail)
    db.flush()
    return mail


def process_due_campaigns(*, sessions=SessionLocal, now=None, limit=50, batch_size=CAMPAIGN_BATCH_SIZE):
    """Queue scheduled and sending campaign batches. The unique ledger is the replay guard."""
    now = now or datetime.now(UTC)
    with sessions() as db:
        ids = list(db.scalars(select(MarketingCampaign.id).where(
            MarketingCampaign.status == "sending",
        ).order_by(MarketingCampaign.created_at, MarketingCampaign.id).limit(limit)))
        due_ids = list(db.scalars(select(MarketingCampaign.id).where(
            MarketingCampaign.status == "scheduled",
            MarketingCampaign.scheduled_at <= now,
        ).order_by(MarketingCampaign.scheduled_at, MarketingCampaign.id).limit(limit)))
    processed = 0
    for campaign_id in dict.fromkeys([*due_ids, *ids]):
        with sessions() as db:
            campaign = db.get(MarketingCampaign, campaign_id)
            if not campaign:
                continue
            site = db.scalar(select(Site).where(
                Site.id == campaign.site_id, Site.tenant_id == campaign.tenant_id,
            ))
            if not site:
                continue
            checkout_services.lock_site(db, site)
            campaign = owned_campaign(db, site, campaign_id, lock=True)
            scheduled_at = campaign.scheduled_at
            scheduled_at = (scheduled_at.replace(tzinfo=UTC)
                            if scheduled_at and scheduled_at.tzinfo is None else scheduled_at)
            if campaign.status == "scheduled" and scheduled_at and scheduled_at <= now:
                campaign.status = "sending"
            if campaign.status == "sending":
                _queue_campaign_batch(db, site, campaign, limit=batch_size)
                counts = campaign_counts(db, campaign)
                if counts["queued"] == 0 and sum(counts.values()) >= campaign.recipient_count:
                    campaign.status = "sent"
                    campaign.sent_at = now
            db.commit()
            processed += 1
    return {"campaigns": processed}
