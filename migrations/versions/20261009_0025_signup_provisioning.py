"""Self-serve signup rate limits, verification, and account mail targets."""

import sqlalchemy as sa
from alembic import op

from app.models import SignupAttempt, SignupEmailVerification

revision = "20261009_0025"
down_revision = "20261009_0024"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    user_columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "email_verified_at" not in user_columns:
        op.add_column(
            "users",
            sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        )

    mail_columns = {
        column["name"]: column for column in sa.inspect(bind).get_columns("commerce_mail")
    }
    with op.batch_alter_table("commerce_mail") as batch:
        if not mail_columns["customer_id"]["nullable"]:
            batch.alter_column(
                "customer_id",
                existing_type=sa.String(32),
                nullable=True,
            )
        if "user_id" not in mail_columns:
            batch.add_column(sa.Column("user_id", sa.String(32), nullable=True))
            batch.create_foreign_key(
                "fk_commerce_mail_user_id_users",
                "users",
                ["user_id"],
                ["id"],
            )
            batch.create_index("ix_commerce_mail_user_id", ["user_id"])

    SignupAttempt.__table__.create(bind, checkfirst=True)
    SignupEmailVerification.__table__.create(bind, checkfirst=True)


def downgrade():
    bind = op.get_bind()
    SignupEmailVerification.__table__.drop(bind, checkfirst=True)
    SignupAttempt.__table__.drop(bind, checkfirst=True)
    bind.execute(sa.text("DELETE FROM commerce_mail WHERE customer_id IS NULL"))
    mail_columns = {column["name"] for column in sa.inspect(bind).get_columns("commerce_mail")}
    with op.batch_alter_table("commerce_mail") as batch:
        if "user_id" in mail_columns:
            batch.drop_index("ix_commerce_mail_user_id")
            batch.drop_constraint("fk_commerce_mail_user_id_users", type_="foreignkey")
            batch.drop_column("user_id")
        batch.alter_column(
            "customer_id",
            existing_type=sa.String(32),
            nullable=False,
        )
    user_columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "email_verified_at" in user_columns:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("email_verified_at")
