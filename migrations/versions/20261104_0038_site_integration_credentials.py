"""Add encrypted site integration credentials."""

from alembic import op

from app.models import SiteIntegrationCredential

revision = "20261104_0038"
down_revision = "20261104_0037"
branch_labels = None
depends_on = None


def upgrade():
    SiteIntegrationCredential.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    SiteIntegrationCredential.__table__.drop(op.get_bind(), checkfirst=True)
