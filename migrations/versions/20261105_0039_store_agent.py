"""Add store agent conversations and messages."""

from alembic import op

from app.models import AgentConversation, AgentMessage

revision = "20261105_0039"
down_revision = "20261104_0038"
branch_labels = None
depends_on = None


def upgrade():
    AgentConversation.__table__.create(op.get_bind(), checkfirst=True)
    AgentMessage.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    AgentMessage.__table__.drop(op.get_bind(), checkfirst=True)
    AgentConversation.__table__.drop(op.get_bind(), checkfirst=True)
