"""Customer reviews table (previously model-only; required for Postgres/Alembic)."""

from alembic import op

from app.models import Review

revision = "20260923_0015"
down_revision = "20260921_0014"
branch_labels = None
depends_on = None


def upgrade():
    Review.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    Review.__table__.drop(op.get_bind(), checkfirst=True)
