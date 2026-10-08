"""Tenant-scoped merchant order operations and committed revenue reporting."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, select

from app import commerce, content
from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway, make_gateway
from app.models import (
    Channel,
    CheckoutAttempt,
    Membership,
    Order,
    OutboxEvent,
    PaymentTransaction,
    RefundCommand,
    ShipmentEvent,
    ShopCustomer,
    Site,
    SiteOrder,
)
from app.services import CommerceError

ORDER_STATES = ("unfulfilled", "fulfilled", "delivered", "cancelled")
TRANSITIONS = {
    "unfulfilled": ("fulfilled", "cancelled"),
    "fulfilled": ("delivered", "cancelled"),
    "delivered": (),
    "cancelled": (),
}
TRACKING_HOSTS = {"dhl.com", "fedex.com", "ups.com", "usps.com", "omniva.ee", "omniva.eu"}
REFUND_STATES_HOLDING_FUNDS = {"creating", "pending", "reconciliation_required"}
REPORT_PAYMENT_STATES = {"paid", "partially_refunded", "refunded"}
MAX_REPORT_DAYS = 366


@dataclass(frozen=True)
class RevenueReport:
    site_id: str
    start_date: date
    end_date: date
    currency: str
    gross_sales_minor: int
    refunds_minor: int
    net_revenue_minor: int
    order_count: int
    average_order_value_minor: int
    excluded_currency_orders: int
    excluded_currency_refunds: int


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def report_dates(start: str = "", end: str = "", *, today: date | None = None) -> tuple[date, date]:
    today = today or datetime.now(UTC).date()
    try:
        end_date = date.fromisoformat(end) if end else today
        start_date = date.fromisoformat(start) if start else end_date - timedelta(days=29)
    except ValueError as exc:
        raise CommerceError("Use valid report dates in YYYY-MM-DD format.") from exc
    if end_date < start_date:
        raise CommerceError("Report end date must be on or after the start date.")
    if (end_date - start_date).days + 1 > MAX_REPORT_DAYS:
        raise CommerceError(f"Revenue reports are limited to {MAX_REPORT_DAYS} days.")
    return start_date, end_date


def revenue_report(db, site: Site, start: date, end: date) -> RevenueReport:
    if end < start or (end - start).days + 1 > MAX_REPORT_DAYS:
        raise CommerceError(f"Choose a report range of 1 to {MAX_REPORT_DAYS} days.")
    channel = db.scalar(select(Channel).where(
        Channel.id == site.channel_id,
        Channel.tenant_id == site.tenant_id,
    ))
    if not channel:
        raise CommerceError("The site's sales channel is unavailable.")
    currency = channel.currency.upper()
    start_at = datetime.combine(start, time.min, tzinfo=UTC)
    end_at = datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)
    order_scope = (
        select(Order)
        .join(SiteOrder, SiteOrder.order_id == Order.id)
        .where(
            SiteOrder.site_id == site.id,
            SiteOrder.tenant_id == site.tenant_id,
            Order.tenant_id == site.tenant_id,
            Order.payment_status.in_(REPORT_PAYMENT_STATES),
            Order.created_at >= start_at,
            Order.created_at < end_at,
        )
    )
    orders = list(db.scalars(order_scope))
    included = [order for order in orders if order.currency.upper() == currency]
    gross = sum(order.total_minor for order in included)
    order_count = len(included)

    refund_scope = (
        select(PaymentTransaction)
        .join(SiteOrder, SiteOrder.order_id == PaymentTransaction.order_id)
        .join(Order, Order.id == SiteOrder.order_id)
        .where(
            SiteOrder.site_id == site.id,
            SiteOrder.tenant_id == site.tenant_id,
            Order.tenant_id == site.tenant_id,
            PaymentTransaction.kind == "refund",
            PaymentTransaction.status == "succeeded",
            PaymentTransaction.created_at >= start_at,
            PaymentTransaction.created_at < end_at,
        )
    )
    refund_rows = list(db.scalars(refund_scope))
    refunds = sum(row.amount_minor for row in refund_rows if row.currency.upper() == currency)
    average = (gross + order_count // 2) // order_count if order_count else 0
    return RevenueReport(
        site_id=site.id,
        start_date=start,
        end_date=end,
        currency=currency,
        gross_sales_minor=gross,
        refunds_minor=refunds,
        net_revenue_minor=gross - refunds,
        order_count=order_count,
        average_order_value_minor=average,
        excluded_currency_orders=len(orders) - order_count,
        excluded_currency_refunds=sum(row.currency.upper() != currency for row in refund_rows),
    )


def orders_for(db, site: Site, status: str = "all"):
    if status not in {"all", *ORDER_STATES}:
        raise CommerceError("Choose a valid fulfillment status.")
    query = (
        select(SiteOrder, Order, ShopCustomer)
        .join(Order, Order.id == SiteOrder.order_id)
        .join(ShopCustomer, ShopCustomer.id == SiteOrder.customer_id)
        .where(
            SiteOrder.site_id == site.id,
            SiteOrder.tenant_id == site.tenant_id,
            Order.tenant_id == site.tenant_id,
            ShopCustomer.site_id == site.id,
            ShopCustomer.tenant_id == site.tenant_id,
        )
    )
    if status != "all":
        query = query.where(Order.status == status)
    return list(db.execute(query.order_by(Order.created_at.desc())))


def owned_order(db, site: Site, site_order_id: str, *, lock: bool = False):
    query = (
        select(SiteOrder, Order, ShopCustomer)
        .join(Order, Order.id == SiteOrder.order_id)
        .join(ShopCustomer, ShopCustomer.id == SiteOrder.customer_id)
        .where(
            SiteOrder.id == site_order_id,
            SiteOrder.site_id == site.id,
            SiteOrder.tenant_id == site.tenant_id,
            Order.tenant_id == site.tenant_id,
            ShopCustomer.site_id == site.id,
            ShopCustomer.tenant_id == site.tenant_id,
        )
    )
    if lock:
        query = query.with_for_update(of=(SiteOrder, Order)).execution_options(populate_existing=True)
    row = db.execute(query).first()
    if not row:
        raise CommerceError("Order not found.")
    return row


def transition_fulfillment(db, site: Site, user_id: str, site_order_id: str, target: str,
                           expected_status: str, *, carrier: str = "", tracking_number: str = "",
                           tracking_url: str = "", note: str = "") -> ShipmentEvent:
    content.owned_site(db, site.id, user_id, publish=True)
    link, order, _ = owned_order(db, site, site_order_id, lock=True)
    if order.status != expected_status:
        raise CommerceError("The order changed. Reload before updating fulfillment.")
    if target not in TRANSITIONS.get(order.status, set()):
        raise CommerceError(f"An order cannot move from {order.status} to {target}.")
    carrier, tracking_number, note = carrier.strip(), tracking_number.strip(), note.strip()
    if target == "fulfilled" and (not carrier or not tracking_number):
        raise CommerceError("Carrier and tracking number are required when fulfilling an order.")
    if target == "cancelled" and not note:
        raise CommerceError("Record a cancellation reason. Cancelling fulfillment does not issue a refund.")
    if len(carrier) > 100 or len(tracking_number) > 100 or len(note) > 500:
        raise CommerceError("Fulfillment details are too long.")
    tracking_url = content.safe_url(tracking_url.strip()) if tracking_url.strip() else ""
    host = (urlsplit(tracking_url).hostname or "").lower()
    if tracking_url and not any(host == allowed or host.endswith("." + allowed) for allowed in TRACKING_HOSTS):
        raise CommerceError("Use an HTTPS tracking link from DHL, FedEx, UPS, USPS or Omniva.")
    event = ShipmentEvent(
        tenant_id=site.tenant_id,
        site_id=site.id,
        site_order_id=link.id,
        author_id=user_id,
        carrier=carrier,
        tracking_number=tracking_number,
        tracking_url=tracking_url,
        status=target,
        note=note,
    )
    order.status = target
    db.add(event)
    db.flush()
    from app.customer_services import queue_order_mail

    queue_order_mail(db, site, link, shipment=event)
    return event


def refund_totals(db, site: Site, link: SiteOrder, order: Order) -> tuple[int, int, int]:
    provider = "stripe:" + site.id
    captured = db.scalar(select(func.coalesce(func.sum(PaymentTransaction.amount_minor), 0)).where(
        PaymentTransaction.order_id == order.id,
        PaymentTransaction.provider == provider,
        PaymentTransaction.kind == "charge",
        PaymentTransaction.status == "succeeded",
        PaymentTransaction.currency == order.currency,
    )) or 0
    refunded = db.scalar(select(func.coalesce(func.sum(PaymentTransaction.amount_minor), 0)).where(
        PaymentTransaction.order_id == order.id,
        PaymentTransaction.provider == provider,
        PaymentTransaction.kind == "refund",
        PaymentTransaction.status == "succeeded",
        PaymentTransaction.currency == order.currency,
    )) or 0
    reserved = db.scalar(select(func.coalesce(func.sum(RefundCommand.amount_minor), 0)).where(
        RefundCommand.site_order_id == link.id,
        RefundCommand.site_id == site.id,
        RefundCommand.tenant_id == site.tenant_id,
        RefundCommand.currency == order.currency,
        RefundCommand.state.in_(REFUND_STATES_HOLDING_FUNDS),
    )) or 0
    return int(captured), int(refunded), max(0, int(captured) - int(refunded) - int(reserved))


def _manager_site(db, site_id: str, user_id: str) -> Site:
    site = db.scalar(select(Site).join(Membership, Membership.tenant_id == Site.tenant_id).where(
        Site.id == site_id,
        Membership.user_id == user_id,
        Membership.role.in_(["admin", "merchant"]),
    ))
    if not site:
        raise CommerceError("Site not found or access denied.")
    return site


def _refund_fingerprint(site_order_id: str, amount_minor: int, reason: str) -> str:
    payload = {"site_order_id": site_order_id, "amount_minor": amount_minor, "reason": reason}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _refund_result(result: dict) -> dict:
    return {key: result.get(key) for key in (
        "id", "payment_intent", "amount", "currency", "status", "livemode"
    )}


def refund(site_id: str, user_id: str, site_order_id: str, amount_minor: int, reason: str,
           request_key: str, *, sessions=SessionLocal, gateway_factory=StripeGateway) -> RefundCommand:
    """Persist an exact refund before Stripe I/O and replay it with one stable key."""
    reason = reason.strip()
    if type(amount_minor) is not int or amount_minor <= 0:
        raise CommerceError("Enter a positive refund amount in minor currency units.")
    if not 10 <= len(reason) <= 500:
        raise CommerceError("Provide a refund reason between 10 and 500 characters.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", request_key):
        raise CommerceError("Invalid refund request key.")
    fingerprint = _refund_fingerprint(site_order_id, amount_minor, reason)
    with sessions() as db:
        site = _manager_site(db, site_id, user_id)
        link, order, _ = owned_order(db, site, site_order_id, lock=True)
        mode = commerce.payment_mode(db, site)
        command = db.scalar(select(RefundCommand).where(
            RefundCommand.site_id == site.id,
            RefundCommand.tenant_id == site.tenant_id,
            RefundCommand.request_key == request_key,
        ).with_for_update().execution_options(populate_existing=True))
        if command:
            if command.request_hash != fingerprint:
                raise CommerceError("This refund request key was already used for different details.")
            if command.state in {"succeeded", "failed"}:
                return command
            if (command.command_json.get("metadata") or {}).get("payment_mode") != mode:
                raise CommerceError("This refund command was created for a different payment mode.")
        else:
            if order.payment_status not in {"paid", "partially_refunded"}:
                raise CommerceError("Only a captured Stripe payment can be refunded.")
            captured, refunded, available = refund_totals(db, site, link, order)
            if captured <= 0:
                raise CommerceError("This order has no captured Stripe payment to refund.")
            if amount_minor > available:
                raise CommerceError(
                    f"Refund exceeds the available captured amount ({available} {order.currency} minor units)."
                )
            charge = db.scalar(select(PaymentTransaction).where(
                PaymentTransaction.order_id == order.id,
                PaymentTransaction.provider == "stripe:" + site.id,
                PaymentTransaction.kind == "charge",
                PaymentTransaction.status == "succeeded",
                PaymentTransaction.currency == order.currency,
            ).order_by(PaymentTransaction.created_at.desc()))
            if not charge or not re.fullmatch(r"pi_[A-Za-z0-9_]+", charge.external_id):
                raise CommerceError("This payment provider state does not support an automatic refund.")
            command = RefundCommand(
                tenant_id=site.tenant_id,
                site_id=site.id,
                site_order_id=link.id,
                actor_id=user_id,
                request_key=request_key,
                request_hash=fingerprint,
                amount_minor=amount_minor,
                currency=order.currency,
                reason=reason,
                state="creating",
                provider_payment_id=charge.external_id,
                command_json={},
            )
            db.add(command)
            db.flush()
            command.command_json = {
                "payment_intent": charge.external_id,
                "amount": amount_minor,
                "reason": "requested_by_customer",
                "metadata": {"site_id": site.id, "site_order_id": link.id,
                             "refund_command_id": command.id, "payment_mode": mode},
            }
        gateway = make_gateway(gateway_factory, site, db)
        command.state = "creating"
        command.attempts += 1
        command_id, payload = command.id, dict(command.command_json)
        db.commit()

        result = gateway.create_refund(command_id, payload)
        command = db.scalar(select(RefundCommand).where(
            RefundCommand.id == command_id,
            RefundCommand.site_id == site.id,
            RefundCommand.tenant_id == site.tenant_id,
        ).with_for_update().execution_options(populate_existing=True))
        link, order, _ = owned_order(db, site, site_order_id, lock=True)
        clean = _refund_result(result)
        exact = (
            isinstance(result.get("id"), str)
            and re.fullmatch(r"re_[A-Za-z0-9_]+", result["id"])
            and result.get("payment_intent") == command.provider_payment_id
            and type(result.get("amount")) is int
            and result["amount"] == command.amount_minor
            and result.get("currency") == command.currency.lower()
            and result.get("livemode") is (mode == "live")
            and result.get("status") in {"succeeded", "pending", "failed"}
        )
        command.result_json = clean
        if not exact:
            command.state = "reconciliation_required"
            db.commit()
            raise CommerceError("Stripe returned a refund result that does not exactly match the command; reconciliation is required.")
        command.provider_refund_id = result["id"]
        command.state = result["status"]
        if command.state == "succeeded":
            transaction = db.scalar(select(PaymentTransaction).where(
                PaymentTransaction.provider == "stripe:" + site.id,
                PaymentTransaction.external_id == command.provider_refund_id,
            ))
            if transaction and transaction.order_id != order.id:
                command.state = "reconciliation_required"
                db.commit()
                raise CommerceError("Stripe returned a refund already linked to another order.")
            if not transaction:
                db.add(PaymentTransaction(
                    order_id=order.id,
                    provider="stripe:" + site.id,
                    external_id=command.provider_refund_id,
                    kind="refund",
                    status="succeeded",
                    currency=command.currency,
                    amount_minor=command.amount_minor,
                ))
                db.flush()
                db.add(OutboxEvent(
                    tenant_id=site.tenant_id,
                    topic="order.refunded",
                    aggregate_id=order.id,
                    payload_json={
                        "order_id": order.id,
                        "site_id": site.id,
                        "refund_command_id": command.id,
                        "amount_minor": command.amount_minor,
                        "currency": command.currency,
                    },
                ))
            completed = db.scalar(select(func.coalesce(func.sum(PaymentTransaction.amount_minor), 0)).where(
                PaymentTransaction.order_id == order.id,
                PaymentTransaction.provider == "stripe:" + site.id,
                PaymentTransaction.kind == "refund",
                PaymentTransaction.status == "succeeded",
                PaymentTransaction.currency == order.currency,
            )) or 0
            captured = db.scalar(select(func.coalesce(func.sum(PaymentTransaction.amount_minor), 0)).where(
                PaymentTransaction.order_id == order.id,
                PaymentTransaction.provider == "stripe:" + site.id,
                PaymentTransaction.kind == "charge",
                PaymentTransaction.status == "succeeded",
                PaymentTransaction.currency == order.currency,
            )) or 0
            order.payment_status = "refunded" if completed >= captured else "partially_refunded"
        db.commit()
        return command


def timeline(db, site: Site, link: SiteOrder, order: Order) -> list[dict]:
    events = [{"at": order.created_at, "kind": "order", "title": "Order confirmed",
               "detail": f"Payment state: {order.payment_status}."}]
    attempts = list(db.scalars(select(CheckoutAttempt).where(
        CheckoutAttempt.order_id == order.id,
        CheckoutAttempt.site_id == site.id,
        CheckoutAttempt.tenant_id == site.tenant_id,
    )))
    events.extend({"at": row.updated_at, "kind": "reconciliation", "title": "Checkout reconciliation",
                   "detail": f"Checkout state: {row.state}."} for row in attempts)
    payments = list(db.scalars(select(PaymentTransaction).where(
        PaymentTransaction.order_id == order.id,
    )))
    events.extend({"at": row.created_at, "kind": "payment", "title": row.kind.replace("_", " ").title(),
                   "detail": f"{row.status.replace('_', ' ').title()} · {row.amount_minor} {row.currency} minor units."}
                  for row in payments)
    shipments = list(db.scalars(select(ShipmentEvent).where(
        ShipmentEvent.site_order_id == link.id,
        ShipmentEvent.site_id == site.id,
        ShipmentEvent.tenant_id == site.tenant_id,
    )))
    events.extend({"at": row.created_at, "kind": "fulfillment", "title": row.status.replace("_", " ").title(),
                   "detail": " · ".join(value for value in (row.carrier, row.tracking_number, row.note) if value),
                   "tracking_url": row.tracking_url} for row in shipments)
    refunds = list(db.scalars(select(RefundCommand).where(
        RefundCommand.site_order_id == link.id,
        RefundCommand.site_id == site.id,
        RefundCommand.tenant_id == site.tenant_id,
    )))
    events.extend({"at": row.updated_at, "kind": "refund", "title": "Refund " + row.state.replace("_", " "),
                   "detail": f"{row.amount_minor} {row.currency} minor units · {row.reason}", "command": row}
                  for row in refunds)
    outbox = list(db.scalars(select(OutboxEvent).where(
        OutboxEvent.tenant_id == site.tenant_id,
        OutboxEvent.aggregate_id == order.id,
    )))
    events.extend({"at": row.updated_at, "kind": "outbox", "title": row.topic.replace(".", " ").title(),
                   "detail": f"Delivery state: {row.status}; attempts: {row.attempts}."} for row in outbox)
    return sorted(events, key=lambda item: _utc(item["at"]), reverse=True)
