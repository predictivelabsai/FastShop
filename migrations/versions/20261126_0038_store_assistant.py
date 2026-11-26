"""Add the store assistant: conversations, messages, review actions, documents and tasks."""

from alembic import op

from app.models import (
    AgentAction,
    AgentConversation,
    AgentDocumentAttachment,
    AgentMessage,
    AssistantTask,
)

revision = "20261126_0038_store_assistant"
down_revision = "20261125_0037"
branch_labels = None
depends_on = None

# Creation order respects the foreign keys between the assistant tables.
TABLES = (AgentConversation, AgentMessage, AgentAction, AgentDocumentAttachment, AssistantTask)


def upgrade():
    for model in TABLES:
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in reversed(TABLES):
        model.__table__.drop(op.get_bind(), checkfirst=True)
