"""Durable customer-owned card-update sessions."""

from alembic import op

from app.models import SubscriptionPaymentSetup

revision = "20260921_0009"
down_revision = "20260921_0008"
branch_labels = None
depends_on = None


def upgrade():
    SubscriptionPaymentSetup.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    SubscriptionPaymentSetup.__table__.drop(op.get_bind(), checkfirst=True)
