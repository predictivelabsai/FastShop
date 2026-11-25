"""Add encrypted, merchant-managed integration credentials."""

from alembic import op

from app.models import SiteIntegrationCredential

revision = "20261125_0036_site_integration_credentials"
down_revision = "20261125_0035_site_domains"
branch_labels = None
depends_on = None


def upgrade():
    SiteIntegrationCredential.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    SiteIntegrationCredential.__table__.drop(op.get_bind(), checkfirst=True)
