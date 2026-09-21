"""Retain a customer's draft discount code on their site-owned cart."""

import sqlalchemy as sa
from alembic import op

revision = "20260921_0012"
down_revision = "20260921_0011"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("site_carts")}
    if "discount_code" not in columns:
        op.add_column("site_carts", sa.Column("discount_code", sa.String(40), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("site_carts") as batch:
        batch.drop_column("discount_code")
