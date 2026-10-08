"""Extend the existing blob-backed media table with reusable URL metadata."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from app.models import Site, SiteMedia
from app.site_media import backfill_media

revision = "20261008_0018"
down_revision = "20261008_0017"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    SiteMedia.__table__.create(bind, checkfirst=True)
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("site_media")}
    if "public_url" not in columns:
        with op.batch_alter_table("site_media", naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}) as batch:
            batch.add_column(sa.Column("public_url", sa.String(2048), nullable=True))
            batch.add_column(sa.Column("localized_alt", sa.JSON(), nullable=True))
            batch.create_unique_constraint("uq_site_media_site_url", ["site_id", "public_url"])
            fk = next(f for f in inspector.get_foreign_keys("site_media") if f["constrained_columns"] == ["site_id"])
            batch.drop_constraint(fk["name"] or "fk_site_media_site_id_sites", type_="foreignkey")
            batch.create_foreign_key("fk_site_media_site_id_sites", "sites", ["site_id"], ["id"], ondelete="CASCADE")
    with Session(bind=bind) as db:
        for site in db.scalars(sa.select(Site)):
            backfill_media(db, site)
        db.flush()


def downgrade():
    with op.batch_alter_table("site_media", naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}) as batch:
        batch.drop_constraint("uq_site_media_site_url", type_="unique")
        batch.drop_constraint("fk_site_media_site_id_sites", type_="foreignkey")
        batch.create_foreign_key("fk_site_media_site_id_sites", "sites", ["site_id"], ["id"])
        batch.drop_column("localized_alt")
        batch.drop_column("public_url")
