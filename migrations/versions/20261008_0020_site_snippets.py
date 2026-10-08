"""Site-scoped draft and published head/foot snippets."""

from alembic import op

from app.models import SiteSnippet

revision = "20261008_0020"
down_revision = "20261008_0019"
branch_labels = None
depends_on = None


def upgrade():
    SiteSnippet.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    SiteSnippet.__table__.drop(op.get_bind(), checkfirst=True)
