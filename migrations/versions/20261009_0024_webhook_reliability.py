"""Bounded outbox retries and a payload-free Stripe webhook ledger."""

from alembic import op
from sqlalchemy import Column, DateTime, inspect

from app.models import StripeWebhookEvent

revision = "20261009_0024"
down_revision = "20261009_0023"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("outbox_events")}
    if "next_attempt_at" not in columns:
        with op.batch_alter_table("outbox_events") as batch:
            batch.add_column(Column("next_attempt_at", DateTime(timezone=True), nullable=True))
            batch.create_index(
                "ix_outbox_status_due_created",
                ["status", "next_attempt_at", "created_at"],
            )
    StripeWebhookEvent.__table__.create(bind, checkfirst=True)


def downgrade():
    bind = op.get_bind()
    StripeWebhookEvent.__table__.drop(bind, checkfirst=True)
    columns = {column["name"] for column in inspect(bind).get_columns("outbox_events")}
    if "next_attempt_at" in columns:
        with op.batch_alter_table("outbox_events") as batch:
            batch.drop_index("ix_outbox_status_due_created")
            batch.drop_column("next_attempt_at")
