"""Create the FastShop commerce schema.

Revision ID: 20260908_0001
Revises:
Create Date: 2026-09-08
"""

from __future__ import annotations

from alembic import op

from app.models import Base

revision = "20260908_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)

