"""Store the subscription and first-order savings per site instead of a fixed 10%."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261125_0037"
down_revision = "20261125_0036"
branch_labels = None
depends_on = None

TABLE = "site_commerce_settings"
COLUMNS = ("subscription_discount_percent", "first_order_discount_percent")


def upgrade():
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns(TABLE)}
    for name in COLUMNS:
        if name not in existing:
            op.add_column(TABLE, sa.Column(name, sa.Integer(), nullable=False, server_default="10"))


def downgrade():
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns(TABLE)}
    with op.batch_alter_table(TABLE) as batch:
        for name in COLUMNS:
            if name in existing:
                batch.drop_column(name)
