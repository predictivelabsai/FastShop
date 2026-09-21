"""Site customer identity, consent, offers, mail queue and shipment events."""

from alembic import op

from app.models import (
    CommerceMail,
    CustomerChallenge,
    CustomerOffer,
    MarketingConsent,
    ShipmentEvent,
    ShopCustomer,
    SiteOrder,
)

revision = "20260921_0004"
down_revision = "20260921_0003"
branch_labels = None
depends_on = None
TABLES = (ShopCustomer, CustomerChallenge, MarketingConsent, CustomerOffer, CommerceMail, SiteOrder, ShipmentEvent)


def upgrade():
    for model in TABLES:
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in reversed(TABLES):
        model.__table__.drop(op.get_bind(), checkfirst=True)
