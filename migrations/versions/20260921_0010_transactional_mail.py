"""Store scoped order/shipment references for transactional mail."""

import sqlalchemy as sa
from alembic import op

revision = "20260921_0010"
down_revision = "20260921_0009"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("commerce_mail")}
    if "reference_json" not in columns:
        op.add_column("commerce_mail", sa.Column("reference_json", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    with op.batch_alter_table("commerce_mail") as batch:
        batch.drop_column("reference_json")
