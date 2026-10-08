"""Encrypted per-site Stripe live credentials and explicit acceptance state."""

from alembic import op
from sqlalchemy import Column, DateTime, ForeignKey, String, inspect

from app.models import SiteStripeLiveCredential

revision = "20261008_0022"
down_revision = "20261008_0021"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    SiteStripeLiveCredential.__table__.create(bind, checkfirst=True)
    columns = {column["name"] for column in inspect(bind).get_columns("site_commerce_settings")}
    additions = (
        ("live_accepted_at", Column("live_accepted_at", DateTime(timezone=True), nullable=True)),
        ("live_accepted_by", Column("live_accepted_by", String(32), ForeignKey("users.id"), nullable=True)),
        ("live_credential_id", Column("live_credential_id", String(32), ForeignKey("site_stripe_live_credentials.id"), nullable=True)),
    )
    for name, column in additions:
        if name not in columns:
            with op.batch_alter_table("site_commerce_settings") as batch:
                batch.add_column(column)


def downgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("site_commerce_settings")}
    for name in ("live_credential_id", "live_accepted_by", "live_accepted_at"):
        if name in columns:
            with op.batch_alter_table("site_commerce_settings") as batch:
                batch.drop_column(name)
    SiteStripeLiveCredential.__table__.drop(bind, checkfirst=True)
