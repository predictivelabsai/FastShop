"""Store agent reads remain scoped and writes wait for merchant confirmation."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import catalog_admin, content
from app.models import AgentAction, Base, Product, User
from app.services import CommerceError
from app.store_agent import run_turn
from app.store_agent_ops import confirm_action, decline_action, register_routes


class FakeTransport:
    def __init__(self, *replies):
        self.replies = iter(replies)

    def complete(self, messages, tools):
        return next(self.replies)


def call(name, args):
    return {"tool_calls": [{"id": "store-call", "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}]}


def test_store_action_is_pending_until_confirmed_and_executes_once():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="ops@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Ops", "ops-test")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        page = content.site_pages(db, site)[0]
        events = list(run_turn(db, site, user, "Rename it", page_id=page.id, transport=FakeTransport(
            call("update_product", {"product_id": product.id, "name": "Raspberry crate"}),
            {"content": "I proposed a product update for confirmation."})))
        action = db.scalar(select(AgentAction))
        assert action.status == "pending"
        assert product.name == "Raspberry box"
        assert any(event["type"] == "action_pending" and event["id"] == action.id for event in events)
        result = confirm_action(db, site, user, action.id)
        assert result.status == "confirmed"
        assert product.name == "Raspberry crate"
        confirm_action(db, site, user, action.id)
        assert product.name == "Raspberry crate"
    engine.dispose()


def test_store_product_reads_reject_cross_tenant_ids():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = User(email="owner@example.test", name="Owner")
        other = User(email="other@example.test", name="Other")
        db.add_all([owner, other])
        db.flush()
        site = content.create_site(db, owner.id, "Owner", "owner-test")
        other_site = content.create_site(db, other.id, "Other", "other-test")
        product = catalog_admin.create_product(db, other_site, {"name": "Private item"})
        page = content.site_pages(db, site)[0]
        events = list(run_turn(db, site, owner, "Read it", page_id=page.id, transport=FakeTransport(
            call("get_product", {"product_id": product.id}), {"content": "That item is unavailable."})))
        assert any(event["type"] == "tool_result" and '"error"' in event["summary"] for event in events)
        assert db.scalar(select(Product).where(Product.id == product.id)).name == "Private item"
    engine.dispose()


def _propose_update(db, site, user, product, name="Raspberry crate"):
    existing_ids = set(db.scalars(select(AgentAction.id)).all())
    page = content.site_pages(db, site)[0]
    list(run_turn(db, site, user, "Update it", page_id=page.id, transport=FakeTransport(
        call("update_product", {"product_id": product.id, "name": name}),
        {"content": "I proposed a product update for confirmation."})))
    return db.scalar(select(AgentAction).where(AgentAction.id.not_in(existing_ids)))


def test_decline_leaves_product_unchanged():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="decline@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Decline", "decline-test")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        action = _propose_update(db, site, user, product)
        assert decline_action(db, site, user, action.id).status == "declined"
        assert product.name == "Raspberry box"
        assert confirm_action(db, site, user, action.id).status == "declined"
        assert product.name == "Raspberry box"
    engine.dispose()


def test_double_confirm_executes_once_and_stale_version_refuses():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="stale@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Stale", "stale-test")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        action = _propose_update(db, site, user, product)
        confirm_action(db, site, user, action.id)
        version = product.version
        confirm_action(db, site, user, action.id)
        assert product.name == "Raspberry crate"
        assert product.version == version

        stale = _propose_update(db, site, user, product, "Overwritten name")
        catalog_admin.update_product(db, site, product.id, {"description": "Changed elsewhere"},
                                     expected_version=product.version)
        assert confirm_action(db, site, user, stale.id).status == "failed"
        assert product.name == "Raspberry crate"
    engine.dispose()


def test_action_older_than_24_hours_expires_without_execution():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="expired@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Expired", "expired-test")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        action = _propose_update(db, site, user, product)
        action.created_at = datetime.now(UTC) - timedelta(hours=24, seconds=1)
        db.flush()
        assert confirm_action(db, site, user, action.id).status == "expired"
        assert product.name == "Raspberry box"
    engine.dispose()


def test_membership_and_tenant_scope_are_required_for_decisions():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="scope-owner@example.test", name="Owner")
        stranger = User(email="scope-stranger@example.test", name="Stranger")
        other = User(email="scope-other@example.test", name="Other")
        db.add_all([owner, stranger, other])
        db.flush()
        site = content.create_site(db, owner.id, "Scope", "scope-test")
        other_site = content.create_site(db, other.id, "Other", "scope-other")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        foreign_product = catalog_admin.create_product(db, other_site, {"name": "Private item"})
        action = _propose_update(db, site, owner, product)
        with pytest.raises(CommerceError, match="membership"):
            confirm_action(db, site, stranger, action.id)
        with pytest.raises(CommerceError, match="not found"):
            confirm_action(db, other_site, other, action.id)
        assert action.status == "pending"
        assert product.name == "Raspberry box"
        assert foreign_product.name == "Private item"
    engine.dispose()


@pytest.mark.parametrize("decision", ["confirm", "decline"])
def test_decision_routes_require_csrf(decision):
    routes = {}
    def register(path, **_kwargs):
        return lambda endpoint: routes.__setitem__(path, endpoint)

    register_routes(register, lambda _session: "user", lambda _session: "token",
                    lambda _session, _form: (_ for _ in ()).throw(CommerceError("CSRF rejected")),
                    lambda *args: None, lambda exc: {"error": str(exc)})
    endpoint = routes[f"/admin/sites/{{site_id}}/agent/actions/{{action_id}}/{decision}"]

    class Request:
        session = {}

        async def form(self):
            return {}

    import asyncio
    result = asyncio.run(endpoint("site", "action", Request()))
    assert result == {"error": "CSRF rejected"}
