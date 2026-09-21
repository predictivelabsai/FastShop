"""Physical-delivery subscription contracts, cycles and customer audit events."""

from alembic import op

from app.models import SubscriptionContract, SubscriptionCycle, SubscriptionEvent

revision = "20260921_0008"
down_revision = "20260921_0007"
branch_labels = None
depends_on = None


def upgrade():
    for model in (SubscriptionContract, SubscriptionCycle, SubscriptionEvent):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in (SubscriptionEvent, SubscriptionCycle, SubscriptionContract):
        model.__table__.drop(op.get_bind(), checkfirst=True)
