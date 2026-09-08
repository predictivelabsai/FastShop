"""Idempotent FastERP API connector; never writes into FastERP tables directly."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Order, OutboxEvent


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


def deliver_pending(session: Session, limit: int = 20) -> tuple[int, int]:
    events = list(
        session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.topic == "order.confirmed", OutboxEvent.status == "pending")
            .order_by(OutboxEvent.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    delivered = 0
    failed = 0
    for event in events:
        event.attempts += 1
        order = session.scalar(
            select(Order).options(selectinload(Order.lines)).where(Order.id == event.aggregate_id)
        )
        if not order:
            event.status = "failed"
            event.last_error = "Local order no longer exists"
            failed += 1
            continue
        if not settings.fasterp_api_token:
            event.last_error = "FASTERP_API_TOKEN is not configured"
            failed += 1
            continue
        try:
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
            event.status = "delivered"
            event.last_error = ""
            delivered += 1
        except (httpx.HTTPError, ValueError) as exc:
            event.last_error = str(exc)[:500]
            failed += 1
    return delivered, failed
