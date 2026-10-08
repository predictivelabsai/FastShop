"""Site-scoped navigation with draft and published ordered items."""

from alembic import op
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Site, SiteMenu
from app.site_menus import adapt_navigation

revision = "20261008_0017"
down_revision = "20261007_0016"
branch_labels = None
depends_on = None


def upgrade():
    # The environment sets PostgreSQL search_path and version_table_schema.
    SiteMenu.__table__.create(op.get_bind(), checkfirst=True)
    with Session(bind=op.get_bind()) as db:
        # Migration-only enumeration; all per-site adaptation queries are scoped.
        for site in db.scalars(select(Site)):
            adapt_navigation(db, site)
        db.flush()


def downgrade():
    SiteMenu.__table__.drop(op.get_bind(), checkfirst=True)
