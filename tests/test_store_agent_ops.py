"""Store agent reads remain scoped and writes wait for merchant confirmation."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import catalog_admin, content
from app.models import (
    AgentAction,
    Base,
    CommerceMail,
    Discount,
    InventoryAdjustment,
    Product,
    SiteCommerceSettings,
    Stock,
    User,
    VariantChannelListing,
    Warehouse,
)
from app.services import CommerceError
from app.store_agent import run_turn
from app.store_agent_ops import confirm_action, decline_action, register_routes


class FakeTransport:
    def __init__(self, *replies):
        self.replies = iter(replies)

    def complete(self, messages, tools):
        return next(self.replies)


def call(name, args):
    return {"tool_calls": [{"id": "store-call", "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}]}


def test_store_action_is_pending_until_confirmed_and_executes_once():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="ops@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Ops", "ops-test")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        page = content.site_pages(db, site)[0]
        events = list(run_turn(db, site, user, "Rename it", page_id=page.id, transport=FakeTransport(
            call("update_product", {"product_id": product.id, "name": "Raspberry crate"}),
            {"content": "I proposed a product update for confirmation."})))
        action = db.scalar(select(AgentAction))
        assert action.status == "pending"
        assert product.name == "Raspberry box"
        assert any(event["type"] == "action_pending" and event["id"] == action.id for event in events)
        result = confirm_action(db, site, user, action.id)
        assert result.status == "confirmed"
        assert product.name == "Raspberry crate"
        confirm_action(db, site, user, action.id)
        assert product.name == "Raspberry crate"
    engine.dispose()


def test_store_agent_product_description_is_sanitised_on_confirm():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="rich-agent@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Rich agent", "rich-agent")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        dirty = '<h3>Ingredients</h3><p onclick="x()">Carefully chosen.</p><script>alert(1)</script>' \
                '<ul><li>Raspberry</li></ul>'
        action = _propose_tool(db, site, user, "update_product", {
            "product_id": product.id, "description": dirty,
        })
        assert action.status == "pending" and not product.description_html
        confirm_action(db, site, user, action.id)
        assert product.description_html == '<h3>Ingredients</h3><p>Carefully chosen.</p><ul><li>Raspberry</li></ul>'
        assert "alert(1)" not in product.description_html
    engine.dispose()


def test_store_product_reads_reject_cross_tenant_ids():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = User(email="owner@example.test", name="Owner")
        other = User(email="other@example.test", name="Other")
        db.add_all([owner, other])
        db.flush()
        site = content.create_site(db, owner.id, "Owner", "owner-test")
        other_site = content.create_site(db, other.id, "Other", "other-test")
        product = catalog_admin.create_product(db, other_site, {"name": "Private item"})
        page = content.site_pages(db, site)[0]
        events = list(run_turn(db, site, owner, "Read it", page_id=page.id, transport=FakeTransport(
            call("get_product", {"product_id": product.id}), {"content": "That item is unavailable."})))
        assert any(event["type"] == "tool_result" and '"error"' in event["summary"] for event in events)
        assert db.scalar(select(Product).where(Product.id == product.id)).name == "Private item"
    engine.dispose()


def _propose_update(db, site, user, product, name="Raspberry crate"):
    existing_ids = set(db.scalars(select(AgentAction.id)).all())
    page = content.site_pages(db, site)[0]
    list(run_turn(db, site, user, "Update it", page_id=page.id, transport=FakeTransport(
        call("update_product", {"product_id": product.id, "name": name}),
        {"content": "I proposed a product update for confirmation."})))
    return db.scalar(select(AgentAction).where(AgentAction.id.not_in(existing_ids)))


def _propose_tool(db, site, user, name, args):
    existing_ids = set(db.scalars(select(AgentAction.id)).all())
    page = content.site_pages(db, site)[0]
    list(run_turn(db, site, user, "Please do this", page_id=page.id, transport=FakeTransport(
        call(name, args), {"content": "I proposed this change for confirmation."})))
    return db.scalar(select(AgentAction).where(AgentAction.id.not_in(existing_ids)))


def test_decline_leaves_product_unchanged():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="decline@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Decline", "decline-test")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        action = _propose_update(db, site, user, product)
        assert decline_action(db, site, user, action.id).status == "declined"
        assert product.name == "Raspberry box"
        assert confirm_action(db, site, user, action.id).status == "declined"
        assert product.name == "Raspberry box"
    engine.dispose()


def test_double_confirm_executes_once_and_stale_version_refuses():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="stale@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Stale", "stale-test")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        action = _propose_update(db, site, user, product)
        confirm_action(db, site, user, action.id)
        version = product.version
        confirm_action(db, site, user, action.id)
        assert product.name == "Raspberry crate"
        assert product.version == version

        stale = _propose_update(db, site, user, product, "Overwritten name")
        catalog_admin.update_product(db, site, product.id, {"description": "Changed elsewhere"},
                                     expected_version=product.version)
        assert confirm_action(db, site, user, stale.id).status == "failed"
        assert product.name == "Raspberry crate"
    engine.dispose()


def test_action_older_than_24_hours_expires_without_execution():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="expired@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Expired", "expired-test")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        action = _propose_update(db, site, user, product)
        action.created_at = datetime.now(UTC) - timedelta(hours=24, seconds=1)
        db.flush()
        assert confirm_action(db, site, user, action.id).status == "expired"
        assert product.name == "Raspberry box"
    engine.dispose()


def test_membership_and_tenant_scope_are_required_for_decisions():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="scope-owner@example.test", name="Owner")
        stranger = User(email="scope-stranger@example.test", name="Stranger")
        other = User(email="scope-other@example.test", name="Other")
        db.add_all([owner, stranger, other])
        db.flush()
        site = content.create_site(db, owner.id, "Scope", "scope-test")
        other_site = content.create_site(db, other.id, "Other", "scope-other")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        foreign_product = catalog_admin.create_product(db, other_site, {"name": "Private item"})
        action = _propose_update(db, site, owner, product)
        with pytest.raises(CommerceError, match="membership"):
            confirm_action(db, site, stranger, action.id)
        with pytest.raises(CommerceError, match="not found"):
            confirm_action(db, other_site, other, action.id)
        assert action.status == "pending"
        assert product.name == "Raspberry box"
        assert foreign_product.name == "Private item"
    engine.dispose()


@pytest.mark.parametrize("decision", ["confirm", "decline"])
def test_decision_routes_require_csrf(decision):
    routes = {}
    def register(path, **_kwargs):
        return lambda endpoint: routes.__setitem__(path, endpoint)

    register_routes(register, lambda _session: "user", lambda _session: "token",
                    lambda _session, _form: (_ for _ in ()).throw(CommerceError("CSRF rejected")),
                    lambda *args: None, lambda exc: {"error": str(exc)})
    endpoint = routes[f"/admin/sites/{{site_id}}/agent/actions/{{action_id}}/{decision}"]

    class Request:
        session = {}

        async def form(self):
            return {}

    import asyncio
    result = asyncio.run(endpoint("site", "action", Request()))
    assert result == {"error": "CSRF rejected"}


def test_create_product_proposal_is_read_only_and_confirmation_creates_draft_once():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="create-product@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Create product", "create-product-test")
        before = db.scalar(select(Product.id).where(Product.tenant_id == site.tenant_id))
        action = _propose_tool(db, site, user, "create_product", {
            "name": "Berry box", "description": "Fresh berries", "variants": [
                {"name": "Small", "price_minor": 1200}, {"name": "Large", "price_minor": 2400}]})
        assert db.scalar(select(Product.id).where(Product.tenant_id == site.tenant_id)) == before
        assert action.status == "pending"
        confirm_action(db, site, user, action.id)
        created = db.scalar(select(Product).where(Product.tenant_id == site.tenant_id,
                                                   Product.name == "Berry box"))
        assert created.status == "draft"
        assert sorted(v.price_minor for v in db.scalars(select(VariantChannelListing).where(
            VariantChannelListing.variant_id.in_([v.id for v in created.variants])))) == [1200, 2400]
        confirm_action(db, site, user, action.id)
        assert len(list(db.scalars(select(Product).where(Product.tenant_id == site.tenant_id,
                                                         Product.name == "Berry box")))) == 1
    engine.dispose()


def test_inventory_proposal_is_read_only_and_confirmation_adjusts_once():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="inventory@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Inventory", "inventory-test")
        product = catalog_admin.create_product(db, site, {"name": "Berry box"})
        variant = product.variants[0]
        warehouse = Warehouse(tenant_id=site.tenant_id, code="main", name="Main")
        db.add(warehouse)
        db.flush()
        action = _propose_tool(db, site, user, "adjust_inventory", {
            "variant_id": variant.id, "warehouse_id": warehouse.id, "delta": 5, "reason": "received"})
        assert db.scalar(select(Stock).where(Stock.variant_id == variant.id)) is None
        assert db.scalar(select(InventoryAdjustment.id)) is None
        confirm_action(db, site, user, action.id)
        stock = db.scalar(select(Stock).where(Stock.variant_id == variant.id))
        assert stock.quantity == 5
        assert len(list(db.scalars(select(InventoryAdjustment).where(
            InventoryAdjustment.variant_id == variant.id)))) == 1
        confirm_action(db, site, user, action.id)
        assert stock.quantity == 5
    engine.dispose()


def test_discount_create_update_and_deactivate_proposals_are_read_only_and_execute_once():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="discount@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Discount", "discount-test")
        create_args = {"title": "Ten off", "type": "amount_off_order", "method": "code",
                       "value_type": "percentage", "value": 10, "codes": "TENOFF"}
        action = _propose_tool(db, site, user, "create_discount", create_args)
        assert db.scalar(select(Discount.id).where(Discount.site_id == site.id)) is None
        confirm_action(db, site, user, action.id)
        assert action.status == "confirmed", action.result_json
        rule = db.scalar(select(Discount).where(Discount.site_id == site.id))
        assert rule.title == "Ten off" and rule.value == 10
        confirm_action(db, site, user, action.id)
        assert len(list(db.scalars(select(Discount).where(Discount.site_id == site.id)))) == 1

        update = _propose_tool(db, site, user, "update_discount", {
            "discount_id": rule.id, "title": "Twenty off", "type": "amount_off_order",
            "method": "code", "value_type": "percentage", "value": 20, "codes": "TWENTY"})
        assert rule.title == "Ten off" and rule.value == 10
        confirm_action(db, site, user, update.id)
        assert rule.title == "Twenty off" and rule.value == 20
        confirm_action(db, site, user, update.id)
        assert rule.value == 20

        deactivate = _propose_tool(db, site, user, "deactivate_discount", {"discount_id": rule.id})
        assert rule.is_active is True
        confirm_action(db, site, user, deactivate.id)
        assert rule.is_active is False
        confirm_action(db, site, user, deactivate.id)
        assert rule.is_active is False
    engine.dispose()


def test_shipping_proposal_is_read_only_and_confirmation_applies_once():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="shipping@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Shipping", "shipping-test")
        action = _propose_tool(db, site, user, "update_shipping_settings", {
            "flat_rate_minor": 650, "free_shipping_threshold_minor": 8000})
        assert db.scalar(select(SiteCommerceSettings.id).where(
            SiteCommerceSettings.site_id == site.id)) is None
        confirm_action(db, site, user, action.id)
        assert action.status == "confirmed", action.result_json
        settings = db.scalar(select(SiteCommerceSettings).where(
            SiteCommerceSettings.site_id == site.id, SiteCommerceSettings.tenant_id == site.tenant_id))
        assert (settings.shipping_minor, settings.free_shipping_threshold_minor) == (650, 8000)
        version = settings.version
        confirm_action(db, site, user, action.id)
        assert settings.version == version
    engine.dispose()


def test_tracking_ids_use_agent_confirm_card_and_update_published_settings():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="tracking-agent@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Tracking", "tracking-agent-test")
        site.status = "published"
        site.settings_json = {**site.settings_json, "ga4_measurement_id": "G-OLD1234567"}
        site.published_settings_json = {"ga4_measurement_id": "G-OLD1234567"}
        from app.store_agent_ops import register_tools

        settings = register_tools(db, site, user.id, "tracking-agent-read")[
            "get_store_settings"].handler({})
        assert settings["tracking"]["ga4_measurement_id"] == "G-OLD1234567"
        action = _propose_tool(db, site, user, "set_tracking_ids", {
            "ga4_measurement_id": "G-NEW1234567",
            "meta_pixel_id": "123456789",
        })

        assert action.status == "pending"
        assert site.published_settings_json["ga4_measurement_id"] == "G-OLD1234567"
        result = confirm_action(db, site, user, action.id)
        assert result.status == "confirmed"
        assert site.settings_json["tracking"]["ga4_measurement_id"] == "G-NEW1234567"
        assert site.published_settings_json["tracking"] == site.settings_json["tracking"]
        assert "ga4_measurement_id" not in site.published_settings_json
        confirm_action(db, site, user, action.id)
        assert site.version == action.result_json["version"]
    engine.dispose()


def test_customer_update_and_message_are_pending_scoped_and_single_execution():
    from tests.test_order_management import workspace

    with workspace() as w:
        original_consent = w.customer.email_marketing_consent
        action = _propose_tool(w.db, w.site, w.owner, "update_customer", {
            "customer_id": w.customer.id, "tags": ["vip"], "note": "Call before delivery"})
        assert w.customer.tags_json == [] and w.customer.note == ""
        confirm_action(w.db, w.site, w.owner, action.id)
        assert action.status == "confirmed" and w.customer.tags_json == ["vip"]
        assert w.customer.note == "Call before delivery"
        assert w.customer.email_marketing_consent is original_consent
        confirm_action(w.db, w.site, w.owner, action.id)
        assert w.customer.tags_json == ["vip"]

        message = _propose_tool(w.db, w.site, w.owner, "send_customer_email", {
            "customer_id": w.customer.id, "subject": "Delivery detail", "body": "Your parcel is ready."})
        assert w.db.scalar(select(CommerceMail).where(CommerceMail.kind == "merchant_message")) is None
        confirm_action(w.db, w.site, w.owner, message.id)
        queued = w.db.scalar(select(CommerceMail).where(CommerceMail.kind == "merchant_message"))
        assert queued.customer_id == w.customer.id and queued.status == "pending"
        confirm_action(w.db, w.site, w.owner, message.id)
        assert len(list(w.db.scalars(select(CommerceMail).where(
            CommerceMail.kind == "merchant_message")))) == 1
        from app.integrations.commerce_email import dispatch_mail
        sent = []
        assert dispatch_mail(queued.id, sessions=w.sessions,
            sender=lambda recipient, subject, body, **_kwargs: (
                sent.append((recipient, subject, body)) or ("sent", "provider-fixture")))
        assert sent[0][0] == w.customer.email and sent[0][1] == "Delivery detail"
        assert "transactional message" in sent[0][2]


def test_marketing_email_without_consent_is_refused_at_proposal():
    from tests.test_order_management import workspace

    with workspace() as w:
        before = w.db.scalar(select(AgentAction.id))
        _propose_tool(w.db, w.site, w.owner, "send_customer_email", {
            "customer_id": w.customer.id, "subject": "Offers", "body": "Buy this", "marketing": True})
        assert w.db.scalar(select(AgentAction.id)) == before
        assert w.db.scalar(select(CommerceMail).where(CommerceMail.kind == "merchant_message")) is None


def test_fulfil_order_proposal_and_confirmation_use_shipment_path():
    from sqlalchemy import func

    from app.models import OrderLine, ShipmentEvent
    from tests.test_order_management import workspace

    with workspace() as w:
        line = w.db.scalar(select(OrderLine).where(OrderLine.order_id == w.order.id))
        action = _propose_tool(w.db, w.site, w.owner, "fulfil_order", {
            "order_id": w.link.id, "carrier": "UPS", "tracking_number": "TRACK-123"})
        assert line.fulfilled_quantity == 0
        assert w.db.scalar(select(func.count(ShipmentEvent.id))) == 0
        confirm_action(w.db, w.site, w.owner, action.id)
        assert action.status == "confirmed" and line.fulfilled_quantity == line.quantity
        assert w.db.scalar(select(func.count(ShipmentEvent.id))) == 1
        confirm_action(w.db, w.site, w.owner, action.id)
        assert w.db.scalar(select(func.count(ShipmentEvent.id))) == 1


def test_refund_order_validates_remainder_and_confirms_with_fake_gateway():
    from tests.test_order_management import workspace

    class Gateway:
        calls = []

        def __init__(self, _site):
            pass

        def create_refund(self, command_id, payload):
            self.calls.append((command_id, payload))
            return {"id": "re_" + command_id, "payment_intent": payload["payment_intent"],
                    "amount": payload["amount"], "currency": "usd", "status": "succeeded",
                    "livemode": False}

    with workspace() as w:
        before = w.db.scalar(select(AgentAction.id))
        _propose_tool(w.db, w.site, w.owner, "refund_order", {
            "order_id": w.link.id, "amount_minor": 10001,
            "reason": "Customer requested a refund."})
        assert w.db.scalar(select(AgentAction.id)) == before
        action = _propose_tool(w.db, w.site, w.owner, "refund_order", {
            "order_id": w.link.id, "amount_minor": 2500,
            "reason": "Customer requested a refund."})
        assert "25.00" in action.summary and "USD" in action.summary
        assert not Gateway.calls
        result = confirm_action(w.db, w.site, w.owner, action.id, gateway_factory=Gateway)
        assert result.status == "confirmed", result.result_json
        assert result.result_json["amount_minor"] == 2500
        assert len(Gateway.calls) == 1
        confirm_action(w.db, w.site, w.owner, action.id, gateway_factory=Gateway)
        assert len(Gateway.calls) == 1


def test_subscription_tools_use_versioned_merchant_change():
    from datetime import UTC, datetime, timedelta

    from app.models import CheckoutAttempt, CommerceQuote, SubscriptionContract
    from tests.test_order_management import workspace

    with workspace() as r:
        quote = CommerceQuote(tenant_id=r.site.tenant_id, site_id=r.site.id, fingerprint="agent-sub",
            provider_id="agent-sub", currency="USD", subtotal_minor=10000, discount_minor=0,
            shipping_minor=0, tax_minor=0, total_minor=10000,
            expires_at=datetime.now(UTC) + timedelta(days=1), snapshot_json={})
        r.db.add(quote)
        r.db.flush()
        attempt = CheckoutAttempt(tenant_id=r.site.tenant_id, site_id=r.site.id,
            customer_id=r.customer.id, quote_id=quote.id, request_key="agent-sub-attempt",
            request_hash="agent-sub", state="paid", order_id=r.order.id, expires_at=quote.expires_at)
        r.db.add(attempt)
        r.db.flush()
        subscription = SubscriptionContract(tenant_id=r.site.tenant_id, site_id=r.site.id,
            customer_id=r.customer.id, initial_attempt_id=attempt.id, stripe_customer_id="cus_fixture",
            payment_method_id="pm_fixture", state="active", interval_months=1, anchor_day=1,
            next_due_at=datetime.now(UTC) + timedelta(days=30), lines_json=[], destination_json={},
            recipient_name="Buyer", consent_json={}, version=1)
        r.db.add(subscription)
        r.db.flush()
        action = _propose_tool(r.db, r.site, r.owner, "pause_subscription", {
            "contract_id": subscription.id})
        assert subscription.state == "active"
        confirm_action(r.db, r.site, r.owner, action.id)
        assert action.status == "confirmed" and subscription.state == "paused"
        confirm_action(r.db, r.site, r.owner, action.id)
        assert subscription.version == 2

        stale = _propose_tool(r.db, r.site, r.owner, "resume_subscription", {
            "contract_id": subscription.id})
        subscription.version += 1
        r.db.flush()
        confirm_action(r.db, r.site, r.owner, stale.id)
        assert stale.status == "failed" and subscription.state == "paused"


def test_new_order_customer_tools_refuse_stale_proposals():
    from datetime import UTC, datetime, timedelta

    from app.models import OrderLine
    from tests.test_order_management import workspace

    with workspace() as w:
        order_action = _propose_tool(w.db, w.site, w.owner, "fulfil_order", {
            "order_id": w.link.id, "carrier": "UPS", "tracking_number": "TRACK-STALE"})
        w.order.updated_at = datetime.now(UTC) + timedelta(minutes=1)
        w.db.flush()
        assert confirm_action(w.db, w.site, w.owner, order_action.id).status == "failed"
        line = w.db.scalar(select(OrderLine).where(OrderLine.order_id == w.order.id))
        assert line.fulfilled_quantity == 0

        customer_action = _propose_tool(w.db, w.site, w.owner, "update_customer", {
            "customer_id": w.customer.id, "tags": ["vip"], "note": "Stale"})
        w.customer.updated_at = datetime.now(UTC) + timedelta(minutes=1)
        w.db.flush()
        assert confirm_action(w.db, w.site, w.owner, customer_action.id).status == "failed"
        assert w.customer.tags_json == []


def test_new_tools_reject_foreign_tenant_entities():
    from app.models import ShopCustomer
    from tests.test_order_management import workspace

    with workspace() as w:
        other_user = User(email="foreign-agent@example.test", name="Foreign")
        w.db.add(other_user)
        w.db.flush()
        foreign_site = content.create_site(w.db, other_user.id, "Foreign", "foreign-agent")
        foreign_customer = ShopCustomer(tenant_id=foreign_site.tenant_id, site_id=foreign_site.id,
            email="foreign@example.test")
        w.db.add(foreign_customer)
        w.db.flush()
        from tests.test_order_management import add_order
        _foreign_order, foreign_link = add_order(w.db, foreign_site, foreign_customer,
            "FOREIGN-ORDER", 1000)
        for name, args in (
            ("fulfil_order", {"order_id": foreign_link.id, "carrier": "UPS", "tracking_number": "123"}),
            ("refund_order", {"order_id": foreign_link.id, "amount_minor": 100,
                               "reason": "Customer requested a refund."}),
            ("update_customer", {"customer_id": foreign_customer.id, "tags": [], "note": ""}),
            ("send_customer_email", {"customer_id": foreign_customer.id,
                "subject": "Hello", "body": "Transactional update."}),
        ):
            before = set(w.db.scalars(select(AgentAction.id)))
            _propose_tool(w.db, w.site, w.owner, name, args)
            assert set(w.db.scalars(select(AgentAction.id))) == before
