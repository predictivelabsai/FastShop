"""Merchant growth rules, shipping, tax, notifications and staff invitations."""
from alembic import op

from app.models import Discount, DiscountCode, DiscountRedemption

revision = "20261025_0032_growth_settings"
down_revision = "20261025_0030"
branch_labels = None
depends_on = None


def tables():
    from app.growth_settings_models import (
        NotificationTemplate,
        ShippingRate,
        ShippingZone,
        StaffInvite,
        TaxRate,
    )
    return (Discount, DiscountCode, DiscountRedemption, ShippingZone, ShippingRate,
            TaxRate, NotificationTemplate, StaffInvite)


def upgrade():
    # Initial repository migration imports current metadata on fresh databases.
    for model in tables():
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in reversed(tables()):
        model.__table__.drop(op.get_bind(), checkfirst=True)
