"""Merchant timeline keeps distinct transactions while merging provider duplicates."""
from datetime import UTC, datetime
from types import SimpleNamespace

from app.order_admin_ui import merchant_timeline


def test_timeline_merges_provider_refund_and_command_without_hiding_other_refunds():
    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    def payment(reference):
        return dict(at=now, kind="payment", title="Refund", detail="Succeeded · USD 30.00.",
                    payment_reference=reference, payment_provider="stripe:site",
                    payment_kind="refund", payment_status="succeeded", amount_minor=3000,
                    currency="USD")
    command = SimpleNamespace(provider_refund_id="refund-1", amount_minor=3000,
                              state="succeeded", reason="Customer returned an unopened item.")
    visible, integration = merchant_timeline([
        payment("refund-1"), payment("refund-2"),
        dict(at=now, kind="refund", title="Refund succeeded", detail="", command=command),
        dict(at=now, kind="outbox", title="Order Refunded",
             detail="Delivery state: pending; attempts: 0."),
    ], "USD")
    assert len(visible) == 2
    assert all("30.00" in event["title"] and "succeeded" in event["title"] for event in visible)
    assert visible[-1]["detail"] == command.reason
    assert len(integration) == 1 and integration[0]["kind"] == "outbox"


def test_timeline_humanises_payment_state_and_preserves_internal_note_verbatim():
    now = datetime.now(UTC)
    note = "The customer mentioned partially_refunded in their email."
    visible, integration = merchant_timeline([
        dict(at=now, kind="order", title="Order confirmed",
             detail="Payment state: partially_refunded."),
        dict(at=now, kind="comment", title="Internal note · Ada", detail=note),
        dict(at=now, kind="reconciliation", title="Checkout reconciliation",
             detail="Checkout state: awaiting_provider."),
    ], "USD")
    assert visible[0]["detail"] == "Payment partially refunded."
    assert visible[1]["detail"] == note
    assert len(integration) == 1


def test_timeline_merges_repeated_charge_identity_but_preserves_distinct_payments():
    now = datetime.now(UTC)
    payment = dict(at=now, kind="payment", title="Charge", detail="",
                   payment_reference="charge-1", payment_provider="stripe:site",
                   payment_kind="charge", payment_status="succeeded", amount_minor=4000,
                   currency="USD")
    visible, _ = merchant_timeline([payment, dict(payment),
                                   payment | {"payment_reference": "charge-2"}], "USD")
    assert len(visible) == 2
    assert all("Payment of" in event["title"] for event in visible)
