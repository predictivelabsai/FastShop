"""Phase 5e platform Stripe subscription billing."""

from alembic import op

from app.models import BillingSubscription, PlatformBillingStripeEvent

revision = "20261009_0028"
down_revision = "20261009_0027"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    BillingSubscription.__table__.create(bind, checkfirst=True)
    PlatformBillingStripeEvent.__table__.create(bind, checkfirst=True)


def downgrade():
    bind = op.get_bind()
    PlatformBillingStripeEvent.__table__.drop(bind, checkfirst=True)
    BillingSubscription.__table__.drop(bind, checkfirst=True)
