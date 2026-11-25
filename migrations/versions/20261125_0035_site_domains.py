"""Add merchant-connected custom domains."""

from alembic import op

from app.models import SiteDomain

revision = "20261125_0035_site_domains"
down_revision = "20261025_0034_admin_gaps"
branch_labels = None
depends_on = None


def upgrade():
    SiteDomain.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    SiteDomain.__table__.drop(op.get_bind(), checkfirst=True)
