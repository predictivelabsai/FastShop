"""Stripe callback boundary. No browser cookies, card payload storage or demo payments."""

import re

from fastapi import HTTPException, Request
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app import checkout_services as checkout
from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway, credentials, verify_webhook
from app.models import Site
from app.services import CommerceError

CHECKOUT_EVENTS = {
    "checkout.session.completed", "checkout.session.expired",
    "checkout.session.async_payment_succeeded", "checkout.session.async_payment_failed",
}
RENEWAL_EVENTS = {"payment_intent.succeeded", "payment_intent.payment_failed",
    "payment_intent.processing", "payment_intent.canceled", "payment_intent.requires_action"}


def process_event(db, site, event, gateway):
    if event.get("livemode") is not False:
        raise CommerceError("Live events are not enabled.")
    if event.get("type") not in CHECKOUT_EVENTS | RENEWAL_EVENTS:
        return "ignored"
    data = event.get("data")
    if not isinstance(data, dict):
        raise CommerceError("Invalid checkout event.")
    obj = data.get("object", {})
    if event.get("type") in RENEWAL_EVENTS:
        if not isinstance(obj, dict) or obj.get("object") != "payment_intent":
            raise CommerceError("Invalid payment event.")
        metadata = obj.get("metadata") or {}
        if metadata.get("site_id") != site.id or not metadata.get("cycle_id"):
            return "ignored"
        from app import subscription_renewals as renewals
        checkout.lock_site(db, site)
        cycle, contract, attempt, quote = renewals.load_cycle(db, site, metadata["cycle_id"])
        intent = gateway.payment_intent_status(obj.get("id", ""))
        renewals.verify_intent(intent, cycle)
        cycle.payment_intent_id = intent["id"]
        db.flush()
        renewals.settle(db, site, cycle.id, intent)
        return "processed"
    if not isinstance(obj, dict) or obj.get("object") != "checkout.session":
        raise CommerceError("Invalid checkout event.")
    if obj.get("mode") == "setup":
        # Card updates are verified by their own customer-owned setup flow;
        # they are not payment checkout attempts and must never create an order.
        return "ignored"
    metadata = obj.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("site_id") != site.id:
        return "ignored"  # The account may host unrelated integrations.
    attempt_id, session_id = obj.get("client_reference_id"), obj.get("id")
    if not isinstance(attempt_id, str) or not isinstance(session_id, str) or not re.fullmatch(r"cs_test_[A-Za-z0-9]+", session_id):
        raise CommerceError("Invalid checkout reference.")
    checkout.lock_site(db, site)
    attempt = checkout.owned_attempt(db, site, attempt_id)
    if attempt.stripe_session_id and attempt.stripe_session_id != session_id:
        raise CommerceError("Checkout session does not match.")
    if not attempt.stripe_session_id:
        # Handles a webhook beating the HTTP creation response (or a process crash).
        if attempt.state != "creating":
            raise CommerceError("Checkout is not awaiting a provider session.")
        attempt.stripe_session_id = session_id
        db.flush()
    checkout.reconcile(db, site, attempt.id, gateway)
    return "processed"


def register_commerce_webhooks(api):
    @api.post("/v1/commerce/{site_id}/stripe-webhook", tags=["Commerce"])
    async def stripe_webhook(site_id: str, request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 2_000_000:
                raise HTTPException(413, "Webhook body too large")

        def handle():
            with SessionLocal() as db:
                site = db.scalar(select(Site).where(Site.id == site_id))
                if not site:
                    raise HTTPException(404, "Store not found")
                try:
                    event = verify_webhook(bytes(body), request.headers.get("stripe-signature", ""), credentials(site)[1])
                except CommerceError as exc:
                    raise HTTPException(400, "Invalid webhook signature or body") from exc
                try:
                    result = process_event(db, site, event, StripeGateway(site))
                    db.commit()
                    return {"status": result}
                except CommerceError as exc:
                    db.rollback()
                    # A retryable response lets Stripe redeliver after provider/stock recovery.
                    raise HTTPException(503, "Payment reconciliation is pending") from exc

        return await run_in_threadpool(handle)
