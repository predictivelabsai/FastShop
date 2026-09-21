"""Private, isolated commerce simulations; no real payment/order records."""

from alembic import op

from app.models import DemoCommand, DemoWorkspace

revision = "20260921_0014"
down_revision = "20260921_0013"
branch_labels = None
depends_on = None


def upgrade():
    for model in (DemoWorkspace, DemoCommand):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in (DemoCommand, DemoWorkspace):
        model.__table__.drop(op.get_bind(), checkfirst=True)
