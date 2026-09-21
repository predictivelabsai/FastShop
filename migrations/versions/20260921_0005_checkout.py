"""Durable checkout attempts and expiring inventory reservations."""

from alembic import op

from app.models import CheckoutAttempt, InventoryReservation

revision = "20260921_0005"
down_revision = "20260921_0004"
branch_labels = None
depends_on = None


def upgrade():
    for model in (CheckoutAttempt, InventoryReservation):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in (InventoryReservation, CheckoutAttempt):
        model.__table__.drop(op.get_bind(), checkfirst=True)
