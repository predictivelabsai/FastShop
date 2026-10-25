"""Order quantities, exact refund maths, stock provenance and tenant boundaries."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select

from app import catalog_admin, order_admin
from app import order_management as orders
from app.models import (
    CheckoutAttempt,
    CommerceMail,
    CommerceQuote,
    InventoryAdjustment,
    InventoryReservation,
    OrderComment,
    OrderLine,
    OrderRestock,
    ReturnRequest,
    ShipmentEvent,
    Stock,
    Warehouse,
)
from app.services import CommerceError
from tests.test_order_management import workspace


@pytest.fixture
def tracked():
    with workspace() as w:
        db = w.db
        product = catalog_admin.create_product(db, w.site, {'name': 'Canvas tote', 'status': 'active'})
        variant = product.variants[0]
        a = Warehouse(id='p3-a', tenant_id=w.site.tenant_id, code='A', name='First')
        z = Warehouse(id='p3-z', tenant_id=w.site.tenant_id, code='Z', name='Second')
        db.add_all([a,z])
        db.flush()
        stock_a = Stock(id='p3-stock-a', warehouse_id=a.id, variant_id=variant.id, quantity=4, allocated=2)
        stock_z = Stock(id='p3-stock-z', warehouse_id=z.id, variant_id=variant.id, quantity=6, allocated=3)
        db.add_all([stock_z,stock_a])
        line = db.scalar(select(OrderLine).where(OrderLine.order_id == w.order.id))
        line.variant_id, line.quantity, line.unit_price_minor = variant.id, 5, 1800
        now = datetime.now(UTC)
        quote = CommerceQuote(tenant_id=w.site.tenant_id, site_id=w.site.id, fingerprint='fixture',
            provider_id='fixture', currency='USD', subtotal_minor=9000, discount_minor=0,
            shipping_minor=500, tax_minor=500, total_minor=10000, expires_at=now+timedelta(hours=1),
            snapshot_json={'lines': [], 'origin': {'country':'EE'}, 'destination':{}})
        db.add(quote)
        db.flush()
        attempt = CheckoutAttempt(tenant_id=w.site.tenant_id, site_id=w.site.id,
            customer_id=w.customer.id, quote_id=quote.id, request_key='fixture-order-checkout',
            request_hash='fixture', state='paid', order_id=w.order.id, expires_at=quote.expires_at)
        db.add(attempt)
        db.flush()
        db.add_all([InventoryReservation(tenant_id=w.site.tenant_id, site_id=w.site.id,
            attempt_id=attempt.id, stock_id=stock_a.id, quantity=2, state='committed'),
            InventoryReservation(tenant_id=w.site.tenant_id, site_id=w.site.id,
            attempt_id=attempt.id, stock_id=stock_z.id, quantity=3, state='committed')])
        db.commit()
        yield SimpleNamespace(**vars(w), line=line, variant=variant, stocks=[stock_a,stock_z])


def fulfil(w, quantity=5, key='fulfil-request-0001', notify=True):
    result = order_admin.fulfil_items(w.db, w.site, w.owner.id, w.link.id, {w.line.id:quantity},
        carrier='UPS', tracking_number='P3-TRACK', tracking_url='https://ups.com/track',
        notify=notify, request_key=key)
    w.db.commit()
    return result


class RefundGateway:
    calls = []
    status = 'succeeded'

    def __init__(self, site):
        self.site = site

    def create_refund(self, command_id, payload):
        self.calls.append((command_id,payload))
        return dict(id='re_'+command_id, payment_intent=payload['payment_intent'],
                    amount=payload['amount'], currency='usd', livemode=False, status=self.status)


def refund(w, quantities=None, key='refund-items-request-0001', **values):
    return order_admin.refund_items(w.site.id, w.owner.id, w.link.id,
        quantities if quantities is not None else {w.line.id:1},
        reason='Customer returned the unused items.', request_key=key,
        sessions=w.sessions, gateway_factory=RefundGateway, **values)


def total_stock(w):
    w.db.expire_all()
    return tuple(w.db.execute(select(func.sum(Stock.quantity),func.sum(Stock.allocated))
        .where(Stock.variant_id == w.variant.id)).one())


def test_partial_fulfilment_moves_stock_in_pk_order_and_replay_is_exact(tracked):
    w = tracked
    statements=[]
    event.listen(w.engine,'before_cursor_execute',lambda c,cur,s,p,ctx,m: statements.append(s))
    first=fulfil(w,3,notify=False)
    assert total_stock(w)==(7,2)
    assert w.order.status=='partially_fulfilled' and w.line.fulfilled_quantity==3
    assert w.db.scalar(select(func.count(CommerceMail.id)).where(CommerceMail.kind=='shipment_update'))==0
    assert fulfil(w,3,notify=False).id==first.id
    assert total_stock(w)==(7,2)
    with pytest.raises(CommerceError,match='different'):
        fulfil(w,2,notify=False)
    locks=[s for s in statements if 'FROM stocks' in s and 'ORDER BY stocks.id' in s]
    assert locks
    fulfil(w,2,key='fulfil-request-0002')
    assert total_stock(w)==(5,0) and w.order.status=='fulfilled'
    assert w.db.scalar(select(func.count(ShipmentEvent.id)))==2


def test_fulfilment_failure_rolls_back_all_lines_and_inventory(tracked):
    w=tracked
    with pytest.raises(CommerceError,match='exceeds'):
        fulfil(w,6)
    w.db.commit()
    assert total_stock(w)==(10,5)
    assert w.line.fulfilled_quantity==0
    assert w.db.scalar(select(func.count(ShipmentEvent.id)))==0
    with pytest.raises(CommerceError,match='access denied'):
        order_admin.fulfil_items(w.db,w.site,w.editor.id,w.link.id,{w.line.id:1},
            carrier='UPS',tracking_number='x',request_key='fulfil-role-request-01')


def test_refund_math_tax_shipping_caps_restock_and_idempotency(tracked):
    w=tracked
    RefundGateway.calls=[]
    fulfil(w)
    before=total_stock(w)
    preview=order_admin.preview_refund(w.db,w.site,w.link.id,{w.line.id:1},100,True)
    assert preview['amount_minor']==2000
    w.db.rollback()
    command=refund(w,shipping_minor=100,restock=True,expected_amount_minor=2000)
    assert command.state=='succeeded'
    assert total_stock(w)==(before[0]+1,0)
    assert w.line.refunded_quantity==1 and w.line.restocked_quantity==1
    assert w.db.scalar(select(func.count(InventoryAdjustment.id)).where(InventoryAdjustment.reason=='returned'))==1
    assert refund(w,shipping_minor=100,restock=True,expected_amount_minor=2000).id==command.id
    assert len(RefundGateway.calls)==1 and total_stock(w)==(6,0)
    with pytest.raises(CommerceError,match='different'):
        refund(w,shipping_minor=101,restock=True)
    with pytest.raises(CommerceError,match='Shipping refund'):
        order_admin.preview_refund(w.db,w.site,w.link.id,{},401)
    with pytest.raises(CommerceError,match='remaining'):
        order_admin.preview_refund(w.db,w.site,w.link.id,{w.line.id:5})
    with pytest.raises(CommerceError,match='changed'):
        refund(w,key='refund-changed-review-01',expected_amount_minor=1)


def test_cumulative_rounding_returns_every_minor_unit(tracked):
    w=tracked
    w.order.discount_minor=101
    w.order.subtotal_minor=9000
    w.order.tax_minor=503
    w.order.total_minor=9902
    w.db.commit()
    amounts=[]
    for i in range(5):
        command=refund(w,key=f'rounding-refund-key-{i:02d}')
        amounts.append(command.amount_minor)
    assert sum(amounts)==9402
    assert max(amounts)-min(amounts)<=1
    w.db.expire_all()
    assert w.line.unfulfilled_refunded_quantity==5
    with pytest.raises(CommerceError,match='remaining'):
        fulfil(w,1)


def test_pending_refund_reserves_quantity_and_does_not_restock(tracked):
    w=tracked
    fulfil(w)
    RefundGateway.status='pending'
    try:
        command=refund(w,restock=True)
        assert command.state=='pending' and total_stock(w)==(5,0)
        with pytest.raises(CommerceError,match='remaining'):
            order_admin.preview_refund(w.db,w.site,w.link.id,{w.line.id:5})
        with pytest.raises(CommerceError,match='available to return'):
            order_admin.create_return(w.db,w.site,w.owner.id,w.link.id,{w.line.id:5},'Return pending refund')
        RefundGateway.status='succeeded'
        command=refund(w,restock=True)
        assert command.state=='succeeded' and total_stock(w)==(6,0)
        assert refund(w,restock=True).id==command.id and total_stock(w)==(6,0)
    finally:
        RefundGateway.status='succeeded'


def test_return_lifecycle_receipt_restock_and_refund_do_not_double_restock(tracked):
    w=tracked
    fulfil(w)
    row=order_admin.create_return(w.db,w.site,w.owner.id,w.link.id,{w.line.id:2},'Unwanted unopened items')
    w.db.commit()
    with pytest.raises(CommerceError,match='cannot move'):
        order_admin.transition_return(w.db,w.site,w.owner.id,w.link.id,row.id,'received')
    for state in ('approved','received','received'):
        order_admin.transition_return(w.db,w.site,w.owner.id,w.link.id,row.id,state)
        w.db.commit()
    assert total_stock(w)==(7,0) and w.line.restocked_quantity==2
    command=refund(w,{w.line.id:2},restock=True,return_id=row.id)
    assert total_stock(w)==(7,0)
    w.db.expire_all()
    assert w.db.get(ReturnRequest,row.id).refund_command_id==command.id
    assert w.db.get(ReturnRequest,row.id).status=='closed'
    assert w.db.scalar(select(func.count(OrderRestock.id)))==1
    # Two returned and refunded units must not consume four returnable units.
    other=order_admin.create_return(w.db,w.site,w.owner.id,w.link.id,{w.line.id:3},'Remaining returned items')
    order_admin.transition_return(w.db,w.site,w.owner.id,w.link.id,other.id,'declined')
    w.db.commit()
    assert total_stock(w)==(7,0)


def test_comments_tags_archive_filter_literal_search_and_export(tracked):
    w=tracked
    order_admin.add_comment(w.db,w.site,w.owner.id,w.link.id,'Checked with the customer.')
    order_admin.update_order(w.db,w.site,w.owner.id,w.link.id,tags='priority, repeat, priority',note='Deliver to reception')
    w.db.commit()
    assert w.link.tags_json==['priority','repeat']
    assert any(e['detail']=='Checked with the customer.' for e in orders.timeline(w.db,w.site,w.link,w.order))
    assert order_admin.list_orders(w.db,w.site,{'q':w.line.sku})['total']==1
    assert order_admin.list_orders(w.db,w.site,{'q':'%_'})['total']==0
    assert order_admin.list_orders(w.db,w.site,{'tag':'PRIORITY','payment':'paid','per_page':1,'page':99})['page']==1
    assert order_admin.list_orders(w.db,w.site,{'tag':'none'})['total']==0
    order_admin.bulk_orders(w.db,w.site,w.owner.id,[w.link.id],'archive')
    w.db.commit()
    assert order_admin.list_orders(w.db,w.site,{'view':'archived'})['total']==1
    assert order_admin.list_orders(w.db,w.site,{'view':'open'})['total']==0
    csv=order_admin.export_orders(w.db,w.site,{'ids':[w.link.id]})
    assert w.order.number in csv and 'priority' in csv
    assert w.order.number not in order_admin.export_orders(w.db,w.site,{'ids':[]})
    assert w.db.scalar(select(func.count(OrderComment.id)))>=3


def test_cross_tenant_mutations_rejected_before_bulk_changes(tracked):
    w=tracked
    with workspace() as foreign:
        with pytest.raises(CommerceError,match='not found'):
            order_admin.update_order(w.db,w.site,w.owner.id,foreign.link.id,tags='leak')
        with pytest.raises(CommerceError,match='not found'):
            order_admin.bulk_orders(w.db,w.site,w.owner.id,[w.link.id,foreign.link.id],'archive')
        w.db.commit()
        assert not w.link.is_archived


def test_caller_rollback_keeps_stock_events_and_mail_atomic(tracked):
    w=tracked
    order_admin.fulfil_items(w.db,w.site,w.owner.id,w.link.id,{w.line.id:1},
        carrier='UPS',tracking_number='rollback',request_key='rollback-fulfil-key-01')
    w.db.rollback()
    assert total_stock(w)==(10,5)
    assert w.db.scalar(select(func.count(ShipmentEvent.id)))==0
    assert w.db.scalar(select(func.count(CommerceMail.id)))==0

def test_pending_restock_refund_reserves_physical_quantity(tracked):
    w=tracked
    fulfil(w,2)
    RefundGateway.status='pending'
    try:
        refund(w,{w.line.id:2},restock=True)
        with pytest.raises(CommerceError,match='Only fulfilled'):
            refund(w,{w.line.id:2},key='other-restock-refund-01',restock=True)
    finally:
        RefundGateway.status='succeeded'


def test_unfulfilled_refund_releases_own_allocation_without_adding_stock(tracked):
    w=tracked
    fulfil(w,2)
    assert total_stock(w)==(8,3)
    refund(w,{w.line.id:3},restock=False)
    assert total_stock(w)==(8,0)
    assert w.line.unfulfilled_refunded_quantity==3


def test_pending_unfulfilled_refund_cannot_be_fulfilled_concurrently(tracked):
    w=tracked
    RefundGateway.status='pending'
    try:
        refund(w,{w.line.id:3},restock=False)
        with pytest.raises(CommerceError,match='exceeds'):
            fulfil(w,3)
        fulfil(w,2)
        RefundGateway.status='succeeded'
        refund(w,{w.line.id:3},restock=False)
        assert total_stock(w)==(8,0)
    finally:
        RefundGateway.status='succeeded'

def test_failed_pending_rounding_refund_does_not_strand_last_minor_unit(tracked):
    w=tracked
    w.order.subtotal_minor=100
    w.order.tax_minor=0
    w.order.shipping_minor=0
    w.order.total_minor=100
    w.line.quantity=3
    w.line.total_minor=100
    w.db.commit()
    RefundGateway.status='pending'
    try:
        first=refund(w,key='round-pending-first-01')
        second=refund(w,key='round-pending-second-01')
        third=refund(w,key='round-pending-third-01')
        assert [first.amount_minor,second.amount_minor,third.amount_minor]==[33,33,34]
        RefundGateway.status='failed'
        refund(w,key='round-pending-first-01')
        RefundGateway.status='succeeded'
        refund(w,key='round-pending-second-01')
        refund(w,key='round-pending-third-01')
        final=refund(w,key='round-residual-final-01')
        assert final.amount_minor==33
    finally:
        RefundGateway.status='succeeded'

def test_a_return_can_only_hold_one_refund_command(tracked):
    w=tracked
    fulfil(w)
    row=order_admin.create_return(w.db,w.site,w.owner.id,w.link.id,{w.line.id:1},'Unwanted item')
    for state in ('approved','received'):
        order_admin.transition_return(w.db,w.site,w.owner.id,w.link.id,row.id,state)
    w.db.commit()
    RefundGateway.status='pending'
    try:
        first=refund(w,{w.line.id:1},key='return-pending-refund-01',return_id=row.id)
        with pytest.raises(CommerceError,match='already has'):
            refund(w,{w.line.id:1},key='return-pending-refund-02',return_id=row.id)
        assert refund(w,{w.line.id:1},key='return-pending-refund-01',return_id=row.id).id==first.id
    finally:
        RefundGateway.status='succeeded'


def test_order_search_filters_sort_and_pagination_are_composable(tracked):
    from tests.test_order_management import add_order
    w=tracked
    for i in range(27):
        order,link=add_order(w.db,w.site,w.customer,f'PAGED-{i:02d}',100+i,
            payment_status='pending' if i%2 else 'paid')
        link.tags_json=['group']
    w.db.commit()
    result=order_admin.list_orders(w.db,w.site,{'q':'PAGED','sort':'total_desc','per_page':10,'page':2})
    assert result['total']==27 and result['pages']==3 and len(result['rows'])==10
    assert result['rows'][0][1].total_minor==116
    assert result['rows'][-1][1].total_minor==107
    assert order_admin.list_orders(w.db,w.site,{'view':'unpaid','tag':'group'})['total']==13
    assert order_admin.list_orders(w.db,w.site,{'q':'Buyer','payment':'paid','tag':'group'})['total']==14
    assert order_admin.list_orders(w.db,w.site,{'start':'2099-01-01'})['total']==0
    assert order_admin.list_orders(w.db,w.site,{'end':'2000-01-01'})['total']==0
    assert order_admin.list_orders(w.db,w.site,{'channel':w.site.channel_id})['total']==28
    assert order_admin.list_orders(w.db,w.site,{'channel':'unrelated'})['total']==0


def test_legacy_fulfilment_preserves_validation_and_site_lock_order(tracked):
    w=tracked
    with pytest.raises(CommerceError,match='too long'):
        orders.transition_fulfillment(w.db,w.site,w.owner.id,w.link.id,'fulfilled','unfulfilled',
            carrier='UPS',tracking_number='test',note='x'*501)
    statements=[]
    event.listen(w.engine,'before_cursor_execute',lambda c,cur,s,p,ctx,m:statements.append(s))
    orders.transition_fulfillment(w.db,w.site,w.owner.id,w.link.id,'fulfilled','unfulfilled',
        carrier='UPS',tracking_number='test',note='Packed carefully')
    w.db.commit()
    assert total_stock(w)==(5,0)
    site_lock=next(i for i,s in enumerate(statements) if s.startswith('UPDATE sites'))
    order_lock=next(i for i,s in enumerate(statements) if 'FROM site_orders' in s)
    assert site_lock<order_lock


def test_shipment_notification_can_render_after_partial_refund(tracked):
    from app.integrations.commerce_email import render_message
    w=tracked
    fulfil(w,2)
    refund(w,{w.line.id:1},restock=False)
    w.db.expire_all()
    order_admin.fulfil_items(w.db,w.site,w.owner.id,w.link.id,{w.line.id:1},
        carrier='UPS',tracking_number='after-refund',notify=True,request_key='after-refund-fulfil-01')
    w.db.commit()
    message=w.db.scalar(select(CommerceMail).where(CommerceMail.kind=='shipment_update')
        .order_by(CommerceMail.created_at.desc(),CommerceMail.id.desc()))
    rendered=render_message(w.db,message,w.site,w.customer)
    assert rendered and 'delivery update' in rendered[0].lower()


def test_stale_session_cannot_overfulfil_after_another_command(tracked):
    w=tracked
    w.variant.track_inventory=False
    w.db.commit()
    assert w.order.lines[0].fulfilled_quantity==0
    with w.sessions() as other:
        order_admin.fulfil_items(other,w.site,w.owner.id,w.link.id,{w.line.id:1},
            carrier='UPS',tracking_number='first',request_key='parallel-first-key-01')
        other.commit()
    with pytest.raises(CommerceError,match='exceeds'):
        order_admin.fulfil_items(w.db,w.site,w.owner.id,w.link.id,{w.line.id:5},
            carrier='UPS',tracking_number='second',request_key='parallel-second-key-01')
