"""Store per-site sanitised custom CSS drafts and published versions."""

from alembic import op

from app.models import SiteDesignOverride, SiteDesignOverrideRevision

revision = "20261112_0044"
down_revision = "20261111_0043"
branch_labels = None
depends_on = None


def upgrade():
    SiteDesignOverride.__table__.create(op.get_bind(), checkfirst=True)
    SiteDesignOverrideRevision.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    SiteDesignOverrideRevision.__table__.drop(op.get_bind(), checkfirst=True)
    SiteDesignOverride.__table__.drop(op.get_bind(), checkfirst=True)
