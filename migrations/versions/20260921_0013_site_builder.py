"""Durable site builder turns and reversible draft changes."""

from alembic import op

from app.models import SiteBuilderTurn, SiteChangeSet

revision = "20260921_0013"
down_revision = "20260921_0012"
branch_labels = None
depends_on = None


def upgrade():
    for model in (SiteBuilderTurn, SiteChangeSet):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in (SiteChangeSet, SiteBuilderTurn):
        model.__table__.drop(op.get_bind(), checkfirst=True)
