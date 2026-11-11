"""Abandoned checkout automation consent, scheduling and recovery behaviour."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fasthtml.common import Input, fast_app
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import content, marketing_automation, marketing_automation_routes
from app.admin_ui import AdminContextMiddleware, admin_page, admin_scope
from app.models import (
    Base,
    CheckoutAttempt,
    CommerceMail,
    CommerceQuote,
    MarketingAbandonedEmail,
    MarketingSubscriber,
    Order,
    ShopCustomer,
    SiteCart,
    SiteOrder,
    User,
)
from app.services import CommerceError


@pytest.fixture
def automation_workspace(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="automation-owner@example.test", name="Automation owner")
        other_owner = User(email="automation-other@example.test", name="Other owner")
        db.add_all([owner, other_owner])
        db.flush()
        site = content.create_site(db, owner.id, "Automation store", "automation-" + uuid4().hex[:8])
        other_site = content.create_site(db, other_owner.id, "Other store", "other-auto-" + uuid4().hex[:8])
        site.settings_json = {**site.settings_json, "address": "22 Market Road, York"}
        site.published_settings_json = {**site.published_settings_json,
                                        "address": "22 Market Road, York"}
        db.commit()

    state = SimpleNamespace(user_id=owner.id)

    def actor(session):
        return state.user_id

    def csrf(session):
        session.setdefault("csrf_token", "automation-csrf")
        return Input(type="hidden", name="csrf_token", value=session["csrf_token"])

    def check_csrf(session, form):
        if form.get("csrf_token") != session.get("csrf_token"):
            raise CommerceError("Your session expired. Reload and try again.")

    def shell(title, *children):
        scope = admin_scope.get() or {}
        return admin_page(title, *children, site=site, sites=[site], user=owner,
                          path=scope.get("path", ""))

    app, rt = fast_app(secret_key="marketing-automation-test-only")
    app.add_middleware(AdminContextMiddleware)
    monkeypatch.setattr(marketing_automation_routes, "SessionLocal", sessions)
    marketing_automation_routes.register_marketing_automation_routes(
        rt, actor, csrf, check_csrf, shell, lambda exc: Response(str(exc), status_code=400),
    )
    with TestClient(app, follow_redirects=False) as client:
        yield SimpleNamespace(client=client, sessions=sessions, engine=engine, site=site,
                              other_site=other_site, owner=owner, other_owner=other_owner,
                              state=state)
    engine.dispose()


def add_customer(db, site, email, *, consent=False, source="checkout", status="subscribed"):
    customer = ShopCustomer(
        tenant_id=site.tenant_id, site_id=site.id, email=email,
        email_marketing_consent=consent,
    )
    db.add(customer)
    db.flush()
    subscriber = None
    if status:
        subscriber = MarketingSubscriber(
            tenant_id=site.tenant_id, site_id=site.id, email=email,
            status=status, source=source, consent_evidence="I agreed to receive emails.",
            unsubscribe_token=uuid4().hex,
        )
        db.add(subscriber)
        db.flush()
    return customer, subscriber


def add_attempt(db, site, customer, *, created_at=None):
    created_at = created_at or datetime.now(UTC) - timedelta(hours=5)
    quote = CommerceQuote(
        tenant_id=site.tenant_id, site_id=site.id, fingerprint=uuid4().hex,
        provider_id="stripe", currency="USD", subtotal_minor=1000, discount_minor=0,
        shipping_minor=0, tax_minor=0, total_minor=1000,
        expires_at=created_at + timedelta(minutes=35),
        snapshot_json={"lines": [{"variant_id": "variant-test", "quantity": 1,
                                  "subscription": False}], "destination": {}},
    )
    db.add(quote)
    db.flush()
    attempt = CheckoutAttempt(
        tenant_id=site.tenant_id, site_id=site.id, customer_id=customer.id,
        quote_id=quote.id, request_key=uuid4().hex[:24], request_hash=uuid4().hex,
        state="open", expires_at=created_at + timedelta(minutes=35), created_at=created_at,
    )
    db.add(attempt)
    db.flush()
    return attempt, quote


def enable_automation(db, site, *, delay=4, include_link=True):
    return marketing_automation.save_automation(db, site, {
        "enabled": "on", "delay_hours": str(delay), "subject": "Your basket is waiting",
        "body_html": "<p>Return when you are ready.</p>",
        "include_recovery_link": "on" if include_link else "",
    })


def test_automation_is_off_by_default_and_page_shows_default_delay(automation_workspace):
    work = automation_workspace
    response = work.client.get(f"/admin/sites/{work.site.id}/marketing/automations")

    assert response.status_code == 200
    assert "Automation is off" in response.text
    assert 'name="delay_hours"' in response.text and 'value="4"' in response.text
    assert 'name="include_recovery_link"' in response.text
    with work.sessions() as db:
        assert marketing_automation.automation_for(db, work.site) is None
        customer, _ = add_customer(db, work.site, "automation-off@example.test", source="checkout")
        add_attempt(db, work.site, customer)
        db.commit()
    result = marketing_automation.queue_due_abandoned(sessions=work.sessions)
    assert result["queued"] == 0


def test_scheduler_checks_consent_completed_orders_and_queues_once(automation_workspace):
    work = automation_workspace
    now = datetime.now(UTC)
    with work.sessions() as db:
        enable_automation(db, work.site)
        eligible, _ = add_customer(db, work.site, "checkout-consent@example.test", source="checkout")
        pending, _ = add_customer(db, work.site, "pending-consent@example.test", status="pending")
        no_consent, _ = add_customer(db, work.site, "no-consent@example.test", status=None)
        completed, _ = add_customer(db, work.site, "completed@example.test", source="account")
        eligible_attempt, _ = add_attempt(db, work.site, eligible, created_at=now - timedelta(hours=5))
        add_attempt(db, work.site, pending, created_at=now - timedelta(hours=5))
        add_attempt(db, work.site, no_consent, created_at=now - timedelta(hours=5))
        completed_attempt, _ = add_attempt(db, work.site, completed, created_at=now - timedelta(hours=5))
        order = Order(
            tenant_id=work.site.tenant_id, channel_id=work.site.channel_id,
            number="RECOVERED-1", idempotency_key="completed-order-1",
            email=completed.email, currency="USD", subtotal_minor=1000, total_minor=1000,
            payment_status="paid", created_at=now,
        )
        db.add(order)
        db.flush()
        db.add(SiteOrder(
            tenant_id=work.site.tenant_id, site_id=work.site.id,
            order_id=order.id, customer_id=completed.id,
        ))
        db.commit()

    first = marketing_automation.queue_due_abandoned(sessions=work.sessions, now=now)
    second = marketing_automation.queue_due_abandoned(sessions=work.sessions, now=now)

    assert first == {"scanned": 4, "queued": 1}
    assert second["queued"] == 0
    with work.sessions() as db:
        records = list(db.scalars(select(MarketingAbandonedEmail)))
        mails = list(db.scalars(select(CommerceMail).where(CommerceMail.kind == "marketing_abandoned")))
        assert len(records) == len(mails) == 1
        assert records[0].attempt_id == eligible_attempt.id
        assert records[0].attempt_id != completed_attempt.id
        assert mails[0].customer_id == eligible.id


def test_recovery_email_has_one_click_headers_and_signed_cart_link(automation_workspace):
    work = automation_workspace
    now = datetime.now(UTC)
    with work.sessions() as db:
        automation = enable_automation(db, work.site)
        customer, subscriber = add_customer(db, work.site, "recover@example.test", source="checkout")
        attempt, _ = add_attempt(db, work.site, customer, created_at=now - timedelta(hours=5))
        token, expiry = marketing_automation.create_recovery_token(work.site, attempt.id)
        record = MarketingAbandonedEmail(
            tenant_id=work.site.tenant_id, site_id=work.site.id, automation_id=automation.id,
            attempt_id=attempt.id, subscriber_id=subscriber.id,
            status="queued", recovery_expires_at=expiry,
        )
        db.add(record)
        db.flush()
        row = CommerceMail(
            tenant_id=work.site.tenant_id, site_id=work.site.id, customer_id=customer.id,
            kind="marketing_abandoned", dedupe_key="test-abandoned-mail",
            available_at=now, reference_json={
                "abandoned_email_id": record.id, "attempt_id": attempt.id,
                "subscriber_id": subscriber.id, "recovery_token": token,
            },
        )
        result = marketing_automation.render_abandoned_message(db, row, work.site, customer)

    subject, text_body, html_body, headers = result
    assert subject == "Your basket is waiting"
    assert "Return to your basket: http://localhost:5025/sites/" in text_body
    assert "22 Market Road, York" in text_body and "22 Market Road, York" in html_body
    assert "List-Unsubscribe" in headers and headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert 'href="http://localhost:5025/sites/' in html_body


def test_recovery_token_expiry_tampering_and_cart_restore(automation_workspace):
    work = automation_workspace
    now = datetime.now(UTC)
    session = {}
    with work.sessions() as db:
        automation = enable_automation(db, work.site)
        customer, subscriber = add_customer(db, work.site, "restore@example.test", source="checkout")
        attempt, quote = add_attempt(db, work.site, customer, created_at=now - timedelta(hours=5))
        token, expiry = marketing_automation.create_recovery_token(work.site, attempt.id)
        record = MarketingAbandonedEmail(
            tenant_id=work.site.tenant_id, site_id=work.site.id, automation_id=automation.id,
            attempt_id=attempt.id, subscriber_id=subscriber.id,
            status="sent", recovery_expires_at=expiry,
        )
        db.add(record)
        db.flush()
        cart = SiteCart(
            tenant_id=work.site.tenant_id, site_id=work.site.id,
            lines_json=[{"variant_id": "variant-test", "quantity": 1, "subscription": False}],
            checkout_id=attempt.id,
        )
        db.add(cart)
        db.flush()
        valid = marketing_automation.verify_recovery_token(work.site, token, now=now)
        assert valid and valid[0] == attempt.id
        assert marketing_automation.verify_recovery_token(
            work.site, token, now=expiry + timedelta(seconds=1),
        ) is None
        tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
        assert marketing_automation.verify_recovery_token(work.site, tampered, now=now) is None
        marketing_automation.restore_cart(db, work.site, token, session)
        assert session["cart:" + work.site.id] == cart.id
        assert cart.recovery_email_id == record.id
        db.commit()


def test_recovered_order_attribution_is_recorded_once(automation_workspace):
    work = automation_workspace
    with work.sessions() as db:
        automation = enable_automation(db, work.site)
        customer, subscriber = add_customer(db, work.site, "attributed@example.test")
        attempt, _ = add_attempt(db, work.site, customer)
        record = MarketingAbandonedEmail(
            tenant_id=work.site.tenant_id, site_id=work.site.id, automation_id=automation.id,
            attempt_id=attempt.id, subscriber_id=subscriber.id, status="sent",
            recovery_expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db.add(record)
        db.flush()
        cart = SiteCart(
            tenant_id=work.site.tenant_id, site_id=work.site.id,
            lines_json=[], checkout_id=attempt.id, recovery_email_id=record.id,
        )
        db.add(cart)
        order = Order(
            tenant_id=work.site.tenant_id, channel_id=work.site.channel_id,
            number="RECOVERED-2", idempotency_key="recovered-order-attribution",
            email=customer.email, currency="USD", subtotal_minor=1000, total_minor=1000,
            payment_status="paid",
        )
        db.add(order)
        db.flush()

        assert marketing_automation.record_recovered_order(db, work.site, attempt.id, order.id)
        assert not marketing_automation.record_recovered_order(db, work.site, attempt.id, order.id)
        assert marketing_automation.recovered_order_count(db, work.site) == 1


def test_automation_admin_tenant_isolation_and_save(automation_workspace):
    work = automation_workspace
    path = f"/admin/sites/{work.site.id}/marketing/automations"
    page = work.client.get(path)
    token_match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token_match
    saved = work.client.post(path, data={
        "csrf_token": token_match.group(1), "enabled": "on", "delay_hours": "8",
        "subject": "Basket update", "body_html": "<p>Still interested?</p>",
        "include_recovery_link": "on",
    })
    assert saved.status_code == 303
    with work.sessions() as db:
        automation = marketing_automation.automation_for(db, work.site)
        assert automation.enabled and automation.delay_hours == 8
        assert "Still interested?" in automation.body_text

    work.state.user_id = work.other_owner.id
    assert work.client.get(path).status_code == 400


def test_concurrent_scheduler_ticks_create_one_abandoned_email(tmp_path):
    db_path = tmp_path / "marketing-concurrency.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 10})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="concurrent-owner@example.test", name="Owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Concurrent store", "concurrent-" + uuid4().hex[:8])
        enable_automation(db, site)
        customer, _ = add_customer(db, site, "concurrent@example.test", source="checkout")
        attempt, _ = add_attempt(db, site, customer)
        site_id = site.id
        now = datetime.now(UTC)
        db.commit()

    def tick(_):
        return marketing_automation.queue_due_abandoned(sessions=sessions, now=now)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(tick, range(2)))

    assert sum(result["queued"] for result in results) == 1
    with sessions() as db:
        assert db.scalar(select(func.count(MarketingAbandonedEmail.id)).where(
            MarketingAbandonedEmail.site_id == site_id,
        )) == 1
        assert db.scalar(select(func.count(CommerceMail.id)).where(
            CommerceMail.kind == "marketing_abandoned",
        )) == 1
    engine.dispose()
