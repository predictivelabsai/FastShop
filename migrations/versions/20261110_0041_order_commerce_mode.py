"""Record the payment mode selected for each order."""

from alembic import op
from sqlalchemy import Column, String, inspect

revision = "20261110_0041"
down_revision = "20261105_0040"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("orders")}
    if "commerce_mode" not in columns:
        with op.batch_alter_table("orders") as batch:
            batch.add_column(Column("commerce_mode", String(12), nullable=False,
                                   server_default="sandbox"))


def downgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("orders")}
    if "commerce_mode" in columns:
        with op.batch_alter_table("orders") as batch:
            batch.drop_column("commerce_mode")
