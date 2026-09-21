from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.checkout_payments import cancel, handoff, payment_command
from app.integrations.stripe_commerce import StripeGateway
from app.models import CheckoutAttempt, CommerceQuote, Order
from app.services import CommerceError
from tests.test_checkout_services import prepare, ready  # noqa: F401
from tests.test_us_commerce import shop  # noqa: F401


def setup_handoff(r):
    r.customer.name = "Test Recipient"
    attempt = prepare(r)
    r.db.commit()
    quote = r.db.get(CommerceQuote, attempt.quote_id)
    sessions = sessionmaker(bind=r.db.bind, expire_on_commit=False)
    response = {"id": "cs_test_payment", "livemode": False, "mode": "payment",
        "client_reference_id": attempt.id, "metadata": {"site_id": r.site.id, "quote_id": quote.id},
        "currency": "usd", "amount_total": quote.total_minor, "status": "open",
        "payment_status": "unpaid", "automatic_tax": {"status": "complete"},
        "total_details": {"amount_tax": quote.tax_minor, "amount_shipping": quote.shipping_minor},
        "url": "https://checkout.stripe.com/c/pay/cs_test_payment"}
    return attempt, quote, sessions, response


def test_handoff_persists_command_before_network_and_reuses_session(ready):
    r = ready
    attempt, quote, sessions, response = setup_handoff(r)
    commands = []
    class Gateway:
        def create_checkout(self, attempt_id, command):
            with sessions() as db:
                saved = db.get(CheckoutAttempt, attempt_id)
                assert saved.state == "creating" and saved.provider_payload_json == command
            commands.append(command)
            return response
        def checkout_status(self, session_id):
            assert session_id == response["id"]
            return response
    for _ in range(2):
        assert handoff(r.site.id, r.customer.id, attempt.id, sessions=sessions,
            gateway_factory=lambda site: Gateway()) == response["url"]
    assert len(commands) == 1
    command = commands[0]
    assert command["customer"]["shipping"]["address"]["state"] == "CA"
    assert command["session"]["automatic_tax"] == {"enabled": True}
    assert "shipping_address_collection" not in command["session"]
    assert sum(line["quantity"] * line["price_data"]["unit_amount"] for line in command["session"]["line_items"]) == quote.subtotal_minor


def test_timeout_retains_command_and_retry_ignores_mutated_customer(ready):
    r = ready
    attempt, quote, sessions, response = setup_handoff(r)
    commands = []
    class Gateway:
        def create_checkout(self, attempt_id, command):
            commands.append(command)
            if len(commands) == 1:
                raise CommerceError("Provider timeout")
            return response
    kwargs = {"sessions": sessions, "gateway_factory": lambda site: Gateway()}
    with pytest.raises(CommerceError, match="timeout"):
        handoff(r.site.id, r.customer.id, attempt.id, **kwargs)
    r.db.refresh(attempt)
    assert attempt.state == "creating" and r.stock.allocated == 2
    r.customer.name = "Changed after submission"
    r.db.commit()
    handoff(r.site.id, r.customer.id, attempt.id, **kwargs)
    assert commands[0] == commands[1]


@pytest.mark.parametrize("change", [{"amount_total": 1}, {"total_details": {"amount_tax": 1, "amount_shipping": 1000}},
    {"url": "https://checkout.stripe.com.attacker.test/pay"}, {"automatic_tax": {"status": "requires_location_inputs"}}])
def test_handoff_never_returns_unverified_payment_url(ready, change):
    r = ready
    attempt, quote, sessions, response = setup_handoff(r)
    response.update(change)
    gateway = SimpleNamespace(create_checkout=lambda attempt_id, command: response)
    with pytest.raises(CommerceError):
        handoff(r.site.id, r.customer.id, attempt.id, sessions=sessions, gateway_factory=lambda site: gateway)
    r.db.refresh(attempt)
    assert attempt.stripe_session_id == response["id"]  # Reference retained for cancellation.
    assert attempt.order_id is None


def test_handoff_checks_customer_and_stops_retry_before_provider_key_expiry(ready):
    r = ready
    attempt, quote, sessions, response = setup_handoff(r)
    def unavailable(site):
        raise AssertionError("Must not contact provider")
    with pytest.raises(CommerceError, match="not found"):
        handoff(r.site.id, "another-customer", attempt.id, sessions=sessions, gateway_factory=unavailable)
    attempt.state = "creating"
    attempt.provider_payload_json = payment_command(r.site, attempt, quote, r.customer)
    attempt.provider_started_at = datetime.now(UTC) - timedelta(hours=24)
    r.db.commit()
    with pytest.raises(CommerceError, match="merchant reconciliation"):
        handoff(r.site.id, r.customer.id, attempt.id, sessions=sessions, gateway_factory=lambda site: object())


def test_stripe_creation_uses_stable_keys_and_customer_binding(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fixture")
    keys = []
    def reply(request):
        keys.append(request.headers["idempotency-key"])
        if request.url.path.endswith("/customers"):
            return httpx.Response(200, json={"id": "cus_fixture", "livemode": False})
        assert parse_qs(request.content.decode())["customer"] == ["cus_fixture"]
        return httpx.Response(200, json={"id": "cs_test_fixture", "livemode": False})
    gateway = StripeGateway(SimpleNamespace(id="test", slug="h24you"), transport=httpx.MockTransport(reply))
    for _ in range(2):
        gateway.create_checkout("attempt1", {"customer": {"email": "fixture@example.test"}, "session": {"mode": "payment"}})
    assert keys == ["fastshop-customer-attempt1", "fastshop-checkout-attempt1"] * 2


@pytest.mark.parametrize("recurring", [False, True])
def test_hosted_wallet_prerequisites_reach_stripe_without_changing_tax_destination(ready, monkeypatch, recurring):
    from app import checkout_services
    from tests.test_us_commerce import DESTINATION

    r = ready
    r.selections[0]["subscription"] = recurring
    attempt = checkout_services.prepare(r.db, r.site, r.customer.id, r.selections, DESTINATION,
        r.gateway, request_key="wallet-wire-fixture-01", recipient_name="Wallet Fixture", subscription_consent=recurring)
    quote = r.db.get(CommerceQuote, attempt.quote_id)
    command = payment_command(r.site, attempt, quote, r.customer)
    monkeypatch.setenv(f"FASTSHOP_STRIPE_{r.site.id.upper()}_SECRET_KEY", "sk_test_fixture")
    requests = []

    def reply(request):
        payload = parse_qs(request.content.decode())
        requests.append((request.url.path, payload))
        if request.url.path == "/v1/customers":
            # A saved shipping address, bound to the subsequently created
            # customer, satisfies the documented tax-related wallet prerequisite.
            assert payload["shipping[name]"] == ["Wallet Fixture"]
            for key, value in DESTINATION.items():
                assert payload[f"shipping[address][{key}]"] == [value]
            return httpx.Response(200, json={"id": "cus_walletfixture", "livemode": False})
        assert request.url.path == "/v1/checkout/sessions"
        assert payload["customer"] == ["cus_walletfixture"]
        assert payload["payment_method_types[0]"] == ["card"]
        assert "payment_method_types[1]" not in payload  # No unverified PayPal enablement.
        assert payload["automatic_tax[enabled]"] == ["true"]
        assert payload["customer_update[shipping]"] == ["never"]
        assert not any(key.startswith("shipping_address_collection") for key in payload)
        assert payload.get("payment_intent_data[setup_future_usage]") == (["off_session"] if recurring else None)
        return httpx.Response(200, json={"id": "cs_test_walletfixture", "livemode": False})

    gateway = StripeGateway(r.site, transport=httpx.MockTransport(reply))
    gateway.create_checkout(attempt.id, command)
    assert [path for path, _ in requests] == ["/v1/customers", "/v1/checkout/sessions"]


def test_cancel_mismatched_tax_session_releases_only_after_provider_expiration(ready):
    r = ready
    attempt, quote, sessions, response = setup_handoff(r)
    response["amount_total"] += 1
    class Gateway:
        def create_checkout(self, attempt_id, command):
            return response
        def checkout_status(self, session_id):
            return response
        def expire_checkout(self, session_id):
            response["status"] = "expired"
    kwargs = {"sessions": sessions, "gateway_factory": lambda site: Gateway()}
    with pytest.raises(CommerceError, match="differs"):
        handoff(r.site.id, r.customer.id, attempt.id, **kwargs)
    assert cancel(r.site.id, r.customer.id, attempt.id, **kwargs) == "expired"
    r.db.refresh(r.stock)
    assert r.stock.allocated == 0


def test_recovery_releases_unstarted_expiry_without_contacting_stripe(ready):
    from scripts.reconcile_checkouts import recover
    r = ready
    attempt, quote, sessions, response = setup_handoff(r)
    attempt.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    r.db.commit()
    def no_provider(site):
        raise AssertionError("An unstarted checkout has no Stripe payment")
    result = recover(sessions=sessions, gateway_factory=no_provider)
    assert result == {"checked": 1, "paid": 0, "expired": 1, "pending": 0, "needs_attention": 0}
    r.db.refresh(r.stock)
    assert r.stock.allocated == 0


def test_recovery_expires_open_provider_before_releasing(ready):
    from scripts.reconcile_checkouts import recover
    r = ready
    attempt, quote, sessions, response = setup_handoff(r)
    class Gateway:
        def create_checkout(self, attempt_id, command):
            return response
        def checkout_status(self, session_id):
            return response
        def expire_checkout(self, session_id):
            response["status"] = "expired"
    kwargs = {"sessions": sessions, "gateway_factory": lambda site: Gateway()}
    handoff(r.site.id, r.customer.id, attempt.id, **kwargs)
    r.db.refresh(attempt)
    attempt.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    r.db.commit()
    result = recover(**kwargs)
    assert result["expired"] == 1 and response["status"] == "expired"
    r.db.refresh(r.stock)
    assert r.stock.allocated == 0


def test_paid_handoff_keeps_recipient_and_product_snapshot(ready):
    from app import checkout_services
    r = ready
    attempt, quote, sessions, response = setup_handoff(r)
    gateway = SimpleNamespace(create_checkout=lambda attempt_id, command: response,
        checkout_status=lambda session_id: response)
    handoff(r.site.id, r.customer.id, attempt.id, sessions=sessions, gateway_factory=lambda site: gateway)
    r.customer.name = "Later name"
    r.variant.sku, r.variant.name = "LATER-SKU", "Later variant"
    r.db.commit()
    response.update(status="complete", payment_status="paid", payment_intent="pi_fixture")
    settled = checkout_services.reconcile(r.db, r.site, attempt.id, gateway)
    order = r.db.get(Order, settled.order_id)
    assert order.shipping_address_json["name"] == "Test Recipient"
    assert order.lines[0].sku == "TEST" and order.lines[0].variant_name == "Original"
