"""Tenant and site-owned browser carts."""

from alembic import op

from app.models import SiteCart

revision = "20260921_0007"
down_revision = "20260921_0006"
branch_labels = None
depends_on = None


def upgrade():
    SiteCart.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    SiteCart.__table__.drop(op.get_bind(), checkfirst=True)
