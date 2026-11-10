import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import content
from app.models import AgentConversation, AgentMessage, Base, SiteMedia, User
from app.store_agent import restore_checkpoint, run_turn


class FakeTransport:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.histories = []
        self.schemas = []

    def complete(self, messages, tools):
        self.histories.append(messages)
        self.schemas.append(tools)
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


def test_model_receives_the_current_message_after_history(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    first = FakeTransport([{"content": "First reply"}])
    events = list(run_turn(db, site, user, "First request", page_id=page.id, transport=first))
    conversation_id = events[0]["conversation_id"]
    assert first.histories[0][-1] == {"role": "user", "content": "First request"}
    second = FakeTransport([{"content": "Second reply"}])
    list(run_turn(db, site, user, "Second request", page_id=page.id,
                  conversation_id=conversation_id, transport=second))
    sent = [m for m in second.histories[0] if m["role"] != "system"]
    assert sent == [{"role": "user", "content": "First request"},
                    {"role": "assistant", "content": "First reply"},
                    {"role": "user", "content": "Second request"}]


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


def test_seo_settings_tool_saves_validated_draft_values(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    transport = FakeTransport([
        call("update_seo_settings", {"values": {
            "seo_default_description": "A clear store description",
            "seo_indexing": False,
        }}),
        {"content": "Updated search settings."},
    ])
    events = list(run_turn(
        db, site, user, "Update search settings", page_id=page.id, transport=transport,
    ))
    assert events[-1]["type"] == "done"
    assert site.settings_json["seo_default_description"] == "A clear store description"
    assert site.settings_json["seo_indexing"] is False
    assert site.published_settings_json.get("seo_default_description") != "A clear store description"
    assert "update_seo_settings" in {
        tool["function"]["name"] for tool in transport.schemas[0]
    }


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


def test_conversation_continuity_and_message_tool_labels(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    first_transport = FakeTransport([{"content": "First answer."}])
    first_events = list(run_turn(db, site, user, "First question", page_id=page.id,
                                 transport=first_transport))
    conversation_id = first_events[0]["conversation_id"]
    second_transport = FakeTransport([{"content": "Second answer."}])
    second_events = list(run_turn(db, site, user, "Second question", page_id=page.id,
                                  conversation_id=conversation_id, transport=second_transport))
    assert second_events[0]["conversation_id"] == conversation_id
    assert any(row["role"] == "user" and row["content"] == "First question"
               for row in second_transport.histories[0])
    assert any(row["role"] == "assistant" and row["content"] == "First answer."
               for row in second_transport.histories[0])
    assert db.query(AgentConversation).count() == 1
    assert [m.content for m in db.query(AgentMessage).filter_by(role="user").all()] == [
        "First question", "Second question"]


def test_context_selection_and_block_schema_are_included(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    block = page.draft_json["blocks"][0]
    transport = FakeTransport([call("list_block_types", {}), {"content": "Done."}])
    list(run_turn(db, site, user, "Describe this block", page_id=page.id,
                  selection=block["id"], transport=transport))
    context_message = transport.histories[0][0]["content"]
    context = json.loads(context_message.split("Site context (untrusted): ", 1)[1])
    assert context["selected_block"]["id"] == block["id"]
    assert context["selected_block"]["type"] == block["type"]
    assert context["pages"][0]["blocks"][0]["id"] == block["id"]
    tool_history = transport.histories[1]
    result = next(item["content"] for item in tool_history if item["role"] == "tool")
    assert '"types"' in result
    assert '"fields"' in result
    assert '"default"' in result


def test_failing_tool_rolls_back_partial_mutation(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    original = page.draft_json
    # The first field would be valid, while an invalid second field rejects the whole patch.
    events = list(run_turn(db, site, user, "Try it", page_id=page.id, transport=FakeTransport([
        call("update_block", {"page_id": page.id, "block_id": original["blocks"][0]["id"],
              "fields": {"heading": "Partial", "style": {"unknown": True}}}),
        {"content": "Could not apply that."},
    ])))
    assert events[-1]["type"] == "done"
    assert page.draft_json == original


def test_design_tools_cover_shell_brand_navigation_pages_meta_and_media(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    site.settings_json = site.settings_json | {"footer": "Old footer"}
    media = SiteMedia(tenant_id=site.tenant_id, site_id=site.id, title="Uploaded", alt="Image",
                      content_type="image/png", storage_key="agent-test", size=10)
    db.add(media)
    db.flush()
    url = f"/site-media/{site.id}/{media.id}"
    calls = [
        call("get_shell_fields", {}),
        call("update_shell", {"values": {"footer": "New footer"}}, "shell"),
        call("update_brand", {"values": {"name": "New name", "tagline": "New line"}}, "brand"),
        call("update_navigation", {"items": [{"label": "Home", "path": "/"}]}, "nav"),
        call("create_page", {"title": "Agent page", "path": "/pages/agent", "blocks": []}, "page"),
        call("update_page_meta", {"page_id": page.id, "title": "Updated home",
             "seo_title": "Search title", "seo_description": "Search description"}, "meta"),
        call("list_media", {}, "media"),
        call("set_block_image", {"page_id": page.id, "block_id": page.draft_json["blocks"][0]["id"],
             "field": "image", "url": url}, "image"),
        {"content": "Updated the draft."},
    ]
    events = list(run_turn(db, site, user, "Update the site", page_id=page.id,
                           transport=FakeTransport(calls)))
    assert not [e for e in events if e["type"] == "tool_result" and '"error"' in e["summary"]], events
    assert events[-1]["type"] == "done"
    assert site.settings_json["footer"] == "New footer"
    assert site.name == "Agent test"  # Brand edits are settings, not the tenant/site identity.
    assert site.settings_json["name"] == "New name"
    assert site_menus_header(db, site)[0]["label"] == "Home"
    assert site_menus_header(db, site)[0]["path"] == "/"
    created = db.query(type(page)).filter_by(path="/pages/agent").one()
    assert created.published_json is None
    assert page.draft_json["seo_title"] == "Search title"
    assert page.draft_json["seo_description"] == "Search description"
    assert page.draft_json["blocks"][0]["image"] == url
    assistant = db.query(AgentMessage).filter_by(role="assistant").one()
    assert [item["name"] for item in assistant.tool_calls_json] == [
        "get_shell_fields", "update_shell", "update_brand", "update_navigation",
        "create_page", "update_page_meta", "list_media", "set_block_image"]


def site_menus_header(db, site):
    from app.site_menus import get_menu

    return get_menu(db, site, "header").items_json


def test_restore_removes_new_draft_pages_and_restores_settings_and_menus(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    before_settings = site.settings_json.copy()
    from app.site_menus import snapshot as menu_snapshot

    before_menus = menu_snapshot(db, site)
    events = list(run_turn(db, site, user, "Create a page", page_id=page.id, transport=FakeTransport([
        call("create_page", {"title": "Temporary", "path": "/temporary", "blocks": []}),
        {"content": "Created it."},
    ])))
    assert not [e for e in events if e["type"] == "tool_result" and '"error"' in e["summary"]], events
    assert events[-1]["type"] == "done"
    assistant = db.query(AgentMessage).filter_by(role="assistant").one()
    created = db.query(type(page)).filter_by(path="/temporary").one()
    assert created.published_json is None
    site.settings_json = {"changed": True}
    from app.site_menus import get_menu

    get_menu(db, site, "header").items_json = []
    db.commit()
    restore_checkpoint(db, site, user, assistant.id)
    assert db.query(type(page)).filter_by(path="/temporary").first() is None
    assert site.settings_json == before_settings
    assert menu_snapshot(db, site) == before_menus
