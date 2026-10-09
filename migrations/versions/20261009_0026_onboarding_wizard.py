"""Durable tenant onboarding wizard progress."""

from alembic import op

from app.models import OnboardingState

revision = "20261009_0026"
down_revision = "20261009_0025"
branch_labels = None
depends_on = None


def upgrade():
    OnboardingState.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    OnboardingState.__table__.drop(op.get_bind(), checkfirst=True)
