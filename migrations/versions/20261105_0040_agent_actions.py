"""Add durable pending actions for the store agent."""

from alembic import op

from app.models import AgentAction

revision = "20261105_0040"
down_revision = "20261105_0039"
branch_labels = None
depends_on = None


def upgrade():
    AgentAction.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    AgentAction.__table__.drop(op.get_bind(), checkfirst=True)
