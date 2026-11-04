"""Add isolated passwordless customer login code challenges."""

from alembic import op

from app.models import CustomerLoginCodeChallenge

revision = "20261104_0035_customer_login_codes"
down_revision = "20261025_0034_admin_gaps"
branch_labels = None
depends_on = None


def upgrade():
    CustomerLoginCodeChallenge.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    CustomerLoginCodeChallenge.__table__.drop(op.get_bind(), checkfirst=True)
