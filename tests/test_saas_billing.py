"""Phase 5e FastShop platform subscription billing coverage."""

import hashlib
import hmac
import json
import re
import time
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from fasthtml.common import fast_app
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from app import billing, content, plans, site_routes
from app.billing_webhooks import process_verified_event
from app.integrations.commerce_email import render_message
from app.models import (
    Base,
    BillingSubscription,
    CommerceMail,
    PlatformBillingStripeEvent,
    Site,
    Tenant,
    User,
)


def configured_settings(**overrides):
    values = {
        "billing_stripe_secret_key": "sk_test_platform12345678",
        "billing_stripe_webhook_secret": "whsec_platform12345678",
        "billing_price_basic": "price_basic_test",
        "billing_price_pro": "price_pro_test",
        "billing_live_accepted": False,
        "is_production": False,
        "public_url": "http://testserver",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


@pytest.fixture
def workspace(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(site_routes, "SessionLocal", sessions)
    monkeypatch.setattr("app.plan_routes.SessionLocal", sessions)
    monkeypatch.setattr("app.billing_routes.SessionLocal", sessions)
    app, rt = fast_app(
        live=False,
        pico=False,
        secret_key="billing-test-cookie-secret",
        sess_https_only=False,
        same_site="lax",
        canonical=False,
    )
    site_routes.register_site_routes(rt)

    @rt("/_sign-in", methods=["POST"])
    async def sign_in_route(session, request):
        form = await request.form()
        session["user_id"] = str(form.get("user_id", ""))
        return JSONResponse({"ok": True})

    yield SimpleNamespace(client=TestClient(app), sessions=sessions)
    engine.dispose()


def seeded_account(workspace, *, email="billing@example.test"):
    with workspace.sessions() as db:
        user = User(email=email, name="Billing merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Billing workspace", "billing-workspace")
        db.commit()
        return user.id, site.id, site.tenant_id


def sign_in(workspace, user_id):
    workspace.client.post("/_sign-in", data={"user_id": user_id})


def billing_row(workspace, user_id, tenant_id, *, plan_id="basic", status="checkout_pending"):
    with workspace.sessions() as db:
        row = BillingSubscription(
            tenant_id=tenant_id,
            user_id=user_id,
            plan_id=plan_id,
            status=status,
        )
        db.add(row)
        db.flush()
        db.commit()
        return row.id


def event(event_id, event_type, obj, *, livemode=False):
    return {
        "id": event_id,
        "type": event_type,
        "livemode": livemode,
        "data": {"object": obj},
    }


def subscription_object(row_id, tenant_id, *, plan_id="basic", status="active"):
    return {
        "object": "subscription",
        "id": "sub_platform_123",
        "customer": "cus_platform_123",
        "status": status,
        "cancel_at_period_end": False,
        "current_period_end": 1_800_000_000,
        "metadata": {
            "billing_subscription_id": row_id,
            "tenant_id": tenant_id,
            "plan_id": plan_id,
        },
        "items": {
            "data": [{"price": {"id": f"price_{plan_id}_test"}}],
        },
    }


def invoice_object(row_id, tenant_id):
    return {
        "object": "invoice",
        "id": "in_platform_123",
        "subscription": "sub_platform_123",
        "customer": "cus_platform_123",
        "metadata": {
            "billing_subscription_id": row_id,
            "tenant_id": tenant_id,
        },
    }


def test_unconfigured_billing_panel_and_checkout_refusal(workspace, monkeypatch):
    monkeypatch.setattr(
        billing,
        "settings",
        configured_settings(
            billing_stripe_secret_key="",
            billing_stripe_webhook_secret="",
            billing_price_basic="",
            billing_price_pro="",
        ),
    )
    user_id, _site_id, _tenant_id = seeded_account(workspace)
    sign_in(workspace, user_id)
    page = workspace.client.get("/admin/billing")
    assert page.status_code == 200
    assert "Billing setup is incomplete" in page.text
    assert "Currently unavailable" in page.text and "disabled" in page.text
    response = workspace.client.post(
        "/admin/billing/checkout",
        data={"csrf_token": csrf(page), "plan": "basic"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/billing?notice=")
    with workspace.sessions() as db:
        assert db.scalar(select(BillingSubscription.id)) is None


def test_checkout_session_uses_subscription_mode_and_configured_price():
    seen = {}

    def handler(request):
        seen["authorization"] = request.headers["authorization"]
        seen["idempotency"] = request.headers["idempotency-key"]
        seen["form"] = parse_qs(request.content.decode())
        return httpx.Response(
            200,
            json={
                "id": "cs_test_platform_123",
                "url": "https://checkout.stripe.com/c/pay/test-session",
                "livemode": False,
            },
        )

    row = BillingSubscription(
        id="billingrow123",
        tenant_id="tenant123",
        user_id="user123",
        plan_id="pro",
        status="checkout_pending",
        checkout_command_id="checkoutcommand123",
    )
    gateway = billing.StripeBillingGateway(
        transport=httpx.MockTransport(handler),
        config=billing.BillingConfiguration(
            secret_key="sk_test_platform12345678",
            webhook_secret="whsec_platform12345678",
            prices={"basic": "price_basic_test", "pro": "price_pro_test"},
            mode="test",
        ),
    )
    created = gateway.create_checkout_session(
        row,
        "owner@example.test",
        success_url="https://fastshop.example/admin/billing/success",
        cancel_url="https://fastshop.example/admin/billing/cancel",
    )
    assert created["id"] == "cs_test_platform_123"
    assert seen["authorization"] == "Bearer sk_test_platform12345678"
    assert seen["idempotency"] == "fastshop-platform-billing-checkout-checkoutcommand123"
    assert seen["form"]["mode"] == ["subscription"]
    assert seen["form"]["line_items[0][price]"] == ["price_pro_test"]
    assert seen["form"]["metadata[tenant_id]"] == ["tenant123"]


def test_platform_webhook_signature_verification():
    payload = json.dumps({"id": "evt_signed", "type": "invoice.paid"}).encode()
    timestamp = int(time.time())
    secret = "whsec_platform12345678"
    digest = hmac.new(
        secret.encode(), str(timestamp).encode() + b"." + payload, hashlib.sha256
    ).hexdigest()
    signature = f"t={timestamp},v1={digest}"
    assert billing.verify_webhook(payload, signature, secret)["id"] == "evt_signed"
    with pytest.raises(Exception, match="Invalid platform billing webhook"):
        billing.verify_webhook(payload, f"t={timestamp},v1=wrong", secret)


def test_checkout_completed_reconciles_plan_and_mapping(workspace, monkeypatch):
    monkeypatch.setattr(billing, "settings", configured_settings())
    user_id, _site_id, tenant_id = seeded_account(workspace)
    row_id = billing_row(workspace, user_id, tenant_id)
    obj = {
        "object": "checkout.session",
        "id": "cs_test_platform_123",
        "mode": "subscription",
        "customer": "cus_platform_123",
        "subscription": "sub_platform_123",
        "metadata": {
            "billing_subscription_id": row_id,
            "tenant_id": tenant_id,
            "checkout_command_id": "",
            "plan_id": "basic",
        },
    }
    with workspace.sessions() as db:
        obj["metadata"]["checkout_command_id"] = db.get(
            BillingSubscription, row_id
        ).checkout_command_id
        assert process_verified_event(
            db, event("evt_checkout", "checkout.session.completed", obj)
        ) == "processed"
        db.commit()
        row = db.get(BillingSubscription, row_id)
        assert row.status == "checkout_complete"
        assert row.stripe_customer_id == "cus_platform_123"
        assert row.stripe_subscription_id == "sub_platform_123"
        assert db.get(Tenant, tenant_id).plan == "basic"


def test_subscription_created_and_updated_reconcile_tier_and_downgrade(
    workspace, monkeypatch
):
    monkeypatch.setattr(billing, "settings", configured_settings())
    user_id, _site_id, tenant_id = seeded_account(workspace)
    row_id = billing_row(workspace, user_id, tenant_id)
    with workspace.sessions() as db:
        created = subscription_object(row_id, tenant_id, plan_id="pro")
        process_verified_event(
            db, event("evt_sub_created", "customer.subscription.created", created)
        )
        db.commit()
        assert db.get(Tenant, tenant_id).plan == "pro"
        row = db.get(BillingSubscription, row_id)
        assert row.status == "active" and row.plan_id == "pro"

        ending = subscription_object(row_id, tenant_id, plan_id="pro", status="active")
        ending["cancel_at_period_end"] = True
        process_verified_event(
            db, event("evt_sub_ending", "customer.subscription.updated", ending)
        )
        db.commit()
        assert db.get(Tenant, tenant_id).plan == "pro"
        assert db.get(BillingSubscription, row_id).cancel_at_period_end is True

        updated = subscription_object(row_id, tenant_id, plan_id="pro", status="canceled")
        process_verified_event(
            db, event("evt_sub_updated", "customer.subscription.updated", updated)
        )
        db.commit()
        assert db.get(Tenant, tenant_id).plan == "free"
        assert db.get(BillingSubscription, row_id).status == "canceled"


def test_subscription_deleted_downgrades_to_free(workspace, monkeypatch):
    monkeypatch.setattr(billing, "settings", configured_settings())
    user_id, _site_id, tenant_id = seeded_account(workspace)
    row_id = billing_row(workspace, user_id, tenant_id, plan_id="basic", status="active")
    with workspace.sessions() as db:
        row = db.get(BillingSubscription, row_id)
        row.stripe_customer_id = "cus_platform_123"
        row.stripe_subscription_id = "sub_platform_123"
        db.get(Tenant, tenant_id).plan = "basic"
        db.commit()
        deleted = subscription_object(row_id, tenant_id, status="active")
        process_verified_event(
            db, event("evt_sub_deleted", "customer.subscription.deleted", deleted)
        )
        db.commit()
        assert db.get(Tenant, tenant_id).plan == "free"
        assert db.get(BillingSubscription, row_id).status == "canceled"


def test_invoice_payment_failed_queues_dunning_for_admins(workspace, monkeypatch):
    monkeypatch.setattr(billing, "settings", configured_settings())
    user_id, site_id, tenant_id = seeded_account(workspace)
    row_id = billing_row(workspace, user_id, tenant_id, status="active")
    with workspace.sessions() as db:
        row = db.get(BillingSubscription, row_id)
        row.stripe_customer_id = "cus_platform_123"
        row.stripe_subscription_id = "sub_platform_123"
        db.commit()
        process_verified_event(
            db,
            event(
                "evt_invoice_failed",
                "invoice.payment_failed",
                invoice_object(row_id, tenant_id),
            ),
        )
        db.commit()
        row = db.get(BillingSubscription, row_id)
        assert row.status == "past_due"
        assert row.last_invoice_status == "payment_failed"
        mail = db.scalar(select(CommerceMail).where(
            CommerceMail.kind == "platform_billing_payment_failed"
        ))
        assert mail is not None and mail.user_id == user_id and mail.site_id == site_id
        message = render_message(
            db, mail, db.get(Site, site_id), user=db.get(User, user_id)
        )
        assert message and message[0] == "FastShop subscription payment needs attention"


def test_invoice_paid_restores_active_entitlement(workspace, monkeypatch):
    monkeypatch.setattr(billing, "settings", configured_settings())
    user_id, _site_id, tenant_id = seeded_account(workspace)
    row_id = billing_row(workspace, user_id, tenant_id, status="past_due")
    with workspace.sessions() as db:
        row = db.get(BillingSubscription, row_id)
        row.stripe_customer_id = "cus_platform_123"
        row.stripe_subscription_id = "sub_platform_123"
        db.commit()
        process_verified_event(
            db, event("evt_invoice_paid", "invoice.paid", invoice_object(row_id, tenant_id))
        )
        db.commit()
        assert db.get(BillingSubscription, row_id).status == "active"
        assert db.get(Tenant, tenant_id).plan == "basic"


def test_webhook_replay_is_deduplicated(workspace, monkeypatch):
    monkeypatch.setattr(billing, "settings", configured_settings())
    user_id, _site_id, tenant_id = seeded_account(workspace)
    row_id = billing_row(workspace, user_id, tenant_id, status="active")
    invoice = invoice_object(row_id, tenant_id)
    with workspace.sessions() as db:
        row = db.get(BillingSubscription, row_id)
        row.stripe_customer_id = "cus_platform_123"
        row.stripe_subscription_id = "sub_platform_123"
        db.commit()
        stripe_event = event("evt_duplicate", "invoice.payment_failed", invoice)
        assert process_verified_event(db, stripe_event) == "processed"
        db.commit()
        assert process_verified_event(db, stripe_event) == "processed (duplicate)"
        db.commit()
        assert int(db.scalar(select(func.count(PlatformBillingStripeEvent.id)))) == 1
        assert int(db.scalar(select(func.count(CommerceMail.id)))) == 1


def test_operator_plan_precedence_disables_conflicting_self_serve_reconciliation(
    workspace, monkeypatch
):
    monkeypatch.setattr(billing, "settings", configured_settings())
    user_id, _site_id, tenant_id = seeded_account(workspace)
    row_id = billing_row(workspace, user_id, tenant_id, plan_id="basic", status="active")
    with workspace.sessions() as db:
        plans.set_tenant_plan(db, tenant_id, "pro")
        db.commit()
        row = db.get(BillingSubscription, row_id)
        assert row.operator_disabled_at is not None
        assert db.get(Tenant, tenant_id).plan == "pro"
        process_verified_event(
            db,
            event(
                "evt_after_operator",
                "customer.subscription.updated",
                subscription_object(row_id, tenant_id, plan_id="basic"),
            ),
        )
        db.commit()
        assert db.get(Tenant, tenant_id).plan == "pro"
        with pytest.raises(Exception, match="operator-managed"):
            billing.begin_checkout(db, user_id, "basic")


def test_billing_active_tier_enforces_write_quota_while_reads_stay_open(
    workspace, monkeypatch
):
    monkeypatch.setattr(billing, "settings", configured_settings())
    user_id, _site_id, tenant_id = seeded_account(workspace)
    row_id = billing_row(workspace, user_id, tenant_id)
    with workspace.sessions() as db:
        process_verified_event(
            db,
            event(
                "evt_entitlement",
                "customer.subscription.created",
                subscription_object(row_id, tenant_id, plan_id="basic"),
            ),
        )
        db.commit()
        for index in range(9):
            content.create_site(db, user_id, f"Basic site {index}", f"basic-site-{index}")
        db.commit()
        assert plans.account_plan(db, user_id).id == "basic"
        assert plans.count_sites(db, user_id) == 10
        with pytest.raises(plans.QuotaExceeded, match="Basic plan allows 10 sites"):
            plans.ensure_sites(db, user_id)
        plan, usage = plans.account_usage(db, user_id)
        assert plan.id == "basic"
        assert next(row for row in usage if row.key == "sites").used == 10
