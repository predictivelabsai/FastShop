"""Subscriber persistence, public consent and merchant workflows."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import content, marketing_subscribers
from app.models import Base, MarketingSubscriber, User


@pytest.fixture
def subscriber_data():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="subscriber-owner@example.test", name="Subscriber owner")
        other_owner = User(email="subscriber-other@example.test", name="Other owner")
        db.add_all((owner, other_owner))
        db.flush()
        site = content.create_site(db, owner.id, "Subscriber shop", "subscribers-" + uuid4().hex[:8])
        other_site = content.create_site(
            db, other_owner.id, "Other subscriber shop", "other-subscribers-" + uuid4().hex[:8],
        )
        db.commit()
    yield SimpleNamespace(engine=engine, sessions=sessions, site=site, other_site=other_site)
    engine.dispose()


def test_subscriber_records_normalize_and_deduplicate_per_site(subscriber_data):
    w = subscriber_data
    with w.sessions() as db:
        site = db.get(type(w.site), w.site.id)
        first, created = marketing_subscribers.upsert_subscriber(
            db, site, "  Reader@Example.Test ", source="footer", status="pending",
            evidence="I agree to receive marketing emails.",
        )
        db.commit()
        repeated, repeated_created = marketing_subscribers.upsert_subscriber(
            db, site, "reader@example.test", source="footer", status="pending",
            evidence="I agree to receive marketing emails.",
        )
        db.commit()
        assert created and not repeated_created
        assert first.id == repeated.id
        assert first.email == "reader@example.test"
        assert len(first.unsubscribe_token) >= 32
        assert first.status == "pending"
        assert marketing_subscribers.counts(db, site) == {
            "pending": 1, "subscribed": 0, "unsubscribed": 0, "total": 1,
        }


def test_subscriber_records_are_isolated_by_site_and_tenant(subscriber_data):
    w = subscriber_data
    with w.sessions() as db:
        for site in (db.get(type(w.site), w.site.id), db.get(type(w.other_site), w.other_site.id)):
            marketing_subscribers.upsert_subscriber(
                db, site, "same@example.test", source="admin", status="subscribed",
                evidence="I have consent.",
            )
        db.commit()
        rows = list(db.scalars(select(MarketingSubscriber).order_by(MarketingSubscriber.site_id)))

    assert len(rows) == 2
    assert rows[0].email == rows[1].email == "same@example.test"
    assert rows[0].site_id != rows[1].site_id
    assert rows[0].tenant_id != rows[1].tenant_id


def test_subscriber_migration_revision_id_fits_alembic_limit():
    migration = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261111_0042_marketing_subscribers.py"
    source = migration.read_text(encoding="utf-8")
    assert 'revision = "20261111_0042"' in source
    assert len("20261111_0042") <= 32
