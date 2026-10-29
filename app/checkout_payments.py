"""Durable hosted payment handoff; explicit commits surround provider network calls."""

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select

from app import checkout_services as checkout
from app import commerce
from app.config import settings
from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway, credentials, make_gateway
from app.models import CommerceQuote, ShopCustomer, Site, SubscriptionCycle
from app.services import CommerceError

# Card is always available; wallets (Apple/Google Pay) are presented automatically by
# Stripe hosted Checkout once the store domain is registered, so no extra type is needed
# for them. PayPal is opt-in per store and only for one-time orders — recurring orders
# stay card-only so the saved card can drive off-session renewals.
ALLOWED_PAYMENT_METHODS = {"card", "paypal"}


def payment_methods_for(site, recurring: bool) -> list[str]:
    if recurring:
        return ["card"]
    chosen = site.settings_json.get("payment_methods") if isinstance(site.settings_json, dict) else None
    methods = [m for m in (chosen or ["card"]) if m in ALLOWED_PAYMENT_METHODS]
    if "card" not in methods:
        methods.insert(0, "card")
    return methods


def payment_command(site, attempt, quote, customer, *, custom=False):
    local_tax = quote.snapshot_json.get("local_tax")
    recovery = bool(quote.snapshot_json.get("recovery_cycle_id"))
    if quote.snapshot_json.get("renewal_contract_id") and not recovery:
        raise CommerceError("Use subscription payment recovery for this renewal.")
    recurring = any(line["subscription"] for line in quote.snapshot_json["lines"]) and not recovery
    if recurring and (quote.snapshot_json.get("subscription_consent") or {}).get("accepted") is not True:
        raise CommerceError("Confirm recurring-payment consent before subscribing.")
    recipient = quote.snapshot_json.get("recipient_name") or customer.name
    if not recipient.strip():
        raise CommerceError("Enter the recipient's full name before payment.")
    root = "https://" + site.hostname if site.hostname else settings.public_url + "/sites/" + site.slug
    parsed = urlsplit(root)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")):
        raise CommerceError("Configure an HTTPS store URL before checkout.")
    lines = []
    for line in quote.snapshot_json["lines"]:
        # A cart-level discount can leave an amount not divisible by quantity.
        # Represent the whole pack as one line in that case, retaining the exact
        # tax calculation base without inventing fractional currency or tax fees.
        unit, remainder = divmod(line["amount_minor"], line["quantity"])
        lines.append({"quantity": 1 if remainder else line["quantity"], "price_data": {
            "currency": "usd", "unit_amount": line["amount_minor"] if remainder else unit,
            "tax_behavior": "exclusive", "product_data": {"name": line["name"] + (f" × {line['quantity']}" if remainder else ""),
                "tax_code": line["tax_code"]}}})
    if local_tax and quote.tax_minor:
        # Merchant tax rates are a sandbox-only calculation. Keep the exact
        # quoted tax as a clearly labelled line instead of asking Stripe Tax to
        # calculate a second, potentially different amount at hosted Checkout.
        lines.append({"quantity": 1, "price_data": {"currency": "usd",
            "unit_amount": quote.tax_minor, "tax_behavior": "exclusive",
            "product_data": {"name": "Sandbox sales tax"}}})
    gift_tender = quote.snapshot_json.get("gift_card")
    if gift_tender:
        lines = [{"quantity": 1, "price_data": {"currency": quote.currency.lower(),
            "unit_amount": quote.total_minor, "tax_behavior": "exclusive",
            "product_data": {"name": "Sandbox order balance after gift card"}}}]
    metadata = {"site_id": site.id, "checkout_id": attempt.id, "quote_id": quote.id}
    session = {"mode": "payment", "payment_method_types": payment_methods_for(site, recurring), "currency": "usd",
            "automatic_tax": {"enabled": not bool(local_tax) and not bool(gift_tender)}, "line_items": lines,
            "customer_update": {"address": "never", "shipping": "never", "name": "never"},
            "client_reference_id": attempt.id, "metadata": metadata,
            "payment_intent_data": {"metadata": metadata, **({"setup_future_usage": "off_session"} if recurring else {})},
            "shipping_options": [{"shipping_rate_data": {"type": "fixed_amount",
                "display_name": "US delivery", "tax_behavior": "exclusive",
                "fixed_amount": {"amount": 0 if gift_tender else quote.shipping_minor, "currency": "usd"}}}]}
    if custom:
        session.update(ui_mode="custom", return_url=root + "/checkout/" + attempt.id)
    else:
        session.update(success_url=root + "/checkout/" + attempt.id,
                       cancel_url=root + "/checkout/" + attempt.id)
    return {
        "customer": {"email": customer.email, "name": recipient,
            "shipping": {"name": recipient, "address": quote.snapshot_json["destination"]},
            "metadata": metadata},
        "session": session,
    }


def matches_quote(result, attempt, quote, *, live_mode=False):
    details = result.get("total_details") or {}
    metadata = result.get("metadata") or {}
    local_tax = quote.snapshot_json.get("local_tax")
    custom_mode_valid = (result.get("ui_mode") != "custom" or bool(result.get("return_url")))
    if (not custom_mode_valid or result.get("ui_mode") not in (None, "hosted", "custom")):
        return False
    if (local_tax or quote.snapshot_json.get("gift_card")) and live_mode:
        return False
    if quote.snapshot_json.get("gift_card"):
        return (result.get("livemode") is False and result.get("mode") == "payment"
            and result.get("client_reference_id") == attempt.id
            and metadata.get("site_id") == attempt.site_id and metadata.get("quote_id") == quote.id
            and result.get("currency") == quote.currency.lower()
            and type(result.get("amount_total")) is int and result["amount_total"] == quote.total_minor
            and details.get("amount_tax") == 0 and details.get("amount_shipping") == 0
            and (result.get("automatic_tax") or {}).get("enabled") is False)
    automatic_tax = result.get("automatic_tax") or {}
    tax_matches = (details.get("amount_tax") == 0
        and automatic_tax.get("enabled") is False
        and automatic_tax.get("status") in (None, "")) if local_tax else (
        details.get("amount_tax") == quote.tax_minor and automatic_tax.get("status") == "complete")
    custom = result.get("ui_mode") == "custom"
    return (result.get("livemode") is live_mode and result.get("mode") == "payment" and
        (custom or result.get("ui_mode") in (None, "hosted")) and
        (bool(result.get("return_url")) if custom else True) and
        result.get("client_reference_id") == attempt.id and metadata.get("site_id") == attempt.site_id and
        metadata.get("quote_id") == quote.id and result.get("currency") == quote.currency.lower() and
        type(result.get("amount_total")) is int and result["amount_total"] == quote.total_minor and
        tax_matches and details.get("amount_shipping") == quote.shipping_minor)


def handoff(site_id, customer_id, attempt_id, *, sessions=SessionLocal, gateway_factory=StripeGateway):
    with sessions() as db:
        site = db.scalar(select(Site).where(Site.id == site_id))
        if not site:
            raise CommerceError("Store not found.")
        checkout.lock_site(db, site)
        attempt = checkout.owned_attempt(db, site, attempt_id)
        if attempt.customer_id != customer_id:
            raise CommerceError("Checkout not found.")
        if db.scalar(select(SubscriptionCycle.id).where(SubscriptionCycle.attempt_id == attempt.id,
                SubscriptionCycle.site_id == site.id, SubscriptionCycle.tenant_id == site.tenant_id)):
            raise CommerceError("Use subscription payment recovery for this renewal.")
        if attempt.state in ("paid", "expired"):
            raise CommerceError("This checkout is already closed.")
        quote = db.scalar(select(CommerceQuote).where(CommerceQuote.id == attempt.quote_id,
            CommerceQuote.site_id == site.id, CommerceQuote.tenant_id == site.tenant_id))
        customer = db.scalar(select(ShopCustomer).where(ShopCustomer.id == customer_id,
            ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id, ShopCustomer.is_active.is_(True)))
        if not quote or not customer:
            raise CommerceError("Checkout details are unavailable.")
        mode = commerce.payment_mode(db, site)
        if mode == "live" and quote.snapshot_json.get("local_tax"):
            raise CommerceError("Merchant tax rates are available in sandbox mode only. Request a new live Stripe Tax quote.")
        if quote.snapshot_json.get("gift_card") and mode != "sandbox":
            raise CommerceError("Gift cards are available in sandbox mode only.")
        if quote.total_minor == 0 and quote.snapshot_json.get("gift_card"):
            if attempt.state != "prepared":
                raise CommerceError("This gift card checkout requires reconciliation.")
            checkout._paid_order(db, site, attempt, quote, {"external_id": "gift-only:"+attempt.id}, manual=True)
            db.commit()
            root = "https://"+site.hostname if site.hostname else settings.public_url+"/sites/"+site.slug
            return root+"/checkout/"+attempt.id
        gateway = make_gateway(gateway_factory, site, db)
        if not attempt.provider_payload_json:
            if attempt.state != "prepared":
                raise CommerceError("The provider command requires merchant reconciliation.")
            attempt.provider_payload_json = payment_command(site, attempt, quote, customer)
            attempt.provider_started_at = datetime.now(UTC)
            checkout.begin_provider(db, site, attempt.id)
        # Stripe may discard idempotency keys after 24h. Never issue an unknown
        # creation again outside that window; reconciliation must find its session.
        if not attempt.stripe_session_id and checkout.utc(attempt.provider_started_at) < datetime.now(UTC) - timedelta(hours=23):
            raise CommerceError("This payment attempt needs merchant reconciliation before retrying.")
        command, session_id = attempt.provider_payload_json, attempt.stripe_session_id
        db.commit()
        # No database locks held across payment creation. The command is already durable.
        result = gateway.checkout_status(session_id) if session_id else gateway.create_checkout(attempt.id, command)
        result_id = result.get("id", "")
        session_prefix = "cs_live_" if mode == "live" else "cs_test_"
        if not isinstance(result_id, str) or not re.fullmatch(session_prefix + r"[A-Za-z0-9]+", result_id):
            raise CommerceError("Stripe did not return a checkout reference for this payment mode.")
        metadata = result.get("metadata") or {}
        if result.get("livemode") is (mode != "live") or result.get("client_reference_id") != attempt.id or metadata.get("site_id") != site.id or metadata.get("quote_id") != quote.id:
            raise CommerceError("Stripe returned a checkout for a different order.")
        checkout.lock_site(db, site)
        attempt = checkout.owned_attempt(db, site, attempt_id)
        if attempt.stripe_session_id and attempt.stripe_session_id != result_id:
            raise CommerceError("Stripe returned a conflicting checkout reference.")
        attempt.stripe_session_id = result_id
        if attempt.state == "creating":
            attempt.state = "open"
        db.commit()  # Retain reference even when tax validation fails, so it can be expired.
        if not matches_quote(result, attempt, quote, live_mode=mode == "live"):
            raise CommerceError("Stripe's final tax or total differs from this quote. Do not pay; cancel and request a fresh quote.")
        if result.get("status") != "open" or attempt.state != "open":
            raise CommerceError("This checkout needs payment-status reconciliation.")
        url = result.get("url", "")
        parsed = urlsplit(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme != "https" or parsed.netloc != "checkout.stripe.com":
            raise CommerceError("Stripe did not return a valid hosted payment URL.")
        return url


def embedded_session(site_id, customer_id, attempt_id, *, sessions=SessionLocal, gateway_factory=StripeGateway):
    """Create or replay a custom Checkout Session and return browser-safe credentials."""
    with sessions() as db:
        site = db.scalar(select(Site).where(Site.id == site_id))
        if not site:
            raise CommerceError("Store not found.")
        checkout.lock_site(db, site)
        attempt = checkout.owned_attempt(db, site, attempt_id)
        if attempt.customer_id != customer_id:
            raise CommerceError("Checkout not found.")
        if attempt.state in ("paid", "expired"):
            raise CommerceError("This checkout is already closed.")
        quote = db.scalar(select(CommerceQuote).where(CommerceQuote.id == attempt.quote_id,
            CommerceQuote.site_id == site.id, CommerceQuote.tenant_id == site.tenant_id))
        customer = db.scalar(select(ShopCustomer).where(ShopCustomer.id == customer_id,
            ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id,
            ShopCustomer.is_active.is_(True)))
        if not quote or not customer:
            raise CommerceError("Checkout details are unavailable.")
        mode = commerce.payment_mode(db, site)
        if mode == "live" and quote.snapshot_json.get("local_tax"):
            raise CommerceError("Merchant tax rates are available in sandbox mode only. Request a new live Stripe Tax quote.")
        if quote.snapshot_json.get("gift_card") and mode != "sandbox":
            raise CommerceError("Gift cards are available in sandbox mode only.")
        gateway = make_gateway(gateway_factory, site, db)
        if not attempt.provider_payload_json:
            if attempt.state != "prepared":
                raise CommerceError("The provider command requires merchant reconciliation.")
            attempt.provider_payload_json = payment_command(site, attempt, quote, customer, custom=True)
            attempt.provider_started_at = datetime.now(UTC)
            checkout.begin_provider(db, site, attempt.id)
        elif attempt.provider_payload_json.get("session", {}).get("ui_mode") != "custom":
            raise CommerceError("This checkout was already prepared for hosted payment.")
        if not attempt.stripe_session_id and checkout.utc(attempt.provider_started_at) < datetime.now(UTC) - timedelta(hours=23):
            raise CommerceError("This payment attempt needs merchant reconciliation before retrying.")
        command, session_id = attempt.provider_payload_json, attempt.stripe_session_id
        db.commit()
        result = gateway.checkout_status(session_id) if session_id else gateway.create_checkout(attempt.id, command)
        result_id = result.get("id", "")
        prefix = "cs_live_" if mode == "live" else "cs_test_"
        metadata = result.get("metadata") or {}
        if (not isinstance(result_id, str) or not re.fullmatch(prefix + r"[A-Za-z0-9]+", result_id) or
                result.get("livemode") is not (mode == "live") or result.get("client_reference_id") != attempt.id or
                metadata.get("site_id") != site.id or metadata.get("quote_id") != quote.id):
            raise CommerceError("Stripe returned a checkout for a different order.")
        checkout.lock_site(db, site)
        attempt = checkout.owned_attempt(db, site, attempt_id)
        if attempt.stripe_session_id and attempt.stripe_session_id != result_id:
            raise CommerceError("Stripe returned a conflicting checkout reference.")
        attempt.stripe_session_id = result_id
        if attempt.state == "creating":
            attempt.state = "open"
        db.commit()
        if not matches_quote(result, attempt, quote, live_mode=mode == "live"):
            raise CommerceError("Stripe's final tax or total differs from this quote. Do not pay; cancel and request a fresh quote.")
        if result.get("status") != "open" or attempt.state != "open":
            raise CommerceError("This checkout needs payment-status reconciliation.")
        secret = result.get("client_secret")
        _, _, publishable = credentials(site)
        if not isinstance(secret, str) or not secret or not publishable:
            raise CommerceError("Configure Stripe publishable credentials for this site.")
        return {"client_secret": secret, "publishable_key": publishable}


def cancel(site_id, customer_id, attempt_id, *, sessions=SessionLocal, gateway_factory=StripeGateway):
    with sessions() as db:
        site = db.scalar(select(Site).where(Site.id == site_id))
        if not site:
            raise CommerceError("Store not found.")
        checkout.lock_site(db, site)
        attempt = checkout.owned_attempt(db, site, attempt_id)
        if attempt.customer_id != customer_id:
            raise CommerceError("Checkout not found.")
        if db.scalar(select(SubscriptionCycle.id).where(SubscriptionCycle.attempt_id == attempt.id,
                SubscriptionCycle.site_id == site.id, SubscriptionCycle.tenant_id == site.tenant_id)):
            raise CommerceError("Use subscription payment recovery for this renewal.")
        if attempt.state == "prepared":
            checkout.cancel_unstarted(db, site, attempt.id)
            db.commit()
            return "expired"
        if attempt.state in ("paid", "expired"):
            return attempt.state
        session_id = attempt.stripe_session_id
        if not session_id:
            raise CommerceError("The payment outcome is unknown. Retry recovery before cancelling.")
        commerce.payment_mode(db, site)
        gateway = make_gateway(gateway_factory, site, db)
        db.commit()
        current = gateway.checkout_status(session_id)
        if current.get("status") == "open":
            # A concurrent successful payment may make expiration fail. In either
            # case fetch again; a failure to fetch leaves reservations untouched.
            try:
                gateway.expire_checkout(session_id)
            except CommerceError:
                pass
        attempt = checkout.reconcile(db, site, attempt_id, gateway)
        db.commit()
        return attempt.state


def refund(site_id, user_id, site_order_id, amount_minor, reason, request_key, *,
           sessions=SessionLocal, gateway_factory=StripeGateway):
    """Use the durable provider-command path for an exact merchant refund."""
    from app.order_management import refund as execute_refund

    return execute_refund(
        site_id,
        user_id,
        site_order_id,
        amount_minor,
        reason,
        request_key,
        sessions=sessions,
        gateway_factory=gateway_factory,
    )
