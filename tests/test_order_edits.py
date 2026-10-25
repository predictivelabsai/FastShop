"""Durable order edits, stock provenance, manual payment and security boundaries."""
import pytest
from sqlalchemy import func, select

from app import order_edits
from app.commerce_gap_models import OrderEdit
from app.integrations.commerce_email import render_message
from app.models import CommerceMail, OrderComment, PaymentTransaction
from app.services import CommerceError
from tests.test_order_admin import tracked  # noqa: F401
from tests.test_order_management import workspace


def selections(w, quantity=5, discount=0):
    return [dict(line_id=w.line.id, quantity=quantity, discount_minor=discount)]


def edit(w, rows=None, key='edit-session-request-0001', **kwargs):
    return order_edits.commit_edit(w.db, w.site, kwargs.pop('actor_id', w.owner.id), w.link.id,
        rows or selections(w), kwargs.pop('reason', 'Customer requested a change'), key,
        kwargs.pop('version', order_edits.version(w.order)))


def test_edit_allocates_releases_and_replays(tracked):
    w = tracked
    old_version = order_edits.version(w.order)
    row = edit(w, selections(w, 7))
    assert sum(stock.allocated for stock in w.stocks) == 7
    assert row.after_minor == 13600 and row.difference_minor == 3600
    assert w.order.payment_status == 'unpaid'
    assert edit(w, selections(w, 7), version=old_version).id == row.id
    with pytest.raises(CommerceError, match='different'):
        edit(w, selections(w, 6), version=old_version)
    w.db.commit()
    row2 = edit(w, selections(w, 3, 100), key='edit-session-request-0002')
    assert sum(stock.allocated for stock in w.stocks) == 3
    assert row2.after_minor == 6300 and row2.difference_minor == -3700
    assert w.db.scalar(select(func.count(OrderEdit.id))) == 2
    assert w.db.scalar(select(func.count(OrderComment.id))) == 2


def test_edit_timeline_uses_amounts_and_reason_without_raw_minor_unit_comment(tracked):
    from app import order_management
    from app.admin_activity import activity_context, record_activity
    from app.order_admin_ui import merchant_timeline

    w = tracked
    with activity_context(w.db, w.site, w.owner.id):
        edit(w, selections(w, 7))
    for _ in range(2):
        record_activity(w.db, w.site, w.owner.id, 'order', w.order.id,
                        'updated', 'Order updated.')
    w.db.commit()
    visible, _ = merchant_timeline(
        order_management.timeline(w.db, w.site, w.link, w.order), w.order.currency)
    edits = [event for event in visible if event['kind'] == 'order_edit']
    assert len(edits) == 1
    assert '136.00' in edits[0]['title']
    assert 'Customer requested a change' in edits[0]['detail']
    assert all('minor units' not in event.get('detail', '') for event in visible)
    assert any(event['kind'] == 'activity' and event['detail'].startswith('By ')
               for event in visible)
    assert len([event for event in visible if event['kind'] == 'activity']) == 1


def test_failed_edit_rolls_back_and_stale_edit_rejected(tracked):
    w = tracked
    before = order_edits.version(w.order)
    with pytest.raises(CommerceError, match='Not enough'):
        edit(w, selections(w, 100))
    w.db.commit()
    assert sum(stock.allocated for stock in w.stocks) == 5
    assert order_edits.version(w.order) == before
    edit(w, selections(w, 4))
    with pytest.raises(CommerceError, match='changed'):
        edit(w, selections(w, 3), key='edit-session-request-0002', version=before)


def test_custom_items_discount_and_manual_invoice_are_idempotent(tracked):
    w = tracked
    row = edit(w, selections(w)+[dict(name='Gift wrapping', price_minor=500, quantity=2, discount_minor=100)])
    assert row.difference_minor == 900
    order_edits.settle(w.db, w.site, w.owner.id, w.link.id, row.id, 'invoice')
    mail = w.db.scalar(select(CommerceMail).where(CommerceMail.kind == 'order_edit_invoice'))
    assert '9.00' in render_message(w.db, mail, w.site, w.customer)[1]
    order_edits.settle(w.db, w.site, w.owner.id, w.link.id, row.id, 'invoice')
    assert w.db.scalar(select(func.count(CommerceMail.id)).where(CommerceMail.kind == 'order_edit_invoice')) == 1
    order_edits.settle(w.db, w.site, w.owner.id, w.link.id, row.id, 'paid')
    order_edits.settle(w.db, w.site, w.owner.id, w.link.id, row.id, 'paid')
    assert w.db.scalar(select(func.count(PaymentTransaction.id)).where(PaymentTransaction.external_id == 'order-edit:'+row.id)) == 1
    assert w.order.payment_status == 'paid'
    assert render_message(w.db, mail, w.site, w.customer) is None


def test_order_edit_roles_tenant_and_fulfilled_quantities(tracked):
    w = tracked
    with pytest.raises(CommerceError):
        edit(w, actor_id=w.editor.id)
    with workspace() as foreign:
        with pytest.raises(CommerceError):
            order_edits.commit_edit(w.db, w.site, w.owner.id, foreign.link.id, selections(w),
                'Requested edit', 'foreign-edit-session-01', 'old')
    w.line.fulfilled_quantity = 3
    with pytest.raises(CommerceError, match='fulfilled'):
        edit(w, selections(w, 2))

def test_edit_after_partial_fulfilment_releases_only_own_location_reservations(tracked):
    from app import order_admin
    w = tracked
    # The first warehouse fulfils this order's two units. Its reservation retains
    # historical quantity, while the physical allocation is consumed.
    order_admin.fulfil_items(w.db, w.site, w.owner.id, w.link.id, {w.line.id:2},
        carrier='UPS', tracking_number='EDIT-PARTIAL', notify=False,
        request_key='edit-partial-fulfil-001')
    assert w.stocks[0].allocated == 0 and w.stocks[1].allocated == 3
    # The remaining stock at A belongs to a different order. This edit must
    # release our remaining commitment at B, leaving the other order untouched.
    from app.models import CheckoutAttempt, CommerceQuote, InventoryReservation
    from tests.test_order_management import add_order
    other_order, _ = add_order(w.db, w.site, w.customer, 'FS-OTHER-EDIT', 3600)
    original = w.db.scalar(select(CheckoutAttempt).where(CheckoutAttempt.order_id == w.order.id))
    source_quote = w.db.get(CommerceQuote, original.quote_id)
    other_quote = CommerceQuote(**{column.name:getattr(source_quote, column.name)
        for column in CommerceQuote.__table__.columns if column.name not in {'id', 'created_at', 'updated_at'}})
    w.db.add(other_quote)
    w.db.flush()
    other_attempt = CheckoutAttempt(tenant_id=w.site.tenant_id, site_id=w.site.id,
        customer_id=w.customer.id, quote_id=other_quote.id, order_id=other_order.id,
        request_key='other-edit-order-checkout', request_hash='other', state='paid',
        expires_at=original.expires_at)
    w.db.add(other_attempt)
    w.db.flush()
    other_reservation = InventoryReservation(tenant_id=w.site.tenant_id, site_id=w.site.id,
        attempt_id=other_attempt.id, stock_id=w.stocks[0].id, quantity=2, state='committed')
    w.db.add(other_reservation)
    w.stocks[0].allocated = 2
    w.db.commit()
    edit(w, selections(w, 4))
    assert other_reservation.quantity == 2 and other_reservation.state == 'committed'
    assert w.stocks[0].allocated == 2
    assert w.stocks[1].allocated == 2
    assert w.line.fulfilled_quantity == 2 and w.line.quantity == 4

@pytest.mark.parametrize('initial_status', ['fulfilled', 'delivered'])
def test_edit_completed_order_reopens_remaining_fulfilment(tracked, initial_status):
    from app import order_admin
    w = tracked
    order_admin.fulfil_items(w.db, w.site, w.owner.id, w.link.id, {w.line.id:5},
        carrier='UPS', tracking_number='EDIT-COMPLETE', notify=False,
        request_key='edit-complete-fulfil-001')
    w.order.status = initial_status
    w.db.commit()
    edit(w, selections(w, 6))
    assert w.order.status == 'partially_fulfilled'
    assert sum(stock.allocated for stock in w.stocks) == 1
    order_admin.fulfil_items(w.db, w.site, w.owner.id, w.link.id, {w.line.id:1},
        carrier='UPS', tracking_number='EDIT-ADDITIONAL', notify=False,
        request_key='edit-additional-fulfil-001')
    assert w.order.status == 'fulfilled' and w.line.fulfilled_quantity == 6
    assert sum(stock.allocated for stock in w.stocks) == 0


def test_fulfilled_return_refunds_do_not_double_count_minimum_quantity(tracked):
    from app import order_admin
    w = tracked
    order_admin.fulfil_items(w.db, w.site, w.owner.id, w.link.id, {w.line.id:5},
        carrier='UPS', tracking_number='EDIT-RETURNED', notify=False,
        request_key='edit-returned-fulfil-001')
    w.line.refunded_quantity = 2
    w.line.unfulfilled_refunded_quantity = 0
    w.order.status = 'delivered'
    w.db.commit()
    edit(w, selections(w, 5, 100))
    assert w.line.quantity == 5 and w.order.status == 'delivered'
