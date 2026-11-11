"""Add campaigns and abandoned checkout email persistence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from app.models import (
    MarketingAbandonedEmail,
    MarketingAutomation,
    MarketingCampaign,
    MarketingCampaignDelivery,
)

revision = "20261111_0043"
down_revision = "20261111_0042"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for table in (
        MarketingCampaign.__table__,
        MarketingCampaignDelivery.__table__,
        MarketingAutomation.__table__,
        MarketingAbandonedEmail.__table__,
    ):
        table.create(bind, checkfirst=True)

    columns = {column["name"] for column in inspect(bind).get_columns("site_carts")}
    if "recovery_email_id" not in columns:
        op.add_column("site_carts", sa.Column("recovery_email_id", sa.String(32), nullable=True))
    indexes = {index["name"] for index in inspect(bind).get_indexes("site_carts")}
    if "ix_site_carts_recovery_email_id" not in indexes:
        op.create_index("ix_site_carts_recovery_email_id", "site_carts", ["recovery_email_id"])


def downgrade():
    bind = op.get_bind()
    indexes = {index["name"] for index in inspect(bind).get_indexes("site_carts")}
    if "ix_site_carts_recovery_email_id" in indexes:
        op.drop_index("ix_site_carts_recovery_email_id", table_name="site_carts")
    columns = {column["name"] for column in inspect(bind).get_columns("site_carts")}
    if "recovery_email_id" in columns:
        with op.batch_alter_table("site_carts") as batch:
            batch.drop_column("recovery_email_id")
    MarketingAbandonedEmail.__table__.drop(bind, checkfirst=True)
    MarketingAutomation.__table__.drop(bind, checkfirst=True)
    MarketingCampaignDelivery.__table__.drop(bind, checkfirst=True)
    MarketingCampaign.__table__.drop(bind, checkfirst=True)
