"""Idempotent FastERP API connector; never writes into FastERP tables directly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Order, OutboxEvent

MAX_ATTEMPTS = 8
BASE_RETRY_SECONDS = 30
MAX_RETRY_SECONDS = 60 * 60


@dataclass(frozen=True)
class ConnectorStatus:
    configured: bool
    reachable: bool
    message: str


def status() -> ConnectorStatus:
    if not settings.fasterp_base_url:
        return ConnectorStatus(False, False, "Standalone mode")
    try:
        response = httpx.get(f"{settings.fasterp_base_url}/api/v1/health", timeout=5)
        response.raise_for_status()
        return ConnectorStatus(bool(settings.fasterp_api_token), True, "FastERP API is reachable")
    except httpx.HTTPError:
        return ConnectorStatus(bool(settings.fasterp_api_token), False, "FastERP API is unavailable")


def _order_payload(order: Order) -> dict:
    return {
        "source": "fastshop",
        "source_id": order.id,
        "number": order.number,
        "email": order.email,
        "currency": order.currency,
        "total_minor": order.total_minor,
        "payment_status": order.payment_status,
        "shipping_address": order.shipping_address_json,
        "lines": [
            {
                "sku": line.sku,
                "description": line.product_name,
                "variant": line.variant_name,
                "quantity": line.quantity,
                "unit_price_minor": line.unit_price_minor,
            }
            for line in order.lines
        ],
    }


def retry_delay(attempts: int) -> timedelta:
    seconds = min(MAX_RETRY_SECONDS, BASE_RETRY_SECONDS * (2 ** max(0, attempts - 1)))
    return timedelta(seconds=seconds)


def _deliver_order_confirmed(session: Session, event: OutboxEvent) -> None:
    order = session.scalar(
        select(Order).options(selectinload(Order.lines)).where(
            Order.id == event.aggregate_id,
            Order.tenant_id == event.tenant_id,
        )
    )
    if not order:
        raise RuntimeError("Local order no longer exists")
    if not settings.fasterp_api_token:
        raise RuntimeError("FASTERP_API_TOKEN is not configured")
    headers = {
        "Authorization": f"Bearer {settings.fasterp_api_token}",
        "Idempotency-Key": event.id,
    }
    if settings.fasterp_company_id:
        headers["X-FastERP-Company"] = settings.fasterp_company_id
    response = httpx.post(
        f"{settings.fasterp_base_url}/api/v1/commerce/orders",
        json=_order_payload(order),
        headers=headers,
        timeout=15,
    )
    response.raise_for_status()
    body = response.json()
    order.fasterp_order_id = str(body.get("id", body.get("order_id", "")))


def _dispatch(session: Session, event: OutboxEvent) -> None:
    if event.topic == "order.confirmed":
        _deliver_order_confirmed(session, event)
    # Other durable topics currently have no external consumer. Acknowledging them
    # is explicit so they do not occupy the pending queue forever.


def deliver_pending(
    session: Session, limit: int = 20, *, now: datetime | None = None
) -> tuple[int, int]:
    now = now or datetime.now(UTC)
    events = list(
        session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.status == "pending",
                (OutboxEvent.next_attempt_at.is_(None) | (OutboxEvent.next_attempt_at <= now)),
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    delivered = 0
    failed = 0
    for event in events:
        event.attempts += 1
        try:
            _dispatch(session, event)
            event.status = "delivered"
            event.last_error = ""
            event.next_attempt_at = None
            delivered += 1
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            event.last_error = str(exc)[:500]
            if event.attempts >= MAX_ATTEMPTS:
                event.status = "dead"
                event.next_attempt_at = None
            else:
                event.status = "pending"
                event.next_attempt_at = now + retry_delay(event.attempts)
            failed += 1
    return delivered, failed
