"""Persist provider commands before sending them to Stripe."""

import sqlalchemy as sa
from alembic import op

revision = "20260921_0006"
down_revision = "20260921_0005"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("checkout_attempts")}
    if "provider_payload_json" not in columns:
        op.add_column("checkout_attempts", sa.Column("provider_payload_json", sa.JSON(), nullable=False, server_default="{}"))
    if "provider_started_at" not in columns:
        op.add_column("checkout_attempts", sa.Column("provider_started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table("checkout_attempts") as batch:
        batch.drop_column("provider_started_at")
        batch.drop_column("provider_payload_json")
