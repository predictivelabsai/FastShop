"""Transactional order edits; inventory allocations and a manual settlement ledger."""
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import func, select

from app import catalog_admin, checkout_services, content, customer_services, order_admin
from app import order_management as orders
from app.commerce_gap_models import OrderEdit
from app.models import (
    CheckoutAttempt,
    InventoryReservation,
    OrderComment,
    OrderLine,
    PaymentTransaction,
    ProductVariant,
    ShipmentLine,
    VariantChannelListing,
)
from app.services import CommerceError


def version(order):
    return order_admin._hash({'total': order.total_minor, 'updated': str(order.updated_at),
        'lines': sorted((line.id, line.quantity, line.total_minor) for line in order.lines)})


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum or value > 100_000_000:
        raise CommerceError(f'Enter a valid {label} in whole minor units or quantities.')
    return value


def preview(db, site, order, selections):
    if not isinstance(selections, list) or not selections or len(selections) > 200:
        raise CommerceError('Choose between 1 and 200 order lines.')
    existing = {line.id: line for line in order.lines}
    seen, result = set(), []
    for selection in selections:
        if not isinstance(selection, dict):
            raise CommerceError('Choose valid order lines.')
        line_id = selection.get('line_id')
        if line_id:
            if line_id not in existing or line_id in seen:
                raise CommerceError('Order item not found or repeated.')
            seen.add(line_id)
            old = existing[line_id]
            quantity = _integer(selection.get('quantity'), 'quantity')
            if quantity < old.fulfilled_quantity + old.unfulfilled_refunded_quantity:
                raise CommerceError('Cannot remove fulfilled or refunded items.')
            variant_id, name, sku, variant_name, price = (
                old.variant_id, old.product_name, old.sku, old.variant_name, old.unit_price_minor)
            default_discount = max(0, old.unit_price_minor * old.quantity - old.total_minor)
        else:
            quantity = _integer(selection.get('quantity'), 'quantity', 1)
            variant_id = selection.get('variant_id') or None
            if variant_id:
                variant = db.scalar(select(ProductVariant).where(ProductVariant.id == variant_id,
                    ProductVariant.tenant_id == site.tenant_id, ProductVariant.is_active.is_(True)))
                if not variant:
                    raise CommerceError('Variant not found.')
                product = catalog_admin.get_product(db, site, variant.product_id)
                listing = db.scalar(select(VariantChannelListing).where(
                    VariantChannelListing.variant_id == variant.id,
                    VariantChannelListing.channel_id == site.channel_id,
                    VariantChannelListing.currency == order.currency))
                if not listing:
                    raise CommerceError('Variant has no price in this store.')
                name, sku, variant_name, price = product.name, variant.sku, variant.name, listing.price_minor
            else:
                name = str(selection.get('name', '')).strip()
                if not name or len(name) > 220:
                    raise CommerceError('Enter a custom item name of up to 220 characters.')
                sku, variant_name = '', 'Custom item'
                price = _integer(selection.get('price_minor'), 'custom item price')
            default_discount = 0
        discount = _integer(selection.get('discount_minor', min(default_discount, price*quantity)),
                            'line discount')
        if not quantity:
            discount = 0
        if discount > price*quantity:
            raise CommerceError('A line discount cannot exceed its item total.')
        result.append(dict(line_id=line_id, variant_id=variant_id, name=name, sku=sku,
            variant_name=variant_name, quantity=quantity, price_minor=price,
            discount_minor=discount, total_minor=price*quantity-discount))
    for identifier, line in existing.items():
        if identifier not in seen and (line.fulfilled_quantity or line.refunded_quantity):
            raise CommerceError('Keep fulfilled or refunded items in the edit.')
    return result


def _allocations(db, site, order, changes):
    variants = {v.id: v for v in db.scalars(select(ProductVariant).where(
        ProductVariant.id.in_(changes), ProductVariant.tenant_id == site.tenant_id))}
    tracked = {key: value for key, value in changes.items() if value and variants[key].track_inventory}
    if not tracked:
        return
    attempt = db.scalar(select(CheckoutAttempt).where(CheckoutAttempt.order_id == order.id,
        CheckoutAttempt.site_id == site.id, CheckoutAttempt.tenant_id == site.tenant_id,
        CheckoutAttempt.state == 'paid'))
    if not attempt:
        raise CommerceError('This order has no inventory reservation record. Reconcile it before editing.')
    stocks = order_admin._stocks(db, site, tracked)
    reservations = {r.stock_id: r for r in db.scalars(select(InventoryReservation).where(
        InventoryReservation.attempt_id == attempt.id, InventoryReservation.site_id == site.id,
        InventoryReservation.tenant_id == site.tenant_id))}
    used = dict(db.execute(select(ShipmentLine.stock_id, func.sum(ShipmentLine.quantity))
        .join(OrderLine, OrderLine.id == ShipmentLine.order_line_id).where(
            OrderLine.order_id == order.id, ShipmentLine.site_id == site.id,
            ShipmentLine.tenant_id == site.tenant_id).group_by(ShipmentLine.stock_id)).all())
    for variant_id, delta in sorted(tracked.items()):
        remaining = abs(delta)
        for stock in stocks:
            if stock.variant_id != variant_id or not remaining:
                continue
            reserve = reservations.get(stock.id)
            if delta < 0:
                available = min(stock.allocated, max(0, reserve.quantity-used.get(stock.id, 0))) if reserve and reserve.state == 'committed' else 0
                take = min(remaining, available)
                if take:
                    stock.allocated -= take
                    reserve.quantity -= take
                    if not reserve.quantity:
                        db.delete(reserve)
                        del reservations[stock.id]
            else:
                # Locations disabled for online fulfilment cannot receive new commitments.
                from app.models import Warehouse
                warehouse = db.get(Warehouse, stock.warehouse_id)
                if not getattr(warehouse, 'is_active', True) or not getattr(warehouse, 'fulfils_online_orders', True):
                    continue
                take = min(remaining, max(0, stock.quantity-stock.allocated))
                if take:
                    stock.allocated += take
                    if reserve:
                        reserve.quantity += take
                        reserve.state = 'committed'
                    else:
                        reserve = InventoryReservation(tenant_id=site.tenant_id, site_id=site.id,
                            attempt_id=attempt.id, stock_id=stock.id, quantity=take, state='committed')
                        reservations[stock.id] = reserve
                        db.add(reserve)
            remaining -= take
        if remaining and (delta < 0 or not variants[variant_id].continue_selling_when_out_of_stock):
            raise CommerceError('Not enough available or committed stock for this edit.')


@catalog_admin._atomic
def commit_edit(db, site, actor_id, link_id, selections, reason, request_key, expected_version):
    content.owned_site(db, site.id, actor_id, publish=True)
    checkout_services.lock_site(db, site)
    order_admin._key(request_key)
    link, order, _ = orders.owned_order(db, site, link_id, lock=True)
    reason = str(reason).strip()
    if not reason or len(reason) > 500:
        raise CommerceError('Record an edit reason of up to 500 characters.')
    fingerprint = order_admin._hash(dict(order=link_id, selections=selections, reason=reason,
                                         version=expected_version))
    previous = db.scalar(select(OrderEdit).where(OrderEdit.site_id == site.id,
        OrderEdit.tenant_id == site.tenant_id, OrderEdit.request_key == request_key))
    if previous:
        if previous.request_hash != fingerprint:
            raise CommerceError('This edit session was used for different details.')
        return previous
    if order.status == 'cancelled' or order.payment_status == 'refunded':
        raise CommerceError('Cancelled or fully refunded orders cannot be edited.')
    if version(order) != expected_version:
        raise CommerceError('This order changed. Reload before editing.')
    if any(command.state != 'succeeded' for command in order_admin._refund_holds(db, site, link)):
        raise CommerceError('Resolve pending refunds before editing this order.')
    rows = preview(db, site, order, selections)
    before = order.total_minor
    old_lines = {line.id: line for line in order.lines}
    delta = Counter()
    for line in old_lines.values():
        if line.variant_id:
            delta[line.variant_id] -= line.quantity
    for row in rows:
        if row['variant_id']:
            delta[row['variant_id']] += row['quantity']
    _allocations(db, site, order, delta)
    before_snapshot = [dict(id=x.id, quantity=x.quantity, total_minor=x.total_minor) for x in old_lines.values()]
    keep = set()
    for row in rows:
        if not row['quantity']:
            continue
        line = old_lines.get(row['line_id'])
        if not line:
            line = OrderLine(order_id=order.id, variant_id=row['variant_id'], sku=row['sku'],
                product_name=row['name'], variant_name=row['variant_name'],
                unit_price_minor=row['price_minor'])
            order.lines.append(line)
        else:
            keep.add(line.id)
        line.quantity, line.total_minor = row['quantity'], row['total_minor']
    for identifier, line in old_lines.items():
        if identifier not in keep:
            order.lines.remove(line)
    order.subtotal_minor = sum(row['price_minor']*row['quantity'] for row in rows)
    order.discount_minor = sum(row['discount_minor'] for row in rows)
    order.total_minor = order.subtotal_minor-order.discount_minor+order.shipping_minor+order.tax_minor
    remaining = any(line.quantity > (line.fulfilled_quantity or 0)
                    + (line.unfulfilled_refunded_quantity or 0) for line in order.lines)
    if remaining:
        order.status = 'partially_fulfilled' if any(
            line.fulfilled_quantity for line in order.lines) else 'unfulfilled'
    elif order.status != 'delivered':
        order.status = 'fulfilled'
    # Payment difference includes previous unsettled edits and captured refunds.
    payments = list(db.scalars(select(PaymentTransaction).where(PaymentTransaction.order_id == order.id,
        PaymentTransaction.currency == order.currency, PaymentTransaction.status == 'succeeded')))
    paid = sum(p.amount_minor * (-1 if p.kind == 'refund' else 1) for p in payments if p.kind in {'charge', 'refund'})
    edit = OrderEdit(created_at=datetime.now(UTC), tenant_id=site.tenant_id, site_id=site.id, site_order_id=link.id,
        actor_id=actor_id, request_key=request_key, request_hash=fingerprint, reason=reason,
        before_minor=before, after_minor=order.total_minor, difference_minor=order.total_minor-paid,
        settlement='settled' if order.total_minor == paid else 'pending',
        snapshot_json={'before': before_snapshot, 'after': rows})
    if order.total_minor > paid:
        order.payment_status = 'unpaid'
    elif order.payment_status == 'unpaid':
        order.payment_status = 'paid'
    db.add(edit)
    db.add(OrderComment(tenant_id=site.tenant_id, site_id=site.id, site_order_id=link.id,
        author_id=actor_id, internal=True,
        body=f'Order edited: {reason} Total changed from {before} to {order.total_minor} {order.currency} minor units.'))
    db.flush()
    return edit


@catalog_admin._atomic
def settle(db, site, actor_id, link_id, edit_id, action):
    content.owned_site(db, site.id, actor_id, publish=True)
    checkout_services.lock_site(db, site)
    link, order, customer = orders.owned_order(db, site, link_id, lock=True)
    edit = db.scalar(select(OrderEdit).where(OrderEdit.id == edit_id,
        OrderEdit.site_id == site.id, OrderEdit.tenant_id == site.tenant_id,
        OrderEdit.site_order_id == link.id).with_for_update())
    if not edit or edit.difference_minor <= 0 or action not in {'paid', 'invoice'}:
        raise CommerceError('Choose an amount owing and a valid settlement action.')
    latest = db.scalar(select(OrderEdit.id).where(OrderEdit.site_order_id == link.id,
        OrderEdit.site_id == site.id).order_by(OrderEdit.created_at.desc(), OrderEdit.id.desc()))
    if latest != edit.id:
        raise CommerceError('Use the latest order edit to settle this order.')
    if edit.settlement == 'paid':
        return edit
    if action == 'invoice':
        customer_services.queue_mail(db, site, customer, 'order_edit_invoice', 'edit-invoice:'+edit.id,
            reference={'site_order_id': link.id, 'order_edit_id': edit.id})
        edit.settlement = 'invoiced'
    else:
        db.add(PaymentTransaction(order_id=order.id, provider='manual:'+site.id,
            external_id='order-edit:'+edit.id, kind='charge', status='succeeded',
            currency=order.currency, amount_minor=edit.difference_minor))
        order.payment_status, edit.settlement = 'paid', 'paid'
        db.add(OrderComment(tenant_id=site.tenant_id, site_id=site.id, site_order_id=link.id,
            author_id=actor_id, internal=True, body='Order edit amount owing marked paid manually.'))
    db.flush()
    return edit
