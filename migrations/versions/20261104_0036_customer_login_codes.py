"""Add isolated passwordless customer login code challenges."""

from alembic import op

from app.models import CustomerLoginCodeChallenge

revision = "20261104_0036"
down_revision = "20261104_0035"
branch_labels = None
depends_on = None


def upgrade():
    CustomerLoginCodeChallenge.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    CustomerLoginCodeChallenge.__table__.drop(op.get_bind(), checkfirst=True)
