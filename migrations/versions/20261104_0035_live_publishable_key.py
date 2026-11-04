"""Add a live Stripe publishable key to stored site credentials."""

from alembic import op
from sqlalchemy import Column, String, inspect

revision = "20261104_0035"
down_revision = "20261025_0034_admin_gaps"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns(
        "site_stripe_live_credentials"
    )}
    if "publishable_key" not in columns:
        with op.batch_alter_table("site_stripe_live_credentials") as batch:
            batch.add_column(Column("publishable_key", String(255), nullable=True))


def downgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns(
        "site_stripe_live_credentials"
    )}
    if "publishable_key" in columns:
        with op.batch_alter_table("site_stripe_live_credentials") as batch:
            batch.drop_column("publishable_key")
