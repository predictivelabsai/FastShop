"""Per-user password hashes on users (multiple password accounts)."""

import sqlalchemy as sa
from alembic import op

revision = "20261007_0016"
down_revision = "20260923_0015"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "password_hash" not in columns:
        op.add_column("users", sa.Column("password_hash", sa.String(200), nullable=True))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("password_hash")