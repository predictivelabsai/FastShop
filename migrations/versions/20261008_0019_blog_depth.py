"""Site blog taxonomy; editorial metadata stays in versioned page documents."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from app.models import BlogCategory, Site
from app.site_blog import backfill_categories

revision = "20261008_0019"
down_revision = "20261008_0018"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    BlogCategory.__table__.create(bind, checkfirst=True)
    with Session(bind=bind) as db:
        for site in db.scalars(sa.select(Site)):
            backfill_categories(db, site)
        db.flush()


def downgrade():
    BlogCategory.__table__.drop(op.get_bind(), checkfirst=True)
