"""Card replacement through hosted Stripe setup; FastShop never receives card fields."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select

from app import checkout_services as checkout
from app import subscriptions
from app.config import settings
from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway
from app.models import Site, SubscriptionEvent, SubscriptionPaymentSetup
from app.services import CommerceError


def load(db, site_id, customer_id, contract_id):
    site = db.scalar(select(Site).where(Site.id == site_id))
    if not site:
        raise CommerceError("Store not found.")
    checkout.lock_site(db, site)
    contract = subscriptions.owned(db, site, customer_id, contract_id)
    return site, contract


def start(site_id, customer_id, contract_id, request_key, *, sessions=SessionLocal, gateway_factory=StripeGateway):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", request_key):
        raise CommerceError("Invalid card-update request key.")
    with sessions() as db:
        site, contract = load(db, site_id, customer_id, contract_id)
        if contract.state == "cancelled":
            raise CommerceError("This subscription is cancelled.")
        setup = db.scalar(select(SubscriptionPaymentSetup).where(SubscriptionPaymentSetup.contract_id == contract.id,
            SubscriptionPaymentSetup.site_id == site.id, SubscriptionPaymentSetup.tenant_id == site.tenant_id,
            SubscriptionPaymentSetup.customer_id == customer_id, SubscriptionPaymentSetup.request_key == request_key))
        if not setup:
            pending = db.scalar(select(SubscriptionPaymentSetup).where(SubscriptionPaymentSetup.contract_id == contract.id,
                SubscriptionPaymentSetup.site_id == site.id, SubscriptionPaymentSetup.tenant_id == site.tenant_id,
                SubscriptionPaymentSetup.customer_id == customer_id, SubscriptionPaymentSetup.state == "pending"))
            setup = pending
        if not setup:
            setup = SubscriptionPaymentSetup(site_id=site.id, tenant_id=site.tenant_id, contract_id=contract.id,
                customer_id=customer_id, request_key=request_key, command_json={})
            db.add(setup)
            db.flush()
            root = "https://" + site.hostname if site.hostname else settings.public_url + "/sites/" + site.slug
            parsed = urlsplit(root)
            if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")):
                raise CommerceError("Configure an HTTPS store URL before card setup.")
            return_url = root + "/account/subscriptions/" + contract.id + "/payment/" + setup.id
            metadata = {"site_id": site.id, "contract_id": contract.id, "setup_id": setup.id}
            setup.command_json = {"mode": "setup", "customer": contract.stripe_customer_id,
                "payment_method_types": ["card"], "client_reference_id": setup.id,
                "metadata": metadata, "setup_intent_data": {"metadata": metadata},
                "success_url": return_url, "cancel_url": return_url}
        if setup.state != "pending":
            raise CommerceError("This card-update request is already closed. Reload to start another.")
        if not setup.session_id and checkout.utc(setup.created_at) < datetime.now(UTC) - timedelta(hours=23):
            raise CommerceError("The previous card setup needs merchant reconciliation before retrying.")
        gateway = gateway_factory(site)
        setup_id, session_id, command = setup.id, setup.session_id, setup.command_json
        db.commit()
        result = gateway.checkout_status(session_id) if session_id else gateway.create_payment_setup(setup_id, command)
        validate_session(result, setup_id, command)
        site, contract = load(db, site_id, customer_id, contract_id)
        setup = db.scalar(select(SubscriptionPaymentSetup).where(SubscriptionPaymentSetup.id == setup_id,
            SubscriptionPaymentSetup.site_id == site.id, SubscriptionPaymentSetup.tenant_id == site.tenant_id,
            SubscriptionPaymentSetup.contract_id == contract.id, SubscriptionPaymentSetup.customer_id == customer_id)
            .execution_options(populate_existing=True))
        if setup.session_id and setup.session_id != result["id"]:
            raise CommerceError("Conflicting card-update reference.")
        setup.session_id = result["id"]
        db.commit()
        if result.get("status") != "open":
            raise CommerceError("Return to the card-update screen to verify or close this session.")
        parsed = urlsplit(result.get("url", ""))
        if parsed.scheme != "https" or parsed.netloc != "checkout.stripe.com":
            raise CommerceError("Invalid hosted card-update URL.")
        return result["url"]


def validate_session(result, setup_id, command):
    if (not re.fullmatch(r"cs_test_[A-Za-z0-9]+", str(result.get("id", ""))) or
            result.get("livemode") is not False or result.get("mode") != "setup" or
            result.get("customer") != command["customer"] or result.get("client_reference_id") != setup_id or
            any((result.get("metadata") or {}).get(key) != value for key, value in command["metadata"].items())):
        raise CommerceError("Card-update session does not match this subscription.")


def finish(site_id, customer_id, contract_id, setup_id, *, sessions=SessionLocal, gateway_factory=StripeGateway):
    with sessions() as db:
        site, contract = load(db, site_id, customer_id, contract_id)
        setup = db.scalar(select(SubscriptionPaymentSetup).where(SubscriptionPaymentSetup.id == setup_id,
            SubscriptionPaymentSetup.site_id == site.id, SubscriptionPaymentSetup.tenant_id == site.tenant_id,
            SubscriptionPaymentSetup.contract_id == contract.id, SubscriptionPaymentSetup.customer_id == customer_id))
        if not setup:
            raise CommerceError("Card-update request not found.")
        if setup.state != "pending":
            return setup.state
        if not setup.session_id:
            raise CommerceError("Retry the original card-update request to recover its provider session.")
        gateway = gateway_factory(site)
        result = gateway.checkout_status(setup.session_id)
        validate_session(result, setup.id, setup.command_json)
        if result["id"] != setup.session_id:
            raise CommerceError("Card-update reference mismatch.")
        if result.get("status") == "expired":
            setup.state = "expired"
        elif result.get("status") == "complete":
            intent = gateway.setup_intent_status(result.get("setup_intent", ""))
            if (intent.get("id") != result.get("setup_intent") or intent.get("livemode") is not False or
                    intent.get("status") != "succeeded" or intent.get("usage") != "off_session" or
                    intent.get("customer") != contract.stripe_customer_id or
                    not re.fullmatch(r"pm_[A-Za-z0-9]+", str(intent.get("payment_method", ""))) or
                    any((intent.get("metadata") or {}).get(key) != value for key, value in setup.command_json["metadata"].items())):
                raise CommerceError("The new payment method could not be verified.")
            if contract.state == "cancelled":
                setup.state = "cancelled"
            else:
                contract.payment_method_id = intent["payment_method"]
                contract.version += 1
                setup.state = "applied"
                db.add(SubscriptionEvent(tenant_id=site.tenant_id, site_id=site.id,
                    contract_id=contract.id, customer_id=customer_id, request_key="card-" + setup.id,
                    request_hash=hashlib.sha256(setup.id.encode()).hexdigest(), action="payment_updated",
                    details_json={"setup_id": setup.id, "version": contract.version, "applies_to": "future deliveries"}))
        db.commit()
        return setup.state
