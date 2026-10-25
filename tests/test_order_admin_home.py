"""P3 home tasks count real records inside the requested tenant and site."""
from app.models import DraftOrder, Order, ReturnRequest, ShopCustomer, SiteOrder
from tests.test_admin_foundation import foundation  # noqa: F401


def test_home_includes_requested_returns_and_open_drafts(foundation):
    w=foundation
    with w.sessions() as db:
        for site in (w.site,w.other):
            customer=ShopCustomer(tenant_id=site.tenant_id,site_id=site.id,email='tasks@example.test')
            db.add(customer)
            db.flush()
            order=Order(tenant_id=site.tenant_id,channel_id=site.channel_id,number='TASKS',
                idempotency_key='tasks-order',email=customer.email,currency='USD',subtotal_minor=100,total_minor=100)
            db.add(order)
            db.flush()
            link=SiteOrder(tenant_id=site.tenant_id,site_id=site.id,order_id=order.id,customer_id=customer.id)
            db.add(link)
            db.flush()
            for state in ('requested','closed'):
                db.add(ReturnRequest(tenant_id=site.tenant_id,site_id=site.id,site_order_id=link.id,
                    status=state,reason='Task fixture'))
            for state in ('open','invoice_sent','completed'):
                db.add(DraftOrder(tenant_id=site.tenant_id,site_id=site.id,status=state,currency='USD'))
        db.commit()
    response=w.client.get(f'/admin/sites/{w.site.id}')
    assert response.status_code==200
    assert '1 return requested' in response.text
    assert '2 open draft orders' in response.text
    assert '4 open draft orders' not in response.text
