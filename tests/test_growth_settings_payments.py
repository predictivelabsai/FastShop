"""Merchant sandbox tax quotes survive the hosted payment boundary exactly."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.checkout_payments import handoff, matches_quote, payment_command
from app.models import CommerceQuote, SubscriptionCycle, SubscriptionEvent, TaxRate
from tests.test_checkout_services import prepare, ready  # noqa: F401
from tests.test_subscriptions import contract  # noqa: F401
from tests.test_us_commerce import shop  # noqa: F401


@pytest.mark.parametrize("inclusive,rate_bps", [(False, 1000), (True, 2000), (False, 0)])
def test_local_tax_hosted_command_exact_total_and_matching(ready, inclusive, rate_bps):
    r = ready
    r.customer.name = "Sandbox recipient"
    r.db.add(TaxRate(tenant_id=r.site.tenant_id, site_id=r.site.id, country="US",
                     rate_bps=rate_bps, prices_include_tax=inclusive))
    r.db.flush()
    attempt = prepare(r)
    quote = r.db.get(CommerceQuote, attempt.quote_id)
    assert quote.snapshot_json["local_tax"]["rate_bps"] == rate_bps
    command = payment_command(r.site, attempt, quote, r.customer)
    session = command["session"]
    assert session["automatic_tax"] == {"enabled": False}
    assert sum(line["quantity"] * line["price_data"]["unit_amount"] for line in session["line_items"]) + quote.shipping_minor == quote.total_minor
    tax_lines = [line for line in session["line_items"] if line["price_data"]["product_data"]["name"] == "Sandbox sales tax"]
    assert len(tax_lines) == int(quote.tax_minor > 0)
    if tax_lines:
        assert tax_lines[0]["price_data"]["unit_amount"] == quote.tax_minor
    result = {"id": "cs_test_localtax", "livemode": False, "mode": "payment",
        "client_reference_id": attempt.id, "metadata": {"site_id": r.site.id, "quote_id": quote.id},
        "currency": "usd", "amount_total": quote.total_minor, "status": "open", "payment_status": "unpaid",
        "automatic_tax": {"enabled": False, "status": None},
        "total_details": {"amount_tax": 0, "amount_shipping": quote.shipping_minor},
        "url": "https://checkout.stripe.com/c/pay/cs_test_localtax"}
    assert matches_quote(result, attempt, quote)
    assert not matches_quote({**result, "livemode": True}, attempt, quote, live_mode=True)
    assert not matches_quote({**result, "automatic_tax": {"enabled": True, "status": "complete"}}, attempt, quote)
    assert not matches_quote({**result, "amount_total": quote.total_minor + 1}, attempt, quote)
    r.db.commit()
    calls = []
    class Gateway:
        def create_checkout(self, attempt_id, payload):
            calls.append(payload)
            return result
    sessions = sessionmaker(bind=r.db.bind, expire_on_commit=False)
    assert handoff(r.site.id, r.customer.id, attempt.id, sessions=sessions,
                   gateway_factory=lambda site: Gateway()) == result["url"]
    assert calls[0] == command


def test_default_provider_tax_command_is_unchanged(ready):
    r = ready
    r.customer.name = "Provider recipient"
    attempt = prepare(r)
    quote = r.db.get(CommerceQuote, attempt.quote_id)
    assert "local_tax" not in quote.snapshot_json
    assert payment_command(r.site, attempt, quote, r.customer)["session"]["automatic_tax"] == {"enabled": True}


def test_local_renewal_tax_ledger_completes_without_provider_transaction(contract):
    from app import subscription_renewals as renewals
    from tests.test_subscription_renewals import RenewalGateway
    r, sub = contract
    r.db.add(TaxRate(tenant_id=r.site.tenant_id, site_id=r.site.id, country="US", rate_bps=1000))
    r.db.flush()
    gateway = RenewalGateway()
    cycle = renewals.prepare_cycle(r.db, r.site, sub.id, gateway)
    r.db.commit()
    sessions = sessionmaker(bind=r.db.bind, expire_on_commit=False)
    kwargs = {"sessions": sessions, "gateway_factory": lambda site: gateway}
    assert renewals.run_cycle(r.site.id, cycle.id, **kwargs) == "paid"
    assert renewals.run_cycle(r.site.id, cycle.id, **kwargs) == "paid"
    renewals.record_tax(r.site.id, cycle.id, **kwargs)
    r.db.expire_all()
    assert cycle.tax_transaction_id.startswith("sandbox_local_")
    assert gateway.tax_records == 0
    assert r.db.scalar(select(func.count()).select_from(SubscriptionEvent).where(
        SubscriptionEvent.action == "sandbox_tax_recorded")) == 1
    assert r.db.scalar(select(func.count()).select_from(SubscriptionCycle).where(
        SubscriptionCycle.state == "paid", SubscriptionCycle.tax_transaction_id == "")) == 0
