"""Custom data, preferences, redirects, order edits and merchant administration."""

import sqlalchemy as sa
from alembic import op

revision = "20261025_0034_admin_gaps"
down_revision = "20261025_0033_merge_admin_v2"
branch_labels = None
depends_on = None


def tables():
    from app.admin_gap_models import AdminActivity, AdminTablePreference, SavedAdminView
    from app.commerce_gap_models import GiftCard, GiftCardEntry, OrderEdit
    from app.content_gap_models import (
        CustomDataDefinition,
        CustomDataValue,
        SitePreferences,
        UrlRedirect,
    )
    from app.theme_bulk_models import ThemeSnapshot
    return (SavedAdminView, AdminTablePreference, AdminActivity, CustomDataDefinition,
            CustomDataValue, SitePreferences, UrlRedirect, OrderEdit, GiftCard,
            GiftCardEntry, ThemeSnapshot)


def upgrade():
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("warehouses")}
    additions = (
        sa.Column("address_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("fulfils_online_orders", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    for column in additions:
        if column.name not in existing:
            op.add_column("warehouses", column)
    bind.execute(sa.text("UPDATE warehouses SET is_default = :yes WHERE id IN "
                        "(SELECT MIN(id) FROM warehouses GROUP BY tenant_id) "
                        "AND NOT EXISTS (SELECT 1 FROM warehouses w2 WHERE "
                        "w2.tenant_id = warehouses.tenant_id AND w2.is_default = :yes)"), {"yes": True})
    for model in tables():
        model.__table__.create(bind, checkfirst=True)


def downgrade():
    for model in reversed(tables()):
        model.__table__.drop(op.get_bind(), checkfirst=True)
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("warehouses")}
    for name in ("is_default", "fulfils_online_orders", "is_active", "address_json"):
        if name in existing:
            op.drop_column("warehouses", name)
