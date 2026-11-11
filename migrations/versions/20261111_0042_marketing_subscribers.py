"""Add site-scoped marketing subscribers."""

import secrets
from datetime import UTC, datetime

from alembic import op
from sqlalchemy import select

from app.models import MarketingConsent, MarketingSubscriber, ShopCustomer

revision = "20261111_0042"
down_revision = "20261110_0041"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    MarketingSubscriber.__table__.create(bind, checkfirst=True)
    source_map = {
        "footer": "footer",
        "checkout": "checkout",
        "account": "account",
        "admin": "admin",
        "import": "import",
        "first-order-offer": "account",
    }
    seen = set()
    rows = bind.execute(
        select(MarketingConsent, ShopCustomer)
        .join(ShopCustomer, ShopCustomer.id == MarketingConsent.customer_id)
        .where(
            MarketingConsent.status.in_(("subscribed", "unsubscribed")),
            ShopCustomer.site_id == MarketingConsent.site_id,
            ShopCustomer.tenant_id == MarketingConsent.tenant_id,
        )
    ).all()
    for consent, customer in rows:
        email = (customer.email or "").strip().lower()
        key = consent.site_id, email
        if not email or key in seen:
            continue
        seen.add(key)
        evidence = consent.consent_json if isinstance(consent.consent_json, dict) else {}
        source = source_map.get(str(evidence.get("source", "")), "account")
        consented_at = consent.confirmed_at or customer.email_consent_at or consent.created_at or datetime.now(UTC)
        bind.execute(
            MarketingSubscriber.__table__.insert().values(
                tenant_id=consent.tenant_id,
                site_id=consent.site_id,
                email=email,
                status=consent.status,
                source=source,
                consented_at=consented_at,
                consent_evidence=str(evidence.get("text") or "Existing customer marketing preference."),
                unsubscribe_token=secrets.token_urlsafe(32),
            )
        )


def downgrade():
    MarketingSubscriber.__table__.drop(op.get_bind(), checkfirst=True)
