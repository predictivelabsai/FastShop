"""Add tenant-owned sites, editorial revisions, media and contact inbox.

Revision ID: 20260921_0002
Revises: 20260908_0001
"""

from alembic import op

from app.models import Site, SiteContact, SiteMedia, SitePage, SiteRevision

revision = "20260921_0002"
down_revision = "20260908_0001"
branch_labels = None
depends_on = None

TABLES = (Site, SitePage, SiteRevision, SiteMedia, SiteContact)


def upgrade() -> None:
    for model in TABLES:
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for model in reversed(TABLES):
        model.__table__.drop(op.get_bind(), checkfirst=True)
