"""Customer accounts are an explicit per-site opt-in."""

import sqlalchemy as sa
from alembic import op

revision = "20261011_0029"
down_revision = "20261009_0028"
branch_labels = None
depends_on = None


def upgrade():
    # Earlier create-table migrations import current models on fresh databases.
    if "customer_accounts_enabled" not in {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("site_commerce_settings")
    }:
        op.add_column("site_commerce_settings", sa.Column(
            "customer_accounts_enabled", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ))


def downgrade():
    op.drop_column("site_commerce_settings", "customer_accounts_enabled")
