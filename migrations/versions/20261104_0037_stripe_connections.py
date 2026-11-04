"""Add encrypted merchant-managed Stripe connections."""

from alembic import op

from app.models import SiteStripeConnection

revision = "20261104_0037_stripe_connections"
down_revision = "20261104_0036_customer_login_codes"
branch_labels = None
depends_on = None


def upgrade():
    SiteStripeConnection.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    SiteStripeConnection.__table__.drop(op.get_bind(), checkfirst=True)
