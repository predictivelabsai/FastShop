"""Phase 5d plan assignments and the durable usage-metering ledger."""

import sqlalchemy as sa
from alembic import op

from app.models import UsageEvent

revision = "20261009_0026"
down_revision = "20261009_0025"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tenant_columns = {column["name"] for column in sa.inspect(bind).get_columns("tenants")}
    if "plan" not in tenant_columns:
        op.add_column(
            "tenants",
            sa.Column("plan", sa.String(20), nullable=False, server_default="free"),
        )
        # Keep the operator's working platform fixtures on the generous tier:
        # existing deployments ran unquotad, and the operator account manages
        # these tenants (see app/seed.py, app/site_seed.py).
        bind.execute(sa.text(
            "UPDATE tenants SET plan = 'pro' WHERE slug IN ('fastshop-demo', 'h24you')"
        ))

    UsageEvent.__table__.create(bind, checkfirst=True)


def downgrade():
    bind = op.get_bind()
    UsageEvent.__table__.drop(bind, checkfirst=True)
    tenant_columns = {column["name"] for column in sa.inspect(bind).get_columns("tenants")}
    if "plan" in tenant_columns:
        with op.batch_alter_table("tenants") as batch:
            batch.drop_column("plan")