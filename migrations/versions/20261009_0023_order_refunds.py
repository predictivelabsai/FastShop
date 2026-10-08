"""Durable site-scoped Stripe refund commands."""

from alembic import op

from app.models import RefundCommand

revision = "20261009_0023"
down_revision = "20261008_0022"
branch_labels = None
depends_on = None


def upgrade():
    RefundCommand.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    RefundCommand.__table__.drop(op.get_bind(), checkfirst=True)
