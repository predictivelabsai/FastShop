"""Signature-verified Stripe lifecycle endpoint for FastShop SaaS billing."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, Request
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app import billing
from app.db import SessionLocal
from app.models import PlatformBillingStripeEvent
from app.services import CommerceError


def process_verified_event(db, event: dict) -> str:
    """Handle one verified platform event once in the caller's transaction."""
    event_id = event.get("id")
    event_type = event.get("type")
    if not isinstance(event_id, str) or not event_id.startswith("evt_"):
        raise CommerceError("Invalid platform billing event.")
    if not isinstance(event_type, str) or not event_type or len(event_type) > 120:
        raise CommerceError("Invalid platform billing event.")
    previous = db.scalar(
        select(PlatformBillingStripeEvent).where(
            PlatformBillingStripeEvent.event_id == event_id
        )
    )
    if previous:
        return "processed (duplicate)"
    result, tenant_id = billing.process_event(db, event)
    db.add(
        PlatformBillingStripeEvent(
            tenant_id=tenant_id,
            event_id=event_id,
            event_type=event_type,
            processed_at=datetime.now(UTC),
        )
    )
    db.flush()
    return result


def register_billing_webhooks(api):
    @api.post("/v1/platform/billing/stripe-webhook", tags=["Platform billing"])
    async def stripe_billing_webhook(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 2_000_000:
                raise HTTPException(413, "Webhook body too large")

        def handle():
            try:
                config = billing.validated_configuration()
            except CommerceError as exc:
                raise HTTPException(503, "Platform billing is not configured") from exc
            try:
                event = billing.verify_webhook(
                    bytes(body),
                    request.headers.get("stripe-signature", ""),
                    config.webhook_secret,
                )
            except CommerceError as exc:
                raise HTTPException(400, "Invalid webhook signature or body") from exc
            with SessionLocal() as db:
                try:
                    result = process_verified_event(db, event)
                    db.commit()
                    return {"status": result}
                except CommerceError as exc:
                    db.rollback()
                    raise HTTPException(
                        503, "Platform billing reconciliation is pending"
                    ) from exc

        return await run_in_threadpool(handle)
