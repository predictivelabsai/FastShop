from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import content
from app import demo_commerce as demo
from app.models import (
    Base,
    CommerceMail,
    DemoCommand,
    Order,
    OutboxEvent,
    PaymentTransaction,
    ShopCustomer,
    Stock,
    User,
)
from app.services import CommerceError

ADDRESS = {"line1": "Example street — DEMO", "city": "Example", "state": "NY", "postal_code": "10001", "country": "US"}


@pytest.fixture
def simulation():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user, outsider = User(email="owner@example.test", name="Owner"), User(email="other@example.test", name="Other")
        db.add_all([user, outsider])
        db.flush()
        site = content.create_site(db, user.id, "Demo site", "demo-site")
        _, workspace = demo.workspace(db, site.id, user.id, create=True)
        db.commit()
        def command(action, **payload):
            result = demo.apply(db, site.id, user.id, uuid4().hex, workspace.version, action, payload)
            db.commit()
            db.refresh(workspace)
            return result
        yield db, user, outsider, site, workspace, command


def subscribe_and_quote(workspace, command):
    command("newsletter", consent=True)
    command("confirm", message_id=workspace.state_json["mail"][-1]["id"])
    command("cart", sku="sample-tea-original", quantity=2, subscription=True)
    command("quote", address=ADDRESS, offer=True, consent=True)


def test_full_simulation_has_no_real_commerce_side_effects(simulation, monkeypatch):
    import httpx
    monkeypatch.setattr(httpx.Client, "request", lambda *a, **k: pytest.fail("Unexpected external request"))
    db, user, _, site, workspace, command = simulation
    subscribe_and_quote(workspace, command)
    customer = workspace.state_json["customer"]
    assert customer["subscribed"] and not customer["verified"]
    quote = workspace.state_json["quote"]
    assert quote["subtotal_minor"] == 5392
    assert quote["discount_minor"] == 539
    assert quote["total_minor"] == 6321
    result = command("checkout", outcome="approved")
    assert workspace.state_json["catalog"]["sample-tea-original"]["stock"] == 28
    assert len(workspace.state_json["subscriptions"]) == 1
    command("login")
    command("confirm", message_id=workspace.state_json["mail"][-1]["id"])
    contract = workspace.state_json["subscriptions"][0]
    command("subscription_pause", contract_id=contract["id"])
    command("subscription_resume", contract_id=contract["id"])
    command("subscription_flavour", contract_id=contract["id"], sku="sample-tea-mint")
    command("subscription_frequency", contract_id=contract["id"], months="2")
    command("subscription_address", contract_id=contract["id"], address=ADDRESS | {"state": "CA", "postal_code": "94105"})
    command("subscription_payment", contract_id=contract["id"])
    command("subscription_renew", contract_id=contract["id"], outcome="approved")
    renewal = workspace.state_json["orders"][-1]
    assert renewal["quote"]["discount_minor"] == 0 and renewal["quote"]["destination"]["state"] == "CA"
    command("tracking", order_id=result["order_id"], status="shipped")
    command("tracking", order_id=result["order_id"], status="delivered")
    command("subscription_cancel", contract_id=contract["id"])
    assert workspace.state_json["subscriptions"][0]["state"] == "cancelled"
    assert workspace.state_json["orders"][0]["events"][-1]["status"] == "delivered"
    for model in (Order, PaymentTransaction, OutboxEvent, CommerceMail, ShopCustomer, Stock):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert site.status == "draft"  # Demo does not publish or activate the store.


def test_checkout_idempotency_and_different_payload_rejection(simulation):
    db, user, _, site, workspace, command = simulation
    subscribe_and_quote(workspace, command)
    key, version = uuid4().hex, workspace.version
    first = demo.apply(db, site.id, user.id, key, version, "checkout", {"outcome": "approved"})
    db.commit()
    repeated = demo.apply(db, site.id, user.id, key, version, "checkout", {"outcome": "approved"})
    assert first == repeated and len(workspace.state_json["orders"]) == 1
    with pytest.raises(CommerceError, match="different"):
        demo.apply(db, site.id, user.id, key, version, "checkout", {"outcome": "declined"})


def test_decline_and_mixed_stock_failure_are_atomic(simulation):
    _, _, _, _, workspace, command = simulation
    command("cart", sku="sample-tea-original", quantity=20, subscription=True)
    command("cart", sku="sample-tea-original", quantity=20, subscription=False)
    command("quote", address=ADDRESS, consent=True)
    command("checkout", outcome="declined")
    assert not workspace.state_json["orders"]
    with pytest.raises(CommerceError, match="stock"):
        command("checkout", outcome="approved")
    assert not workspace.state_json["orders"]
    assert workspace.state_json["catalog"]["sample-tea-original"]["stock"] == 30


def test_demo_requires_tenant_membership_and_current_version(simulation):
    db, user, outsider, site, workspace, command = simulation
    with pytest.raises(CommerceError, match="access denied"):
        demo.workspace(db, site.id, outsider.id)
    with pytest.raises(CommerceError, match="access denied"):
        demo.apply(db, site.id, outsider.id, uuid4().hex, workspace.version, "login", {})
    old_version = workspace.version
    command("login")
    with pytest.raises(CommerceError, match="changed"):
        demo.apply(db, site.id, user.id, uuid4().hex, old_version, "login", {})


def test_marketing_and_login_are_separate_and_confirmations_single_use(simulation):
    _, _, _, _, workspace, command = simulation
    with pytest.raises(CommerceError, match="consent"):
        command("newsletter")
    command("login")
    message = workspace.state_json["mail"][-1]["id"]
    command("confirm", message_id=message)
    assert workspace.state_json["customer"]["verified"] and not workspace.state_json["customer"]["subscribed"]
    with pytest.raises(CommerceError, match="already used"):
        command("confirm", message_id=message)
    command("newsletter", consent=True)
    pending = workspace.state_json["mail"][-1]["id"]
    command("unsubscribe")
    with pytest.raises(CommerceError, match="already used"):
        command("confirm", message_id=pending)


def test_recurring_consent_and_renewal_decline(simulation):
    _, _, _, _, workspace, command = simulation
    command("cart", sku="sample-tea-original", quantity=1, subscription=True)
    with pytest.raises(CommerceError, match="recurring"):
        command("quote", address=ADDRESS)
    command("quote", address=ADDRESS, consent=True)
    command("checkout", outcome="approved")
    contract_id = workspace.state_json["subscriptions"][0]["id"]
    with pytest.raises(CommerceError, match="sign-in"):
        command("subscription_renew", contract_id=contract_id, outcome="approved")
    command("login")
    command("confirm", message_id=workspace.state_json["mail"][-1]["id"])
    command("subscription_renew", contract_id=contract_id, outcome="declined")
    assert len(workspace.state_json["orders"]) == 1
    assert workspace.state_json["subscriptions"][0]["state"] == "paused"
    assert workspace.state_json["mail"][-1]["kind"] == "renewal_failed"


def test_bottle_subscription_and_non_us_address_rejected(simulation):
    _, _, _, _, _, command = simulation
    with pytest.raises(CommerceError):
        command("cart", sku="sample-cup", quantity=1, subscription=True)
    command("cart", sku="sample-cup", quantity=1)
    with pytest.raises(CommerceError):
        command("quote", address=ADDRESS | {"country": "EE"})


def test_failed_action_leaves_no_command_record(simulation):
    db, user, _, site, workspace, _ = simulation
    key = uuid4().hex
    with pytest.raises(CommerceError):
        demo.apply(db, site.id, user.id, key, workspace.version, "unknown", {})
    db.commit()  # Even callers that catch errors cannot commit partial state.
    assert db.scalar(select(DemoCommand).where(DemoCommand.command_id == key)) is None
    db.refresh(workspace)
    assert workspace.version == 1


def test_manual_sample_edit_invalidates_quote_and_preserves_real_catalog(simulation):
    from app.models import Product
    db, _, _, _, workspace, command = simulation
    command("cart", sku="sample-cup", quantity=1)
    command("quote", address=ADDRESS)
    command("catalog", sku="sample-cup", name="Sample travel cup", unit_minor="2100", stock="9")
    assert workspace.state_json["quote"] is None
    assert workspace.state_json["catalog"]["sample-cup"]["unit_minor"] == 2100
    assert db.scalar(select(func.count()).select_from(Product)) == 0


def test_demo_shipping_threshold_and_state_fixtures(simulation):
    _, _, _, _, workspace, command = simulation
    command("cart", sku="sample-cup", quantity=5)
    command("quote", address=ADDRESS)
    quote = workspace.state_json["quote"]
    assert quote["shipping_minor"] == 0 and quote["subtotal_minor"] == 9000
    assert quote["tax_minor"] == 720
    command("quote", address=ADDRESS | {"state": "TX", "postal_code": "78701"})
    assert workspace.state_json["quote"]["tax_minor"] == 563
    assert "not a real" in workspace.state_json["quote"]["tax_label"]
