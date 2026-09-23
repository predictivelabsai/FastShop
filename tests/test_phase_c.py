"""Phase C: payment methods, bundled scheduler tick, carrier adapter seam."""

from types import SimpleNamespace

from app import scheduler
from app.checkout_payments import payment_methods_for
from app.integrations import carrier


def _site(payment_methods=None):
    settings = {}
    if payment_methods is not None:
        settings["payment_methods"] = payment_methods
    return SimpleNamespace(settings_json=settings)


def test_one_time_checkout_offers_configured_methods():
    assert payment_methods_for(_site(), recurring=False) == ["card"]
    assert payment_methods_for(_site(["card", "paypal"]), recurring=False) == ["card", "paypal"]
    # Card is always present even if a merchant only lists paypal.
    assert payment_methods_for(_site(["paypal"]), recurring=False) == ["card", "paypal"]
    # Unknown methods are dropped.
    assert payment_methods_for(_site(["card", "crypto"]), recurring=False) == ["card"]


def test_recurring_orders_stay_card_only_for_saved_card_renewals():
    assert payment_methods_for(_site(["card", "paypal"]), recurring=True) == ["card"]


def test_scheduler_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FASTSHOP_ENABLE_SCHEDULER", raising=False)
    assert scheduler.enabled() is False
    assert scheduler.start_scheduler() is False


def test_scheduler_tick_survives_job_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr("app.subscription_renewals.process_due", boom)
    monkeypatch.setattr("app.subscription_notifications.queue_upcoming", lambda **k: 0)
    result = scheduler.run_due_jobs()
    assert result["renewals"] == "error"  # a failing job never crashes the tick
    assert result["notices"] == 0


def test_carrier_default_is_manual_and_pluggable():
    provider = carrier.resolve_provider({})
    assert provider.name == "manual"
    assert provider.fetch("usps", "123") is None
    assert carrier.automatic_tracking_enabled({}) is False
    assert carrier.automatic_tracking_enabled({"carrier_provider": "manual"}) is False
