"""Add site-owned US commerce configuration and immutable tax quotes.

Revision ID: 20260921_0003
Revises: 20260921_0002
"""

from alembic import op

from app.models import CommerceQuote, SiteCommerceSettings

revision = "20260921_0003"
down_revision = "20260921_0002"
branch_labels = None
depends_on = None


def upgrade():
    for model in (SiteCommerceSettings, CommerceQuote):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in (CommerceQuote, SiteCommerceSettings):
        model.__table__.drop(op.get_bind(), checkfirst=True)
