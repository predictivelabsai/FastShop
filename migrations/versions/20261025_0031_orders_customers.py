"""Site-owned orders, returns, drafts and customer management."""
import sqlalchemy as sa
from alembic import op

from app.models import (
    CustomerAddress,
    CustomerComment,
    CustomerSegment,
    DraftOrder,
    DraftOrderLine,
    OrderComment,
    OrderLine,
    OrderRestock,
    RefundCommand,
    ReturnLine,
    ReturnRequest,
    ShipmentEvent,
    ShipmentLine,
    ShopCustomer,
    SiteOrder,
)

revision = '20261025_0031_orders_customers'
down_revision = '20261025_0030'
branch_labels = None
depends_on = None

COLUMNS = (
    (ShopCustomer, ('phone', 'note', 'tags_json', 'account_state',
        'email_marketing_consent', 'sms_marketing_consent', 'email_consent_at',
        'sms_consent_at', 'email_consent_source', 'sms_consent_source')),
    (SiteOrder, ('tags_json', 'note', 'is_archived')),
    (OrderLine, ('fulfilled_quantity', 'refunded_quantity', 'unfulfilled_refunded_quantity', 'restocked_quantity')),
    (RefundCommand, ('selection_json', 'inventory_applied')),
    (ShipmentEvent, ('request_key', 'request_hash')),
)
TABLES = (OrderComment, ShipmentLine, OrderRestock, ReturnRequest, ReturnLine,
          DraftOrder, DraftOrderLine, CustomerAddress, CustomerComment, CustomerSegment)


def upgrade():
    bind = op.get_bind()
    # Initial revision uses current metadata, so fresh databases may already have
    # these columns and tables. Existing installations still need explicit DDL.
    for model, names in COLUMNS:
        present = {c['name'] for c in sa.inspect(bind).get_columns(model.__tablename__)}
        missing = [name for name in names if name not in present]
        if missing:
            with op.batch_alter_table(model.__tablename__) as batch:
                for name in missing:
                    batch.add_column(model.__table__.c[name]._copy())
        if model is ShipmentEvent:
            uniques = {c['name'] for c in sa.inspect(bind).get_unique_constraints('shipment_events')}
            if 'uq_shipment_request' not in uniques:
                with op.batch_alter_table('shipment_events') as batch:
                    batch.create_unique_constraint('uq_shipment_request', ['site_id', 'request_key'])
    for model in TABLES:
        model.__table__.create(bind, checkfirst=True)
    op.execute(sa.text("UPDATE order_lines SET fulfilled_quantity = quantity WHERE "
        "order_id IN (SELECT id FROM orders WHERE status IN ('fulfilled','delivered'))"))
    op.execute(sa.text("UPDATE shop_customers SET account_state = 'disabled' WHERE is_active = false"))
    # The existing email consent ledger remains authoritative for historic opt-ins.
    op.execute(sa.text("UPDATE shop_customers SET email_marketing_consent = true, "
        "email_consent_source = 'legacy', email_consent_at = (SELECT confirmed_at "
        "FROM marketing_consents mc WHERE mc.customer_id = shop_customers.id "
        "AND mc.site_id = shop_customers.site_id AND mc.tenant_id = shop_customers.tenant_id) "
        "WHERE EXISTS (SELECT 1 FROM marketing_consents mc WHERE mc.customer_id = shop_customers.id "
        "AND mc.site_id = shop_customers.site_id AND mc.tenant_id = shop_customers.tenant_id "
        "AND mc.status = 'subscribed')"))


def downgrade():
    bind = op.get_bind()
    for model in reversed(TABLES):
        model.__table__.drop(bind, checkfirst=True)
    for model, names in reversed(COLUMNS):
        present = {c['name'] for c in sa.inspect(bind).get_columns(model.__tablename__)}
        with op.batch_alter_table(model.__tablename__) as batch:
            if model is ShipmentEvent:
                uniques = {c['name'] for c in sa.inspect(bind).get_unique_constraints('shipment_events')}
                if 'uq_shipment_request' in uniques:
                    batch.drop_constraint('uq_shipment_request', type_='unique')
            for name in reversed(names):
                if name in present:
                    batch.drop_column(name)
