import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app import subscription_payments
from app.commerce_webhooks import process_event
from app.models import SubscriptionEvent, SubscriptionPaymentSetup
from app.services import CommerceError
from tests.test_checkout_services import ready  # noqa: F401
from tests.test_subscriptions import contract  # noqa: F401
from tests.test_us_commerce import shop  # noqa: F401


class SetupGateway:
    def __init__(self):
        self.created = 0
        self.response = None
        self.setup = None

    def create_payment_setup(self, setup_id, payload):
        self.created += 1
        self.response = {"id": "cs_test_" + setup_id, "livemode": False, "mode": "setup",
            "customer": payload["customer"], "client_reference_id": setup_id, "metadata": payload["metadata"],
            "status": "open", "url": "https://checkout.stripe.com/c/pay/cs_test_" + setup_id}
        self.setup = {"id": "seti_fixture", "livemode": False, "customer": payload["customer"],
            "metadata": payload["metadata"], "usage": "off_session", "status": "succeeded", "payment_method": "pm_newcard"}
        return self.response

    def checkout_status(self, session_id):
        return self.response

    def setup_intent_status(self, setup_id):
        return self.setup


def options(r, gateway):
    return {"sessions": sessionmaker(bind=r.db.bind, expire_on_commit=False), "gateway_factory": lambda site: gateway}


def test_card_setup_reuses_pending_session_and_requires_verified_completion(contract):
    r, sub = contract
    gateway = SetupGateway()
    kwargs = options(r, gateway)
    first = subscription_payments.start(r.site.id, r.customer.id, sub.id, "card-update-request-01", **kwargs)
    second = subscription_payments.start(r.site.id, r.customer.id, sub.id, "card-update-request-02", **kwargs)
    assert first == second and gateway.created == 1
    setup = r.db.scalar(select(SubscriptionPaymentSetup))
    assert setup.command_json["mode"] == "setup" and "line_items" not in setup.command_json
    assert subscription_payments.finish(r.site.id, r.customer.id, sub.id, setup.id, **kwargs) == "pending"
    r.db.refresh(sub)
    assert sub.payment_method_id == "pm_fixture"
    gateway.response.update(status="complete", setup_intent="seti_fixture")
    assert subscription_payments.finish(r.site.id, r.customer.id, sub.id, setup.id, **kwargs) == "applied"
    assert subscription_payments.finish(r.site.id, r.customer.id, sub.id, setup.id, **kwargs) == "applied"
    r.db.refresh(sub)
    assert sub.payment_method_id == "pm_newcard"
    assert r.db.scalar(select(func.count(SubscriptionEvent.id)).where(SubscriptionEvent.action == "payment_updated")) == 1
    event = {"livemode": False, "type": "checkout.session.completed", "data": {"object": gateway.response | {"object": "checkout.session"}}}
    assert process_event(r.db, r.site, event, gateway) == "ignored"


@pytest.mark.parametrize("field,value", [("customer", "cus_foreign"), ("usage", "on_session"), ("status", "processing"), ("livemode", True)])
def test_unverified_setup_cannot_replace_saved_payment(contract, field, value):
    r, sub = contract
    gateway = SetupGateway()
    kwargs = options(r, gateway)
    subscription_payments.start(r.site.id, r.customer.id, sub.id, "card-update-request-01", **kwargs)
    setup = r.db.scalar(select(SubscriptionPaymentSetup))
    gateway.response.update(status="complete", setup_intent="seti_fixture")
    gateway.setup[field] = value
    with pytest.raises(CommerceError, match="could not be verified"):
        subscription_payments.finish(r.site.id, r.customer.id, sub.id, setup.id, **kwargs)
    r.db.refresh(sub)
    assert sub.payment_method_id == "pm_fixture"


def test_other_customer_cannot_start_or_finish_card_setup(contract):
    r, sub = contract
    gateway = SetupGateway()
    kwargs = options(r, gateway)
    with pytest.raises(CommerceError, match="not found"):
        subscription_payments.start(r.site.id, "foreign-customer", sub.id, "card-update-request-01", **kwargs)
    assert gateway.created == 0
