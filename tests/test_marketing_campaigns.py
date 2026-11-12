"""Marketing campaign editor, audience and outbox behaviour."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fasthtml.common import Input, fast_app
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import (
    content,
    marketing_campaign_routes,
    marketing_email,
    marketing_subscriber_routes,
    site_integrations,
)
from app.admin_ui import AdminContextMiddleware, admin_page, admin_scope
from app.integrations import commerce_email
from app.models import (
    Base,
    CommerceMail,
    MarketingCampaign,
    MarketingCampaignDelivery,
    MarketingSubscriber,
    User,
)
from app.services import CommerceError


@pytest.fixture
def campaign_workspace(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="campaign-owner@example.test", name="Campaign owner")
        other_owner = User(email="campaign-other@example.test", name="Other owner")
        db.add_all([owner, other_owner])
        db.flush()
        site = content.create_site(db, owner.id, "Campaign store", "campaign-" + uuid4().hex[:8])
        other_site = content.create_site(db, other_owner.id, "Other store", "other-campaign-" + uuid4().hex[:8])
        site.settings_json = {**site.settings_json, "address": "10 Example Street, London"}
        site.published_settings_json = {**site.published_settings_json,
                                        "address": "10 Example Street, London"}
        for email, status in (("active@example.test", "subscribed"),
                              ("pending@example.test", "pending"),
                              ("stopped@example.test", "unsubscribed")):
            db.add(MarketingSubscriber(
                tenant_id=site.tenant_id, site_id=site.id, email=email,
                status=status, source="footer", consent_evidence="I agree to receive emails.",
                unsubscribe_token=uuid4().hex,
            ))
        db.commit()

    state = SimpleNamespace(user_id=owner.id)
    sent = []

    def actor(session):
        return state.user_id

    def csrf(session):
        session.setdefault("csrf_token", "campaign-csrf")
        return Input(type="hidden", name="csrf_token", value=session["csrf_token"])

    def check_csrf(session, form):
        if form.get("csrf_token") != session.get("csrf_token"):
            raise CommerceError("Your session expired. Reload and try again.")

    def shell(title, *children):
        scope = admin_scope.get() or {}
        return admin_page(title, *children, site=site, sites=[site], user=owner,
                          path=scope.get("path", ""))

    app, rt = fast_app(secret_key="marketing-campaign-test-only")
    app.add_middleware(AdminContextMiddleware)
    monkeypatch.setattr(marketing_campaign_routes, "SessionLocal", sessions)
    monkeypatch.setattr(marketing_subscriber_routes, "SessionLocal", sessions)

    def fake_sender(recipient, subject, body, *, message_id):
        sent.append((recipient, subject, body, message_id))
        return "sent", "fake-postmark-id"

    monkeypatch.setattr(marketing_campaign_routes, "dispatch_mail", lambda message_id:
                        commerce_email.dispatch_mail(message_id, sessions=sessions,
                                                     sender=fake_sender))
    marketing_campaign_routes.register_marketing_campaign_routes(
        rt, actor, csrf, check_csrf, shell, lambda exc: Response(str(exc), status_code=400),
    )
    marketing_subscriber_routes.register_marketing_subscriber_routes(
        rt, actor, csrf, check_csrf, shell, lambda exc: Response(str(exc), status_code=400),
    )
    with TestClient(app, follow_redirects=False) as client:
        yield SimpleNamespace(client=client, sessions=sessions, site=site, other_site=other_site,
                              owner=owner, other_owner=other_owner, state=state, sent=sent)
    engine.dispose()


def _csrf(client, path):
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match
    return match.group(1)


def _create_draft(work, *, name="Autumn update", subject="News from our shop", body=None):
    path = f"/admin/sites/{work.site.id}/marketing/campaigns"
    token = _csrf(work.client, path + "/new")
    response = work.client.post(path, data={
        "csrf_token": token, "name": name, "subject": subject,
        "preheader": "A short update", "body_html": body or "<p>Hello <strong>there</strong>.</p>",
    })
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


def test_campaign_body_sanitiser_and_text_part():
    cleaned = marketing_email.sanitize_email_html(
        '<p onclick="run()">Hello <script>alert(1)</script><strong>world</strong> '
        '<a href="javascript:alert(1)" style="color:red">link</a>'
        '<a href="https://example.test/news" target="_blank">safe</a></p>'
    )

    assert "onclick" not in cleaned and "<script" not in cleaned and "alert(1)" not in cleaned
    assert "style=" not in cleaned and "javascript:" not in cleaned
    assert 'href="https://example.test/news"' in cleaned
    assert 'target="_blank" rel="noopener noreferrer"' in cleaned
    assert "Hello world link safe" in marketing_email.plain_text_from_html(cleaned)


def test_campaign_save_round_trips_rich_html_and_strips_executable_markup(campaign_workspace):
    work = campaign_workspace
    dirty = '<h2>November update</h2><p style="color:red">Hello <strong>there</strong>.</p>' \
            '<ul><li>New arrivals</li></ul><script>alert(1)</script>'
    campaign_id = _create_draft(work, body=dirty)
    with work.sessions() as db:
        campaign = db.get(MarketingCampaign, campaign_id)
        assert campaign.body_html == '<h2>November update</h2><p>Hello <strong>there</strong>.</p><ul><li>New arrivals</li></ul>'
        assert "New arrivals" in campaign.body_text


def test_campaign_audience_excludes_pending_and_unsubscribed_and_enqueue_is_idempotent(campaign_workspace):
    work = campaign_workspace
    with work.sessions() as db:
        campaign = marketing_email.create_campaign(db, work.site, work.owner.id, {
            "name": "Consent only", "subject": "Hello", "body_html": "<p>Thanks for reading.</p>",
        })
        campaign_id = campaign.id
        campaign, recipients = marketing_email.schedule_campaign(db, work.site, campaign.id)
        assert recipients == 1
        mail_ids = list(db.scalars(select(CommerceMail.id).where(
            CommerceMail.kind == "marketing_campaign",
            CommerceMail.site_id == work.site.id,
        )))
        assert len(mail_ids) == 1
        marketing_email._queue_campaign_batch(db, work.site, campaign)
        db.commit()

    with work.sessions() as db:
        deliveries = list(db.scalars(select(MarketingCampaignDelivery).where(
            MarketingCampaignDelivery.campaign_id == campaign_id,
        )))
        assert len(deliveries) == 1
        assert deliveries[0].message_id == mail_ids[0]
        mail = db.get(CommerceMail, mail_ids[0])
        assert mail.reference_json["recipient_email"] == "active@example.test"


def test_campaign_headers_and_unsubscribe_link_are_rendered_for_current_subscriber(campaign_workspace):
    work = campaign_workspace
    with work.sessions() as db:
        campaign = marketing_email.create_campaign(db, work.site, work.owner.id, {
            "name": "Rendered", "subject": "A subject", "preheader": "The preheader",
            "body_html": "<p>Safe content</p>",
        })
        campaign.status = "sending"
        subscriber = db.scalar(select(MarketingSubscriber).where(
            MarketingSubscriber.email == "active@example.test",
        ))
        message = SimpleNamespace(kind="marketing_campaign", reference_json={
            "campaign_id": campaign.id, "subscriber_id": subscriber.id,
        })

        result = marketing_email.render_campaign_message(db, message, work.site)

    subject, text_body, html_body, headers = result
    assert subject == "A subject"
    assert "Unsubscribe: http://localhost:5025/sites/" in text_body
    assert "10 Example Street, London" in text_body
    assert "10 Example Street, London" in html_body
    assert 'href="http://localhost:5025/sites/' in html_body
    assert headers["List-Unsubscribe"].startswith("<http://localhost:5025/sites/")
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_postmark_outbox_sends_html_and_one_click_headers_with_fake_transport(
        campaign_workspace, monkeypatch):
    work = campaign_workspace
    payloads = []

    def fake_post(url, *, timeout, headers, json):
        payloads.append((url, headers, json))
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"ErrorCode": 0, "MessageID": "fake-message-id"})

    monkeypatch.setattr(site_integrations, "mail_settings", lambda site, db: {
        "token": "fake-token", "sender": "mail@example.test", "sender_name": site.name,
        "reply_to": "",
    })
    monkeypatch.setattr(commerce_email.httpx, "post", fake_post)
    result = commerce_email.send_email(
        "active@example.test", "Update", "Plain text", message_id="fake-id",
        site=work.site, db=None, html_body="<p>HTML</p>", headers={
            "List-Unsubscribe": "<https://example.test/unsubscribe?token=fake>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )

    assert result == ("sent", "fake-message-id")
    assert payloads[0][0] == "https://api.postmarkapp.com/email"
    assert payloads[0][1]["X-Postmark-Server-Token"] == "fake-token"
    assert payloads[0][2]["HtmlBody"] == "<p>HTML</p>"
    assert {header["Name"]: header["Value"] for header in payloads[0][2]["Headers"]} == {
        "List-Unsubscribe": "<https://example.test/unsubscribe?token=fake>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def test_campaign_send_confirmation_and_test_mail_only_goes_to_admin(campaign_workspace):
    work = campaign_workspace
    campaign_id = _create_draft(work)
    path = f"/admin/sites/{work.site.id}/marketing/campaigns/{campaign_id}"
    confirm = work.client.get(path + "/send-confirm")
    assert confirm.status_code == 200
    assert "1 currently subscribed recipient" in confirm.text

    token = _csrf(work.client, path)
    response = work.client.post(path + "/test", data={"csrf_token": token})
    assert response.status_code == 303
    assert len(work.sent) == 1
    assert work.sent[0][0] == work.owner.email
    with work.sessions() as db:
        test_mail = db.scalar(select(CommerceMail).where(CommerceMail.kind == "marketing_campaign_test"))
        assert test_mail.user_id == work.owner.id
        assert test_mail.customer_id is None

    confirm_token = _csrf(work.client, path + "/send-confirm")
    send = work.client.post(path + "/send", data={
        "csrf_token": confirm_token, "recipient_count": "1",
    })
    assert send.status_code == 303
    with work.sessions() as db:
        campaign = db.get(MarketingCampaign, campaign_id)
        assert campaign.status == "sending" and campaign.recipient_count == 1
        assert db.scalar(select(func.count(CommerceMail.id)).where(
            CommerceMail.kind == "marketing_campaign",
        )) == 1


def test_campaign_schedule_and_tenant_isolation(campaign_workspace):
    work = campaign_workspace
    campaign_id = _create_draft(work, name="Scheduled")
    path = f"/admin/sites/{work.site.id}/marketing/campaigns/{campaign_id}"
    token = _csrf(work.client, path + "/send-confirm")
    scheduled = datetime.now(UTC) + timedelta(hours=3)
    form_time = scheduled.replace(tzinfo=None).isoformat(timespec="minutes")
    response = work.client.post(path + "/schedule", data={
        "csrf_token": token, "recipient_count": "1", "scheduled_at": form_time,
    })
    assert response.status_code == 303
    with work.sessions() as db:
        campaign = db.get(MarketingCampaign, campaign_id)
        assert campaign.status == "scheduled"
        assert campaign.recipient_count == 1
        assert db.scalar(select(CommerceMail.id).where(
            CommerceMail.kind == "marketing_campaign")) is None

    result = marketing_email.process_due_campaigns(
        sessions=work.sessions, now=scheduled + timedelta(minutes=1),
    )
    assert result["campaigns"] == 1
    with work.sessions() as db:
        assert db.scalar(select(func.count(CommerceMail.id)).where(
            CommerceMail.kind == "marketing_campaign")) == 1

    work.state.user_id = work.other_owner.id
    denied = work.client.get(path)
    assert denied.status_code == 400


def test_campaign_rechecks_subscription_before_outbox_send(campaign_workspace):
    work = campaign_workspace
    with work.sessions() as db:
        campaign = marketing_email.create_campaign(db, work.site, work.owner.id, {
            "name": "Consent check", "subject": "A subject", "body_html": "<p>Message</p>",
        })
        campaign_id = campaign.id
        marketing_email.schedule_campaign(db, work.site, campaign.id)
        mail = db.scalar(select(CommerceMail).where(CommerceMail.kind == "marketing_campaign"))
        mail_id = mail.id
        subscriber = db.scalar(select(MarketingSubscriber).where(
            MarketingSubscriber.email == "active@example.test",
        ))
        subscriber.status = "unsubscribed"
        db.commit()

    accepted = []

    def fake_sender(recipient, subject, body, *, message_id):
        accepted.append(recipient)
        return "sent", "unexpected"

    commerce_email.dispatch_mail(mail_id, sessions=work.sessions, sender=fake_sender)
    with work.sessions() as db:
        mail = db.get(CommerceMail, mail_id)
        delivery = db.scalar(select(MarketingCampaignDelivery).where(
            MarketingCampaignDelivery.campaign_id == campaign_id,
        ))
        assert not accepted
        assert mail.status == "cancelled"
        assert delivery.status == "cancelled"


def test_campaign_records_terminal_delivery_failures(campaign_workspace):
    work = campaign_workspace
    with work.sessions() as db:
        campaign = marketing_email.create_campaign(db, work.site, work.owner.id, {
            "name": "Failed delivery", "subject": "A subject", "body_html": "<p>Message</p>",
        })
        campaign_id = campaign.id
        marketing_email.schedule_campaign(db, work.site, campaign.id)
        mail = db.scalar(select(CommerceMail).where(CommerceMail.kind == "marketing_campaign"))
        mail.status = "awaiting_configuration"
        mail.attempts = 5
        marketing_email.sync_mail_result(db, mail)
        db.commit()

    with work.sessions() as db:
        campaign = db.get(MarketingCampaign, campaign_id)
        counts = marketing_email.campaign_counts(db, campaign)
        assert campaign.status == "sent"
        assert counts["failed"] == 1


def test_one_click_unsubscribe_post_uses_subscriber_token(campaign_workspace):
    work = campaign_workspace
    with work.sessions() as db:
        subscriber = db.scalar(select(MarketingSubscriber).where(
            MarketingSubscriber.email == "active@example.test",
        ))
        token = subscriber.unsubscribe_token

    response = work.client.post(
        f"/sites/{work.site.slug}/unsubscribe?token={token}",
        data={"List-Unsubscribe": "One-Click"},
    )
    assert response.status_code == 200
    with work.sessions() as db:
        subscriber = db.scalar(select(MarketingSubscriber).where(
            MarketingSubscriber.email == "active@example.test",
        ))
        assert subscriber.status == "unsubscribed"


def test_subscriber_page_has_one_heading_and_readable_utc_timestamp(campaign_workspace):
    work = campaign_workspace
    response = work.client.get(f"/admin/sites/{work.site.id}/marketing/subscribers")

    assert response.status_code == 200
    assert len(re.findall(r"<h1[^>]*>Email subscribers</h1>", response.text)) == 1
    assert re.search(r"\d{2} [A-Z][a-z]{2} \d{4}, \d{2}:\d{2} UTC", response.text)
    with work.sessions() as db:
        site = db.get(type(work.site), work.site.id)
        site.published_settings_json = {**site.published_settings_json, "timezone": "Europe/Tallinn"}
        db.commit()
    local = work.client.get(f"/admin/sites/{work.site.id}/marketing/subscribers")
    assert re.search(r"\d{2} [A-Z][a-z]{2} \d{4}, \d{2}:\d{2} EET \(Europe/Tallinn\)", local.text)


def test_marketing_email_migration_revision_follows_subscribers():
    migration = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261111_0043_marketing_email.py"
    source = migration.read_text(encoding="utf-8")
    revision = re.search(r'^revision = "([^"]+)"', source, re.MULTILINE)
    parent = re.search(r'^down_revision = "([^"]+)"', source, re.MULTILINE)

    assert revision and len(revision.group(1)) <= 32
    assert parent and parent.group(1) == "20261111_0042"
