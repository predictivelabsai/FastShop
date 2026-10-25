"""Generic regressions retained from optional storefront integration work."""
import json

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app import content
from app.models import Base, SitePage, User
from app.services import CommerceError


@pytest.fixture
def storefronts():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="compatibility-owner@example.test", name="Owner")
        merchant = User(email="compatibility-merchant@example.test", name="Merchant")
        db.add_all([owner, merchant])
        db.flush()
        current = content.create_site(db, owner.id, "Current store", "current-store")
        previous = content.create_site(db, merchant.id, "Previous store", "previous-store")
        db.commit()
        yield db, current, previous, merchant
    engine.dispose()


def test_unknown_legacy_documents_read_without_rewrite_but_writes_stay_strict(storefronts):
    db, site, _, _ = storefronts
    page = db.scalar(select(SitePage).where(SitePage.site_id == site.id))
    legacy = {"title": "Previous theme", "sections": [
        {"type": kind, "heading": kind} for kind in ("retired_banner", "retired_story")
    ]}
    payload = json.dumps(legacy)
    db.execute(text("UPDATE site_pages SET draft_json=:doc, published_json=:doc WHERE id=:id"),
        {"doc": payload, "id": page.id})
    db.commit()
    db.expire(page)
    assert page.draft_json == legacy and page.published_json == legacy
    page.title = "A metadata change"
    db.commit()
    assert json.loads(db.execute(text("SELECT draft_json FROM site_pages WHERE id=:id"),
        {"id": page.id}).scalar_one()) == legacy
    with pytest.raises(CommerceError):
        page.draft_json = {**legacy, "title": "New write"}
    db.rollback()


def test_merchant_copilot_and_outbox_only_use_membership_tenants(storefronts):
    from app import ai, main
    from app.integrations.fasterp import deliver_pending
    from app.models import OutboxEvent

    db, site, previous, merchant = storefronts
    outsider = User(email="compatibility-outsider@example.test", name="No memberships")
    db.add(outsider)
    db.flush()
    assert main.membership_tenants(db, {"id": outsider.id}) == set()
    assert main.membership_tenants(db, {"id": merchant.id}) == {previous.tenant_id}
    own = OutboxEvent(tenant_id=site.tenant_id, topic="local.test", aggregate_id="test")
    other = OutboxEvent(tenant_id=previous.tenant_id, topic="local.test", aggregate_id="other")
    db.add_all([own, other])
    db.flush()
    assert "1 pending" in ai.answer(db, "/sync", "/admin", True, tenant_ids={site.tenant_id})
    assert "0 pending" in ai.answer(db, "/sync", "/admin", True, tenant_ids=set())
    assert deliver_pending(db, tenant_ids={site.tenant_id}) == (1, 0)
    assert own.status == "delivered" and other.status == "pending"
