"""Store agent reads remain scoped and writes wait for merchant confirmation."""

import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import catalog_admin, content
from app.models import AgentAction, Base, Product, User
from app.store_agent import run_turn
from app.store_agent_ops import confirm_action


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
