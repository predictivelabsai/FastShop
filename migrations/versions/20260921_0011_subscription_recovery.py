"""Link missed subscription deliveries to auditable one-time recovery checkouts."""

from alembic import op

from app.models import SubscriptionRecovery

revision = "20260921_0011"
down_revision = "20260921_0010"
branch_labels = None
depends_on = None


def upgrade():
    SubscriptionRecovery.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    SubscriptionRecovery.__table__.drop(op.get_bind(), checkfirst=True)
