"""Site-scoped order operations extending the durable commerce commands."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select

from app import catalog_admin, checkout_services, content, customer_services
from app import order_management as orders
from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway
from app.models import (
    Channel,
    CheckoutAttempt,
    InventoryReservation,
    Order,
    OrderComment,
    OrderLine,
    OrderRestock,
    PaymentTransaction,
    ProductVariant,
    RefundCommand,
    ReturnLine,
    ReturnRequest,
    ShipmentEvent,
    ShipmentLine,
    ShopCustomer,
    SiteOrder,
    Stock,
    Warehouse,
)
from app.services import CommerceError

atomic = catalog_admin._atomic


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', value):
        raise CommerceError('Invalid operation request key.')
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _manager(db, site, user_id):
    content.owned_site(db, site.id, user_id, publish=True)
    checkout_services.lock_site(db, site)


def normalise_tags(value):
    values = value.split(',') if isinstance(value, str) else value
    if not isinstance(values, list) or len(values) > 40:
        raise CommerceError('Use at most 40 tags.')
    result = []
    for tag in values:
        if not isinstance(tag, str) or len(tag.strip()) > 80:
            raise CommerceError('Tags must be at most 80 characters.')
        tag = tag.strip()
        if tag and tag.casefold() not in {v.casefold() for v in result}:
            result.append(tag)
    return result


def fulfilment_label(order):
    if order.status in {'cancelled', 'delivered'}:
        return order.status
    fulfilled = sum(line.fulfilled_quantity for line in order.lines)
    remaining = sum(max(0, line.quantity - line.fulfilled_quantity - line.unfulfilled_refunded_quantity)
                    for line in order.lines)
    if order.status == 'fulfilled' or fulfilled and not remaining:
        return 'fulfilled'
    return 'partially_fulfilled' if fulfilled else 'unfulfilled'


def _pattern(value):
    return '%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def list_orders(db, site, filters=None):
    filters = filters or {}
    query = (select(SiteOrder, Order, ShopCustomer).join(Order, Order.id == SiteOrder.order_id)
        .join(ShopCustomer, ShopCustomer.id == SiteOrder.customer_id)
        .where(SiteOrder.site_id == site.id, SiteOrder.tenant_id == site.tenant_id,
               Order.tenant_id == site.tenant_id, ShopCustomer.site_id == site.id,
               ShopCustomer.tenant_id == site.tenant_id))
    if filters.get('ids') is not None:
        query = query.where(SiteOrder.id.in_(filters['ids']))
    view = filters.get('view', 'all') or 'all'
    if view not in {'all', 'unfulfilled', 'unpaid', 'open', 'archived'}:
        raise CommerceError('Choose a valid order view.')
    if view == 'archived':
        query = query.where(SiteOrder.is_archived.is_(True))
    elif view in {'open', 'unfulfilled', 'unpaid'}:
        query = query.where(SiteOrder.is_archived.is_(False), Order.status != 'cancelled')
        if view == 'unfulfilled':
            query = query.where(Order.status.in_(['unfulfilled', 'partially_fulfilled']))
        elif view == 'unpaid':
            query = query.where(Order.payment_status.in_(['pending', 'unpaid', 'failed']))
    needle = str(filters.get('q', '')).strip()[:160]
    if needle:
        pattern = _pattern(needle)
        sku_orders = select(OrderLine.order_id).where(OrderLine.sku.ilike(pattern, escape='\\'))
        query = query.where(or_(Order.number.ilike(pattern, escape='\\'),
            Order.email.ilike(pattern, escape='\\'), ShopCustomer.name.ilike(pattern, escape='\\'),
            Order.id.in_(sku_orders)))
    payment = filters.get('payment', '')
    if payment:
        if payment not in {'pending', 'unpaid', 'paid', 'partially_refunded', 'refunded', 'failed'}:
            raise CommerceError('Choose a valid payment status.')
        query = query.where(Order.payment_status == payment)
    fulfilment = filters.get('fulfilment', '') or filters.get('status_filter', '')
    if fulfilment and fulfilment != 'all':
        if fulfilment not in {*orders.ORDER_STATES, 'partially_fulfilled'}:
            raise CommerceError('Choose a valid fulfilment status.')
        query = query.where(Order.status == fulfilment)
    for key in ('start', 'end'):
        value = filters.get(key, '')
        if value:
            try:
                moment = datetime.combine(date.fromisoformat(str(value)), time.min, UTC)
            except ValueError as exc:
                raise CommerceError('Use an ISO date for order filters.') from exc
            query = query.where(Order.created_at >= moment) if key == 'start' else query.where(
                Order.created_at < moment + timedelta(days=1))
    channel = filters.get('channel', '')
    if channel:
        query = query.join(Channel, Channel.id == Order.channel_id).where(
            Channel.tenant_id == site.tenant_id,
            or_(Channel.id == channel, Channel.slug == channel, Channel.name == channel))
    sort = filters.get('sort', 'date_desc') or 'date_desc'
    sorts = {'date_desc': Order.created_at.desc(), 'date_asc': Order.created_at.asc(),
             'total_desc': Order.total_minor.desc(), 'total_asc': Order.total_minor.asc(),
             'number': Order.number.asc()}
    if sort not in sorts:
        raise CommerceError('Choose a valid order sort.')
    query = query.order_by(sorts[sort], Order.id.desc())
    try:
        per_page = max(1, min(100, int(filters.get('per_page', 25))))
        page = max(1, int(filters.get('page', 1)))
    except (ValueError, TypeError) as exc:
        raise CommerceError('Use valid pagination numbers.') from exc
    tag = str(filters.get('tag', '')).strip().casefold()
    if tag:
        # Exact JSON tag membership is portable across SQLite and PostgreSQL.
        matches = [row for row in db.execute(query)
                   if tag in {str(t).casefold() for t in row[0].tags_json}]
        total = len(matches)
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        rows = matches[(page-1)*per_page:page*per_page]
    else:
        total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        rows = list(db.execute(query.offset((page-1)*per_page).limit(per_page)))
    return dict(rows=rows, total=total, page=page, per_page=per_page, pages=pages)


def _csv_cell(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value


def export_orders(db, site, filters=None):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['Name', 'Created at', 'Email', 'Customer', 'Total', 'Currency',
        'Financial Status', 'Fulfillment Status', 'Lineitem quantity', 'Delivery method', 'Tags'])
    filters = dict(filters or {}, page=1, per_page=100)
    while True:
        result = list_orders(db, site, filters)
        for link, order, customer in result['rows']:
            writer.writerow([_csv_cell(v) for v in [order.number, order.created_at.isoformat(),
                order.email, customer.name, f'{order.total_minor/100:.2f}', order.currency,
                order.payment_status, fulfilment_label(order), sum(x.quantity for x in order.lines),
                'Shipping' if order.shipping_address_json else 'No delivery', ', '.join(link.tags_json)]])
        if result['page'] >= result['pages']:
            break
        filters['page'] += 1
    return stream.getvalue()


@atomic
def add_comment(db, site, user_id, link_id, body, internal=True):
    _manager(db, site, user_id)
    link, _, _ = orders.owned_order(db, site, link_id, lock=True)
    body = str(body).strip()
    if not body or len(body) > 5000:
        raise CommerceError('Write a comment between 1 and 5,000 characters.')
    comment = OrderComment(tenant_id=site.tenant_id, site_id=site.id,
        site_order_id=link.id, author_id=user_id, body=body, internal=bool(internal))
    db.add(comment)
    db.flush()
    return comment


@atomic
def update_order(db, site, user_id, link_id, *, tags=None, note=None, archived=None):
    _manager(db, site, user_id)
    link, _, _ = orders.owned_order(db, site, link_id, lock=True)
    changes = []
    if tags is not None:
        tags = normalise_tags(tags)
        if tags != link.tags_json:
            link.tags_json = tags
            changes.append('Order tags updated.')
    if note is not None:
        note = str(note).strip()
        if len(note) > 5000:
            raise CommerceError('Notes must be at most 5,000 characters.')
        if note != link.note:
            link.note = note
            changes.append('Order note updated.')
    if archived is not None and bool(archived) != link.is_archived:
        link.is_archived = bool(archived)
        changes.append('Order archived.' if archived else 'Order unarchived.')
    if changes:
        add_comment(db, site, user_id, link_id, ' '.join(changes))
    db.flush()
    return link


def _quantities(order, quantities):
    if not isinstance(quantities, dict):
        raise CommerceError('Choose order items and quantities.')
    lines = {line.id: line for line in order.lines}
    result = {}
    for key, quantity in quantities.items():
        if key not in lines or type(quantity) is not int or quantity < 0:
            raise CommerceError('Choose valid order items and quantities.')
        if quantity:
            result[key] = quantity
    return result


def _stocks(db, site, variant_ids):
    return list(db.scalars(select(Stock).join(Warehouse).join(
        ProductVariant, ProductVariant.id == Stock.variant_id).where(
            Warehouse.tenant_id == site.tenant_id, ProductVariant.tenant_id == site.tenant_id,
            Stock.variant_id.in_(variant_ids)).order_by(Stock.id).with_for_update(of=Stock)
        .execution_options(populate_existing=True)))


def _reservations(db, site, order):
    return list(db.scalars(select(InventoryReservation).join(CheckoutAttempt,
        CheckoutAttempt.id == InventoryReservation.attempt_id).where(
            CheckoutAttempt.order_id == order.id, CheckoutAttempt.site_id == site.id,
            CheckoutAttempt.tenant_id == site.tenant_id,
            InventoryReservation.site_id == site.id, InventoryReservation.tenant_id == site.tenant_id,
            InventoryReservation.state == 'committed').execution_options(populate_existing=True)))


@atomic
def fulfil_items(db, site, user_id, link_id, quantities, *, carrier, tracking_number,
                 tracking_url='', notify=True, request_key):
    _manager(db, site, user_id)
    link, order, _ = orders.owned_order(db, site, link_id, lock=True)
    _key(request_key)
    quantities = _quantities(order, quantities)
    carrier, tracking_number, tracking_url = (str(v).strip() for v in
                                               (carrier, tracking_number, tracking_url))
    if not carrier or not tracking_number or len(carrier) > 100 or len(tracking_number) > 100:
        raise CommerceError('Carrier and tracking number are required and must be at most 100 characters.')
    if tracking_url:
        tracking_url = content.safe_url(tracking_url)
        host = (urlsplit(tracking_url).hostname or '').lower()
        if len(tracking_url) > 500 or not any(host == h or host.endswith('.'+h) for h in orders.TRACKING_HOSTS):
            raise CommerceError('Use an HTTPS tracking link from DHL, FedEx, UPS, USPS or Omniva.')
    fingerprint = _hash(dict(quantities=quantities, carrier=carrier,
        tracking_number=tracking_number, tracking_url=tracking_url, notify=bool(notify), link=link.id))
    previous = db.scalar(select(ShipmentEvent).where(ShipmentEvent.site_id == site.id,
        ShipmentEvent.tenant_id == site.tenant_id, ShipmentEvent.request_key == request_key))
    if previous:
        if previous.request_hash != fingerprint:
            raise CommerceError('This fulfilment key was used for different details.')
        return previous
    if order.status not in {'unfulfilled', 'partially_fulfilled'} or not quantities:
        raise CommerceError('Choose unfulfilled items from an open order.')
    lines = {line.id: line for line in order.lines}
    held_unfulfilled = defaultdict(int)
    for command in _refund_holds(db, site, link):
        selected = command.selection_json or {}
        if command.state != 'succeeded' and not selected.get('restock') and not selected.get('return_id'):
            for item in selected.get('lines_json', []):
                held_unfulfilled[item['line_id']] += item['quantity']
    for key, quantity in quantities.items():
        if quantity > lines[key].quantity - lines[key].fulfilled_quantity - lines[key].unfulfilled_refunded_quantity - held_unfulfilled[key]:
            raise CommerceError('Fulfilment quantity exceeds the remaining items.')
    stocks = _stocks(db, site, [lines[k].variant_id for k in quantities if lines[k].variant_id])
    reserves = {r.stock_id: r.quantity for r in _reservations(db, site, order)}
    used = dict(db.execute(select(ShipmentLine.stock_id, func.sum(ShipmentLine.quantity))
        .join(OrderLine, OrderLine.id == ShipmentLine.order_line_id).where(
            OrderLine.order_id == order.id, ShipmentLine.site_id == site.id,
            ShipmentLine.tenant_id == site.tenant_id).group_by(ShipmentLine.stock_id)).all())
    event = ShipmentEvent(tenant_id=site.tenant_id, site_id=site.id, site_order_id=link.id,
        author_id=user_id, status='fulfilled', carrier=carrier, tracking_number=tracking_number,
        tracking_url=tracking_url, request_key=request_key, request_hash=fingerprint)
    db.add(event)
    db.flush()
    for key, quantity in sorted(quantities.items()):
        line = lines[key]
        variant = db.scalar(select(ProductVariant).where(ProductVariant.id == line.variant_id,
            ProductVariant.tenant_id == site.tenant_id)) if line.variant_id else None
        remaining = quantity
        if variant and variant.track_inventory:
            for stock in stocks:
                if stock.variant_id != variant.id or not remaining:
                    continue
                location = db.get(Warehouse, stock.warehouse_id)
                if not location or not location.is_active or not location.fulfils_online_orders:
                    continue
                reserved = max(0, reserves.get(stock.id, 0) - used.get(stock.id, 0))
                available = reserved if reserves else max(0, stock.quantity-stock.allocated)
                take = min(remaining, available)
                if not take:
                    continue
                if reserved:
                    if stock.allocated < take:
                        raise CommerceError('Committed stock changed. Reconcile inventory before fulfilling.')
                    stock.allocated -= take
                catalog_admin.adjust_inventory(db, site, variant.id, stock.warehouse_id, -take,
                    reason='sale', note='Fulfilment '+event.id, user_id=user_id)
                db.add(ShipmentLine(tenant_id=site.tenant_id, site_id=site.id, shipment_id=event.id,
                    order_line_id=line.id, stock_id=stock.id, quantity=take))
                used[stock.id] = used.get(stock.id, 0) + take
                remaining -= take
            if remaining and not variant.continue_selling_when_out_of_stock:
                raise CommerceError('Not enough recorded stock to fulfil these items.')
        if remaining:
            db.add(ShipmentLine(tenant_id=site.tenant_id, site_id=site.id, shipment_id=event.id,
                order_line_id=line.id, quantity=remaining))
        line.fulfilled_quantity += quantity
    order.status = 'fulfilled' if all(
        line.fulfilled_quantity + line.unfulfilled_refunded_quantity >= line.quantity for line in order.lines
    ) else 'partially_fulfilled'
    event.status = order.status
    if notify:
        customer_services.queue_order_mail(db, site, link, shipment=event)
    db.flush()
    return event


def _allocate(total, weights):
    denominator = sum(weights.values())
    if not denominator:
        return {key: 0 for key in weights}
    allocated = {key: total*weight//denominator for key, weight in weights.items()}
    remainder = total-sum(allocated.values())
    for key in sorted(weights, key=lambda k: (-(total*weights[k] % denominator), k))[:remainder]:
        allocated[key] += 1
    return allocated


def _line_paid_totals(order):
    weights = {line.id: line.total_minor for line in order.lines}
    merchandise = max(0, order.subtotal_minor-order.discount_minor)
    amounts = _allocate(merchandise, weights)
    taxes = _allocate(order.tax_minor, amounts)
    return {key: amounts[key]+taxes[key] for key in weights}


def _refund_holds(db, site, link):
    return list(db.scalars(select(RefundCommand).where(RefundCommand.site_id == site.id,
        RefundCommand.tenant_id == site.tenant_id, RefundCommand.site_order_id == link.id,
        RefundCommand.state.in_([*orders.REFUND_STATES_HOLDING_FUNDS, 'succeeded'])).execution_options(populate_existing=True)))


def _owned_return(db, site, link, return_id):
    row = db.scalar(select(ReturnRequest).where(ReturnRequest.id == return_id,
        ReturnRequest.site_id == site.id, ReturnRequest.tenant_id == site.tenant_id,
        ReturnRequest.site_order_id == link.id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise CommerceError('Return not found.')
    return row


def preview_refund(db, site, link_id, quantities, shipping_minor=0, restock=False, return_id=None):
    link, order, _ = orders.owned_order(db, site, link_id, lock=True)
    quantities = _quantities(order, quantities)
    if type(shipping_minor) is not int or shipping_minor < 0:
        raise CommerceError('Use a non-negative shipping refund in minor units.')
    if not quantities and not shipping_minor:
        raise CommerceError('Choose items or shipping to refund.')
    holds = _refund_holds(db, site, link)
    pending = defaultdict(int)
    pending_restock = defaultdict(int)
    selected_amounts = defaultdict(int)
    pending_returns = defaultdict(int)
    for command in holds:
        for item in (command.selection_json or {}).get('lines_json', []):
            selected_amounts[item['line_id']] += item['amount_minor']
        if command.state != 'succeeded':
            for row in (command.selection_json or {}).get('lines_json', []):
                pending[row['line_id']] += row['quantity']
                if (command.selection_json or {}).get('return_id'):
                    pending_returns[row['line_id']] += row['quantity']
                selected = command.selection_json or {}
                received = _owned_return(db, site, link, selected['return_id']) if selected.get('return_id') else None
                if selected.get('restock') and not (received and received.restock and received.received_at):
                    pending_restock[row['line_id']] += row['quantity']
    used_shipping = sum((c.selection_json or {}).get('shipping_minor', 0) for c in holds)
    if shipping_minor > max(0, order.shipping_minor-used_shipping):
        raise CommerceError('Shipping refund exceeds the remaining shipping amount.')
    returned = None
    if return_id:
        returned = _owned_return(db, site, link, return_id)
        if any((command.selection_json or {}).get('return_id') == return_id for command in holds):
            raise CommerceError('This return already has a pending or completed refund.')
        if returned.status not in {'received', 'closed'} or returned.refund_command_id:
            raise CommerceError('Receive this return before refunding it, and refund it only once.')
        eligible = {line.order_line_id: line.quantity for line in returned.lines}
        if quantities != eligible:
            raise CommerceError('Refund the exact received return quantities.')
    reserved_returns = defaultdict(int)
    if not returned:
        for request in db.scalars(select(ReturnRequest).where(ReturnRequest.site_id == site.id,
            ReturnRequest.tenant_id == site.tenant_id, ReturnRequest.site_order_id == link.id,
            ReturnRequest.status != 'declined', ReturnRequest.refund_command_id.is_(None))):
            for item in request.lines:
                reserved_returns[item.order_line_id] += item.quantity
    paid = _line_paid_totals(order)
    selections = []
    for line in sorted(order.lines, key=lambda x: x.id):
        quantity = quantities.get(line.id, 0)
        if not quantity:
            continue
        prior = line.refunded_quantity + pending[line.id]
        reserved = max(0, reserved_returns[line.id]-pending_returns[line.id])
        if quantity > line.quantity-prior-reserved:
            raise CommerceError('Refund quantity exceeds the remaining purchased quantity; use the return refund for reserved items.')
        if restock and not (returned and returned.restock and returned.received_at):
            if quantity > line.fulfilled_quantity-line.restocked_quantity-pending_restock[line.id]:
                raise CommerceError('Only fulfilled items not already restocked can be restocked.')
        # Use recorded command amounts rather than ordinal rounding: a failed
        # earlier pending selection must not strand the final minor unit.
        remaining_value = max(0, paid[line.id]-selected_amounts[line.id])
        amount = remaining_value*quantity//(line.quantity-prior)
        selections.append(dict(line_id=line.id, quantity=quantity, amount_minor=amount))
    amount = sum(row['amount_minor'] for row in selections)+shipping_minor
    _, _, available = orders.refund_totals(db, site, link, order)
    # A gift card is a separate tender. A full item selection may exceed the
    # remaining provider capture; refund that capture and restore the gift ledger
    # after success rather than sending the gross order value to the provider.
    if amount > available and available > 0 and all(
            quantities.get(line.id, 0) == line.quantity-line.refunded_quantity-pending[line.id]
            for line in order.lines):
        from app.commerce_gap_models import GiftCardEntry
        gift_payment = db.scalar(select(GiftCardEntry.id).where(
            GiftCardEntry.site_id == site.id, GiftCardEntry.tenant_id == site.tenant_id,
            GiftCardEntry.order_id == order.id, GiftCardEntry.kind == 'redemption'))
        if gift_payment:
            # Keep the purchased item/shipping allocation intact for quantity and
            # restock accounting. Only the provider command is capped; the other
            # tender is reversed by its own append-only ledger on completion.
            amount = available
    if amount <= 0 or amount > available:
        raise CommerceError('Computed refund exceeds the available captured amount or has no value.')
    return dict(amount_minor=amount, lines_json=selections, shipping_minor=shipping_minor,
                restock=bool(restock), return_id=return_id or None)


def _restock_lines(db, site, user_id, order, quantities, source_key):
    """Restore recorded outbound stock once, locking the entire stock set first."""
    lines = {line.id: line for line in order.lines}
    stocks = _stocks(db, site, [lines[k].variant_id for k in quantities if lines[k].variant_id])
    stock_map = {s.id: s for s in stocks}
    for key, quantity in sorted(quantities.items()):
        line = lines[key]
        if quantity > line.fulfilled_quantity-line.restocked_quantity:
            raise CommerceError('Restock quantities cannot exceed fulfilled items not already restocked.')
        previous = db.scalar(select(OrderRestock.id).where(OrderRestock.site_id == site.id,
            OrderRestock.tenant_id == site.tenant_id, OrderRestock.source_key == source_key,
            OrderRestock.order_line_id == line.id))
        if previous:
            continue
        outbound = list(db.execute(select(ShipmentLine.stock_id, func.sum(ShipmentLine.quantity))
            .where(ShipmentLine.order_line_id == line.id, ShipmentLine.site_id == site.id,
                ShipmentLine.tenant_id == site.tenant_id).group_by(ShipmentLine.stock_id)
            .order_by(ShipmentLine.stock_id)))
        restored = dict(db.execute(select(OrderRestock.stock_id, func.sum(OrderRestock.quantity))
            .where(OrderRestock.order_line_id == line.id, OrderRestock.site_id == site.id,
                   OrderRestock.tenant_id == site.tenant_id).group_by(OrderRestock.stock_id)).all())
        remaining = quantity
        for stock_id, sent in outbound:
            take = min(remaining, max(0, sent-restored.get(stock_id, 0)))
            if not take:
                continue
            stock = stock_map.get(stock_id)
            if stock_id and stock is None:
                raise CommerceError('Original stock location is unavailable for restocking.')
            if stock:
                catalog_admin.adjust_inventory(db, site, stock.variant_id, stock.warehouse_id, take,
                    reason='returned', note=source_key, user_id=user_id)
            db.add(OrderRestock(tenant_id=site.tenant_id, site_id=site.id, order_line_id=line.id,
                               stock_id=stock_id, source_key=source_key, quantity=take))
            remaining -= take
        if remaining:
            # Historic fulfilments did not decrement on-hand stock. Release only
            # the order's own retained reservation, never invent an inbound unit.
            reservations = _reservations(db, site, order)
            for reservation in sorted(reservations, key=lambda r: r.stock_id):
                stock = stock_map.get(reservation.stock_id)
                if not stock or stock.variant_id != line.variant_id or not remaining:
                    continue
                take = min(remaining, reservation.quantity, stock.allocated)
                if take:
                    stock.allocated -= take
                    reservation.quantity -= take
                    if reservation.quantity == 0:
                        db.delete(reservation)
                    catalog_admin.adjust_inventory(db, site, stock.variant_id, stock.warehouse_id, 0,
                        reason='returned', note=source_key+' (legacy reservation release)', user_id=user_id)
                    db.add(OrderRestock(tenant_id=site.tenant_id, site_id=site.id,
                        order_line_id=line.id, stock_id=stock.id, source_key=source_key, quantity=take))
                    remaining -= take
            if remaining:
                if line.variant_id:
                    variant = db.get(ProductVariant, line.variant_id)
                    if variant and variant.track_inventory:
                        raise CommerceError('Historic fulfilment has no recorded stock movement to restock.')
                db.add(OrderRestock(tenant_id=site.tenant_id, site_id=site.id,
                    order_line_id=line.id, source_key=source_key, quantity=remaining))
        line.restocked_quantity += quantity
    db.flush()


def _release_refunded_reservations(db, site, user_id, order, quantities, source):
    lines = {line.id: line for line in order.lines}
    remaining = defaultdict(int)
    for key, quantity in quantities.items():
        if lines[key].variant_id:
            remaining[lines[key].variant_id] += quantity
    stocks = _stocks(db, site, list(remaining))
    reservations = {r.stock_id:r for r in _reservations(db, site, order)}
    used = dict(db.execute(select(ShipmentLine.stock_id, func.sum(ShipmentLine.quantity))
        .join(OrderLine, OrderLine.id == ShipmentLine.order_line_id).where(
            OrderLine.order_id == order.id, ShipmentLine.site_id == site.id,
            ShipmentLine.tenant_id == site.tenant_id).group_by(ShipmentLine.stock_id)).all())
    for stock in stocks:
        reservation = reservations.get(stock.id)
        if reservation is None:
            continue
        take = min(remaining[stock.variant_id], max(0,reservation.quantity-used.get(stock.id,0)))
        if not take:
            continue
        if stock.allocated < take:
            raise CommerceError('Committed stock changed. Reconcile inventory before refunding.')
        stock.allocated -= take
        reservation.quantity -= take
        if reservation.quantity == 0:
            db.delete(reservation)
        remaining[stock.variant_id] -= take
        catalog_admin.adjust_inventory(db, site, stock.variant_id, stock.warehouse_id, 0,
            reason='correction', note=source+' (refunded unfulfilled reservation released)', user_id=user_id)


def apply_refund_selection(db, site, command, order):
    selection = command.selection_json or {}
    if not selection or command.inventory_applied or command.state != 'succeeded':
        return
    quantities = {row['line_id']: row['quantity'] for row in selection['lines_json']}
    returned = None
    if selection.get('return_id'):
        link, _, _ = orders.owned_order(db, site, command.site_order_id, lock=True)
        returned = _owned_return(db, site, link, selection['return_id'])
    if selection['restock'] and not (returned and returned.restock and returned.received_at):
        _restock_lines(db, site, command.actor_id, order, quantities, 'refund:'+command.id)
    released = {}
    for line in order.lines:
        quantity = quantities.get(line.id, 0)
        if quantity and not selection['restock'] and not returned:
            unfulfilled = min(quantity,
                max(0, line.quantity-line.fulfilled_quantity-line.unfulfilled_refunded_quantity))
            released[line.id] = unfulfilled
            line.unfulfilled_refunded_quantity += unfulfilled
        line.refunded_quantity += quantity
    if released:
        _release_refunded_reservations(db, site, command.actor_id, order, released, 'refund:'+command.id)
    if returned:
        returned.refund_command_id = command.id
        returned.status = 'closed'
    command.inventory_applied = True
    db.flush()


def refund_items(site_id, user_id, link_id, quantities, shipping_minor=0, restock=False,
                 reason='', request_key='', return_id=None, *, sessions=SessionLocal,
                 gateway_factory=StripeGateway, expected_amount_minor=None):
    # Existing command persists the exact selection before I/O. Its fingerprint
    # prevents a retry with changed quantities, restocking or return provenance.
    with sessions() as db:
        site = content.owned_site(db, site_id, user_id, publish=True)
        checkout_services.lock_site(db, site)
        existing = db.scalar(select(RefundCommand).where(RefundCommand.site_id == site.id,
            RefundCommand.tenant_id == site.tenant_id, RefundCommand.request_key == request_key))
        if existing:
            selection = existing.selection_json or {}
            original = {row['line_id']: row['quantity'] for row in selection.get('lines_json', [])}
            if (original != {k:v for k,v in quantities.items() if v} or
                selection.get('shipping_minor') != shipping_minor or selection.get('restock') != bool(restock) or
                selection.get('return_id') != (return_id or None)):
                raise CommerceError('This refund request key was used for different details.')
        else:
            selection = preview_refund(db, site, link_id, quantities, shipping_minor, restock, return_id)
        if expected_amount_minor is not None and selection['amount_minor'] != expected_amount_minor:
            raise CommerceError('The refund amount changed. Review the exact amount again.')
        amount = selection['amount_minor']
        db.rollback()
    return orders.refund(site_id, user_id, link_id, amount, reason, request_key,
        sessions=sessions, gateway_factory=gateway_factory, selection=selection)


@atomic
def create_return(db, site, user_id, link_id, quantities, reason, restock=True):
    _manager(db, site, user_id)
    link, order, _ = orders.owned_order(db, site, link_id, lock=True)
    quantities = _quantities(order, quantities)
    reason = str(reason).strip()
    if not quantities or not 1 <= len(reason) <= 500:
        raise CommerceError('Choose return items and a reason of at most 500 characters.')
    pending = defaultdict(int)
    for command in _refund_holds(db, site, link):
        if command.state != 'succeeded' and not (command.selection_json or {}).get('return_id'):
            for item in (command.selection_json or {}).get('lines_json', []):
                pending[item['line_id']] += item['quantity']
    for item in db.scalars(select(ReturnRequest).where(ReturnRequest.site_id == site.id,
        ReturnRequest.tenant_id == site.tenant_id, ReturnRequest.site_order_id == link.id,
        ReturnRequest.status != 'declined')):
        for line in item.lines:
            pending[line.order_line_id] += line.quantity
    returned_refunds = defaultdict(int)
    for item in db.scalars(select(ReturnRequest).where(ReturnRequest.site_id == site.id,
        ReturnRequest.tenant_id == site.tenant_id, ReturnRequest.site_order_id == link.id,
        ReturnRequest.refund_command_id.is_not(None))):
        for line in item.lines:
            returned_refunds[line.order_line_id] += line.quantity
    for line in order.lines:
        direct_refunded = line.refunded_quantity-line.unfulfilled_refunded_quantity-returned_refunds[line.id]
        if quantities.get(line.id, 0) > line.fulfilled_quantity-direct_refunded-pending[line.id]:
            raise CommerceError('Return quantity exceeds fulfilled items available to return.')
    row = ReturnRequest(tenant_id=site.tenant_id, site_id=site.id, site_order_id=link.id,
        author_id=user_id, reason=reason, restock=bool(restock), status='requested')
    db.add(row)
    db.flush()
    for key, quantity in quantities.items():
        row.lines.append(ReturnLine(tenant_id=site.tenant_id, site_id=site.id,
            order_line_id=key, quantity=quantity))
    add_comment(db, site, user_id, link.id, 'Return requested: '+reason)
    db.flush()
    return row


@atomic
def transition_return(db, site, user_id, link_id, return_id, target):
    _manager(db, site, user_id)
    link, order, _ = orders.owned_order(db, site, link_id, lock=True)
    row = _owned_return(db, site, link, return_id)
    if target == row.status:
        return row
    transitions = {'requested': {'approved', 'declined'}, 'approved': {'received', 'declined'},
                   'received': {'closed'}, 'closed': set(), 'declined': set()}
    if target not in transitions[row.status]:
        raise CommerceError('This return cannot move to the requested state.')
    if target == 'received':
        if row.restock:
            _restock_lines(db, site, user_id, order,
                {line.order_line_id:line.quantity for line in row.lines}, 'return:'+row.id)
        row.received_at = datetime.now(UTC)
    row.status = target
    add_comment(db, site, user_id, link_id, 'Return '+target+': '+row.reason)
    db.flush()
    return row


@atomic
def bulk_orders(db, site, user_id, ids, action, value=''):
    _manager(db, site, user_id)
    ids = list(dict.fromkeys(ids))
    if not ids or len(ids) > 100 or action not in {'archive', 'unarchive', 'tag', 'fulfil'}:
        raise CommerceError('Choose up to 100 orders and a valid bulk action.')
    rows = [orders.owned_order(db, site, key, lock=True) for key in sorted(ids)]
    tracking = None
    if action == 'fulfil':
        try:
            tracking = json.loads(value)
        except (ValueError, TypeError) as exc:
            raise CommerceError('Supply carrier and tracking details to bulk fulfil orders.') from exc
        if not isinstance(tracking, dict) or not tracking.get('carrier') or not tracking.get('tracking_number'):
            raise CommerceError('Supply carrier and tracking details to bulk fulfil orders.')
        _key(tracking.get('request_key', ''))
        if any(order.status not in {'unfulfilled', 'partially_fulfilled'} for _,order,_ in rows):
            raise CommerceError('Only open unfulfilled orders can be bulk fulfilled.')
    tags = normalise_tags(value) if action == 'tag' else None
    for link, order, _ in rows:
        if action in {'archive', 'unarchive'}:
            update_order(db, site, user_id, link.id, archived=action == 'archive')
        elif action == 'tag':
            update_order(db, site, user_id, link.id, tags=normalise_tags(link.tags_json+tags))
        else:
            fulfil_items(db, site, user_id, link.id,
                {line.id: max(0, line.quantity-line.fulfilled_quantity-line.unfulfilled_refunded_quantity) for line in order.lines},
                carrier=tracking['carrier'], tracking_number=tracking['tracking_number'],
                tracking_url=tracking.get('tracking_url', ''), notify=bool(tracking.get('notify', True)),
                request_key=_hash([tracking['request_key'], link.id]))
    return len(rows)


def detail(db, site, link_id):
    link, order, customer = orders.owned_order(db, site, link_id)
    _, _, available = orders.refund_totals(db, site, link, order)
    paid_transactions = list(db.scalars(select(PaymentTransaction).where(
        PaymentTransaction.order_id == order.id, PaymentTransaction.status == 'succeeded',
        PaymentTransaction.currency == order.currency)))
    captured = sum(p.amount_minor for p in paid_transactions if p.kind == 'charge')
    refunded = sum(p.amount_minor for p in paid_transactions if p.kind == 'refund')
    payment_provider = 'manual' if any(p.provider == 'manual:'+site.id for p in paid_transactions) else 'stripe'
    siblings = list(db.scalars(select(SiteOrder.id).join(Order, Order.id == SiteOrder.order_id)
        .where(SiteOrder.site_id == site.id, SiteOrder.tenant_id == site.tenant_id,
               Order.tenant_id == site.tenant_id).order_by(Order.created_at.desc(), Order.id.desc())))
    index = siblings.index(link.id)
    returns = list(db.scalars(select(ReturnRequest).where(ReturnRequest.site_order_id == link.id,
        ReturnRequest.site_id == site.id, ReturnRequest.tenant_id == site.tenant_id)
        .order_by(ReturnRequest.created_at.desc())))
    return dict(link=link, order=order, customer=customer, timeline=orders.timeline(db, site, link, order),
        returns=returns, captured=captured, refunded=refunded, available=available, payment_provider=payment_provider,
        gift_only_payment=any(p.provider == 'gift_card:'+site.id for p in paid_transactions)
            and sum(p.amount_minor * (-1 if p.kind == 'refund' else 1) for p in paid_transactions
                if p.provider != 'gift_card:'+site.id and p.kind in {'charge', 'refund'}) <= 0,
        prev_id=siblings[index-1] if index else None,
        next_id=siblings[index+1] if index+1 < len(siblings) else None,
        fulfilment_label=fulfilment_label(order))
