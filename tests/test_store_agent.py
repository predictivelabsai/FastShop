import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import content
from app.models import AgentMessage, Base, User
from app.store_agent import restore_checkpoint, run_turn


class FakeTransport:
    def __init__(self, replies):
        self.replies = iter(replies)

    def complete(self, messages, tools):
        return next(self.replies)


def call(name, args, ident="call-1"):
    return {
        "tool_calls": [
            {
                "id": ident,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ]
    }


@pytest.fixture
def workspace():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="agent@example.test", name="Agent")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Agent test", "agent-test")
        db.commit()
        yield db, user, site


def test_tool_turn_edits_and_checkpoint_restores(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    first = page.draft_json["blocks"][0]
    transport = FakeTransport(
        [
            call(
                "update_block",
                {
                    "page_id": page.id,
                    "block_id": first["id"],
                    "fields": {"heading": "Merchant supplied claim"},
                },
            ),
            call(
                "add_block",
                {
                    "page_id": page.id,
                    "type": "text",
                    "fields": {"heading": "New section", "body": "Section copy"},
                },
                "call-2",
            ),
            {"content": "Updated the hero and added a section."},
        ]
    )
    events = list(
        run_turn(db, site, user, "Make these edits", page_id=page.id, transport=transport)
    )
    assert events[-1]["type"] == "done"
    assert page.draft_json["blocks"][0]["heading"] == "Merchant supplied claim"
    assert len(page.draft_json["blocks"]) == 2
    msg = next(row for row in db.query(AgentMessage).all() if row.role == "tool")
    restore_checkpoint(db, site, user, msg.id)
    assert page.draft_json["blocks"][0] == first and len(page.draft_json["blocks"]) == 1


def test_invalid_arguments_are_returned_as_tool_error(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    events = list(
        run_turn(
            db,
            site,
            user,
            "try",
            page_id=page.id,
            transport=FakeTransport(
                [
                    call(
                        "update_block",
                        {"page_id": page.id, "block_id": "bad", "fields": {"heading": "x"}},
                    ),
                    {"content": "I could not find that block."},
                ]
            ),
        )
    )
    assert any(e["type"] == "tool_result" and '"error"' in e["summary"] for e in events)
    assert events[-1]["type"] == "done"


def test_cross_tenant_page_is_rejected(workspace):
    db, user, site = workspace
    other = User(email="other@example.test", name="Other")
    db.add(other)
    db.flush()
    other_site = content.create_site(db, other.id, "Other", "agent-other")
    foreign_page = content.site_pages(db, other_site)[0]
    events = list(
        run_turn(
            db,
            site,
            user,
            "Read",
            page_id=foreign_page.id,
            transport=FakeTransport([{"content": "No."}]),
        )
    )
    assert events[0]["type"] == "error"
    assert isinstance(events[0]["message"], str)


def test_step_limit_and_events(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    replies = [call("get_site_overview", {}, f"read-{i}") for i in range(15)]
    replies.append({"content": "unreachable"})
    events = list(
        run_turn(
            db, site, user, "inspect repeatedly", page_id=page.id, transport=FakeTransport(replies)
        )
    )
    assert events[-1]["type"] == "done"
    assert sum(event["type"] == "tool_called" for event in events) == 15
    assert any(event["type"] == "assistant_text" for event in events)
