"""Site-scoped external mappings and one-time connector plans."""

from alembic import op
from sqlalchemy import Column, String, inspect

from app.models import IntegrationPlan

revision = "20261008_0021"
down_revision = "20261008_0020"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    IntegrationPlan.__table__.create(bind, checkfirst=True)
    columns = {column["name"] for column in inspector.get_columns("external_mappings")}
    if "site_id" not in columns:
        batch_options = {}
        if bind.dialect.name == "sqlite":
            batch_options["naming_convention"] = {
                "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
            }
        with op.batch_alter_table("external_mappings", **batch_options) as batch:
            # SQLite batch reflection otherwise drops the legacy tenant FK's
            # ON DELETE action while rebuilding the table.
            if bind.dialect.name == "sqlite":
                batch.drop_constraint(
                    "fk_external_mappings_tenant_id_tenants", type_="foreignkey"
                )
                batch.create_foreign_key(
                    "fk_external_mappings_tenant_id_tenants",
                    "tenants",
                    ["tenant_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
            batch.add_column(Column("site_id", String(32), nullable=True))
            batch.create_foreign_key(
                "fk_external_mappings_site_id_sites",
                "sites",
                ["site_id"],
                ["id"],
                ondelete="CASCADE",
            )
            batch.create_index("ix_external_mappings_site_id", ["site_id"])
            batch.create_unique_constraint(
                "uq_external_mapping_site_external",
                ["tenant_id", "site_id", "system", "resource_type", "external_id"],
            )


def downgrade():
    bind = op.get_bind()
    IntegrationPlan.__table__.drop(bind, checkfirst=True)
    columns = {column["name"] for column in inspect(bind).get_columns("external_mappings")}
    if "site_id" in columns:
        with op.batch_alter_table("external_mappings") as batch:
            batch.drop_constraint("uq_external_mapping_site_external", type_="unique")
            batch.drop_index("ix_external_mappings_site_id")
            batch.drop_constraint("fk_external_mappings_site_id_sites", type_="foreignkey")
            batch.drop_column("site_id")
