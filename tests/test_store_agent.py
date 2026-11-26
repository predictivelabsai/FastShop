import copy
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import content
from app.models import (
    AgentConversation,
    AgentDocumentAttachment,
    AgentMessage,
    AssistantTask,
    Base,
    SiteMedia,
    SiteRevision,
    User,
)
from app.store_agent import SYSTEM, conversation_title, restore_checkpoint, run_turn


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


def test_document_attachment_tool_is_private_bounded_and_citable(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    attachment = AgentDocumentAttachment(
        tenant_id=site.tenant_id,
        site_id=site.id,
        user_id=user.id,
        title="brief.txt",
        content_type="text/plain",
        size=30,
        extracted_text="Approved source text for a private draft.",
    )
    outsider = User(email="attachment-outsider@example.test", name="Attachment outsider")
    db.add_all([attachment, outsider])
    db.flush()
    foreign_site = content.create_site(db, outsider.id, "Foreign attachments", "foreign-attachments")
    foreign = AgentDocumentAttachment(
        tenant_id=foreign_site.tenant_id,
        site_id=foreign_site.id,
        user_id=outsider.id,
        title="foreign.txt",
        content_type="text/plain",
        size=20,
        extracted_text="Private foreign source.",
    )
    db.add(foreign)
    db.commit()

    transport = FakeTransport([
                               call("read_document_attachment", {
                                   "attachment_id": attachment.id,
                                   "max_characters": 12,
                               }, "owned-document"),
                               call("read_document_attachment", {"attachment_id": foreign.id}, "foreign-document"),
                               {"content": "The draft can use the source."},
                           ])
    events = list(run_turn(db, site, user, "Read my attachment", page_id=page.id, transport=transport))
    results = [json.loads(event["summary"]) for event in events if event["type"] == "tool_result"]
    assert results[0] == {
        "attachment_id": attachment.id,
        "title": "brief.txt",
        "content_type": "text/plain",
        "source": "[Source: brief.txt]",
        "text": "Approved sou",
        "truncated": True,
    }
    assert results[1]["error"] == "Document attachment not found for this assistant."
    tools = {tool["function"]["name"]: tool["function"] for tool in transport.schemas[0]}
    assert tools["read_document_attachment"]["parameters"]["properties"]["max_characters"]["maximum"] == 12000
    assert "[Source: its file name]" in SYSTEM


def test_private_task_queue_is_assigned_tenant_scoped_and_never_publishes(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    teammate = User(email="task-teammate@example.test", name="Task teammate")
    outsider = User(email="task-outsider@example.test", name="Task outsider")
    db.add_all([teammate, outsider])
    db.flush()
    from app.models import Membership
    db.add(Membership(tenant_id=site.tenant_id, user_id=teammate.id, role="editor"))
    db.commit()

    transport = FakeTransport([
        call("list_task_assignees", {}, "task-assignees"),
        call("create_assistant_task", {
            "title": "Resolve missing page description",
            "description": "SEO audit found a missing draft description.",
            "priority": "high",
            "due_date": "2026-12-01",
            "assigned_user_id": teammate.id,
        }, "task-create"),
        {"content": "I created a private task for the audit finding."},
    ])
    events = list(run_turn(db, site, user, "Turn the audit finding into a task", page_id=page.id,
                           transport=transport))
    results = [json.loads(event["summary"]) for event in events if event["type"] == "tool_result"]
    assert results[0]["assignees"] == [
        {"user_id": user.id, "name": "Agent", "email": "agent@example.test", "role": "admin"},
        {"user_id": teammate.id, "name": "Task teammate", "email": "task-teammate@example.test", "role": "editor"},
    ]
    assert results[1]["priority"] == "high"
    assert results[1]["due_date"] == "2026-12-01"
    assert results[1]["assignee"]["user_id"] == teammate.id
    assert results[1]["published_content_changed"] is False
    task = db.scalar(select(AssistantTask).where(AssistantTask.site_id == site.id))
    assert task.title == "Resolve missing page description"

    follow_up = FakeTransport([
        call("update_assistant_task", {"task_id": task.id, "status": "in_progress"}, "task-update"),
        call("list_assistant_tasks", {"status": "in_progress"}, "task-list"),
        {"content": "The task is now in progress."},
    ])
    events = list(run_turn(db, site, user, "Update that task", page_id=page.id, transport=follow_up))
    results = [json.loads(event["summary"]) for event in events if event["type"] == "tool_result"]
    assert results[0]["status"] == "in_progress"
    assert [row["task_id"] for row in results[1]["tasks"]] == [task.id]

    foreign = FakeTransport([
        call("create_assistant_task", {"title": "Wrong owner", "assigned_user_id": outsider.id}, "task-foreign"),
        {"content": "I could not assign the task."},
    ])
    events = list(run_turn(db, site, user, "Assign outside the store", page_id=page.id, transport=foreign))
    result = next(json.loads(event["summary"]) for event in events if event["type"] == "tool_result")
    assert result["error"] == "Choose an active member of this store's team."
    assert db.scalar(select(AssistantTask).where(AssistantTask.title == "Wrong owner")) is None
    names = {tool["function"]["name"] for tool in transport.schemas[0]}
    assert {"list_task_assignees", "create_assistant_task", "list_assistant_tasks", "update_assistant_task"} <= names


def test_document_attachment_list_is_private_bounded_and_text_free(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    owned = AgentDocumentAttachment(
        tenant_id=site.tenant_id,
        site_id=site.id,
        user_id=user.id,
        title="brief.txt",
        content_type="text/plain",
        size=30,
        extracted_text="Private approved source text.",
    )
    outsider = User(email="attachment-list-outsider@example.test", name="Attachment outsider")
    db.add_all([owned, outsider])
    db.flush()
    foreign_site = content.create_site(db, outsider.id, "Foreign attachments", "foreign-attachment-list")
    db.add(AgentDocumentAttachment(
        tenant_id=foreign_site.tenant_id,
        site_id=foreign_site.id,
        user_id=outsider.id,
        title="foreign.txt",
        content_type="text/plain",
        size=20,
        extracted_text="Foreign private source.",
    ))
    db.commit()

    transport = FakeTransport([
        call("list_document_attachments", {"limit": 1}, "owned-sources"),
        {"content": "I found the private source."},
    ])
    events = list(run_turn(db, site, user, "List my document sources", page_id=page.id,
                           transport=transport))
    result = next(json.loads(event["summary"]) for event in events if event["type"] == "tool_result")

    assert result["attachments"] == [{
        "attachment_id": owned.id,
        "title": "brief.txt",
        "content_type": "text/plain",
        "size": 30,
        "characters": len(owned.extracted_text),
        "created_at": owned.created_at.isoformat(),
    }]
    assert owned.extracted_text not in json.dumps(result)
    tools = {tool["function"]["name"]: tool["function"] for tool in transport.schemas[0]}
    assert tools["list_document_attachments"]["parameters"]["properties"]["limit"]["maximum"] == 40
    assert "list_document_attachments to find a retained source" in SYSTEM


def test_catalogue_attachment_preparation_is_private_and_draft_only(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    attachment = AgentDocumentAttachment(
        tenant_id=site.tenant_id, site_id=site.id, user_id=user.id, title="catalogue.csv",
        content_type="text/csv", size=45,
        extracted_text="Name | Price | Inventory\nBottle | 49.00 | 5",
    )
    db.add(attachment)
    db.commit()
    transport = FakeTransport([
        call("prepare_catalogue_attachment", {"attachment_id": attachment.id}, "catalogue-plan"),
        {"content": "The catalogue plan is ready for review."},
    ])
    events = list(run_turn(db, site, user, "Prepare this catalogue", page_id=page.id, transport=transport))
    result = next(json.loads(event["summary"]) for event in events if event["type"] == "tool_result")
    assert result["attachment_id"] == attachment.id
    assert result["state"] == "draft_review"
    assert result["published_catalogue_changed"] is False
    assert db.query(AgentMessage).filter_by(role="assistant").one().checkpoint_revision_id is None
    tools = {tool["function"]["name"] for tool in transport.schemas[0]}
    assert "prepare_catalogue_attachment" in tools


def test_page_preview_and_revision_restore_stay_draft_only_and_checkpointed(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    original = copy.deepcopy(page.draft_json)
    first = copy.deepcopy(original)
    first["blocks"][0]["heading"] = "Original draft heading"
    content.save_page(db, site, page.id, user.id, first, page.version)
    saved = db.query(SiteRevision).filter_by(page_id=page.id, action="draft").one()
    revised = copy.deepcopy(page.draft_json)
    revised["blocks"][0]["heading"] = "Newer draft heading"
    content.save_page(db, site, page.id, user.id, revised, page.version)
    published_before = copy.deepcopy(page.published_json)

    transport = FakeTransport([
        call("get_page_preview", {"page_id": page.id}, "preview"),
        call("list_page_revisions", {"page_id": page.id}, "revisions"),
        call("restore_page_revision", {"page_id": page.id, "revision_id": saved.id}, "restore"),
        {"content": "The earlier draft is restored for private review."},
    ])
    events = list(run_turn(db, site, user, "Compare and restore an earlier draft", page_id=page.id,
                           transport=transport))
    results = [json.loads(event["summary"]) for event in events if event["type"] == "tool_result"]
    assert results[0]["draft_preview_url"] == f"/admin/sites/{site.id}/preview/{page.id}"
    assert results[0]["review_viewports"] == [
        {"label": "desktop", "width": 1440, "height": 900},
        {"label": "mobile", "width": 375, "height": 844},
    ]
    assert any(row["revision_id"] == saved.id for row in results[1]["revisions"])
    assert results[2]["state"] == "draft"
    assert results[2]["published_content_changed"] is False
    assert page.draft_json["blocks"][0]["heading"] == "Original draft heading"
    assert page.published_json == published_before
    assistant = db.query(AgentMessage).filter_by(role="assistant").one()
    assert assistant.checkpoint_revision_id

    restore_checkpoint(db, site, user, assistant.id)
    assert page.draft_json["blocks"][0]["heading"] == "Newer draft heading"
    assert page.published_json == published_before
    names = {tool["function"]["name"] for tool in transport.schemas[0]}
    assert {"get_page_preview", "list_page_revisions", "restore_page_revision"} <= names


def test_audit_tools_are_read_only_and_exposed_to_the_assistant(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    published_before = copy.deepcopy(page.published_json)
    transport = FakeTransport([
        call("audit_page_draft", {"page_id": page.id}, "draft-audit"),
        {"content": "The audit is ready for draft fixes."},
    ])
    events = list(run_turn(db, site, user, "Audit this draft", page_id=page.id, transport=transport))
    results = [json.loads(event["summary"]) for event in events if event["type"] == "tool_result"]
    assert results[0]["page_id"] == page.id
    assert results[0]["published_content_changed"] is False
    assert page.published_json == published_before
    assert db.query(AgentMessage).filter_by(role="assistant").one().checkpoint_revision_id is None
    names = {tool["function"]["name"] for tool in transport.schemas[0]}
    assert "audit_page_draft" in names
    # The SEO health report belongs to the dedicated SEO feature, not to this assistant.
    assert "audit_store_seo" not in names


def test_storefront_readiness_is_read_only_and_exposed_to_the_assistant(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    published_before = copy.deepcopy(page.published_json)
    settings_before = copy.deepcopy(site.published_settings_json)
    transport = FakeTransport([
        call("get_storefront_readiness", {"include_commerce": True}, "storefront-readiness"),
        {"content": "The launch checklist is ready for review."},
    ])

    events = list(run_turn(db, site, user, "Check storefront readiness", page_id=page.id,
                           transport=transport))
    result = next(json.loads(event["summary"]) for event in events
                  if event["type"] == "tool_result")

    assert "error" not in result, result
    assert result["site_id"] == site.id
    assert result["commerce_requested"] is True
    assert result["published_content_changed"] is False
    assert {"required_pages", "menus", "catalog"} <= {
        check["key"] for check in result["checks"]
    }
    assert page.published_json == published_before
    assert site.published_settings_json == settings_before
    assert db.query(AgentMessage).filter_by(role="assistant").one().checkpoint_revision_id is None
    names = {tool["function"]["name"] for tool in transport.schemas[0]}
    assert "get_storefront_readiness" in names


def test_draft_change_history_and_undo_stay_private(workspace):
    from app import site_builder_services as builder

    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    draft_before = copy.deepcopy(page.draft_json)
    published_before = copy.deepcopy(page.published_json)
    before = builder.snapshot(db, site)
    revised = copy.deepcopy(page.draft_json)
    revised["blocks"][0]["heading"] = "Temporary assistant draft"
    content.save_page(db, site, page.id, user.id, revised, page.version)
    site.version += 1
    db.flush()
    change = builder.record_change(db, site, user.id, before, "agent", "Temporary assistant draft")

    transport = FakeTransport([
        call("list_site_draft_changes", {}, "draft-history"),
        call("undo_site_draft_change", {"change_id": change.id}, "draft-undo"),
        {"content": "The private draft change was undone."},
    ])
    events = list(run_turn(db, site, user, "Undo the latest draft change", page_id=page.id,
                           transport=transport))
    results = [json.loads(event["summary"]) for event in events if event["type"] == "tool_result"]

    assert results[0]["changes"][0]["change_id"] == change.id
    assert results[0]["changes"][0]["can_undo"] is True
    assert results[0]["published_content_changed"] is False
    assert results[1]["state"] == "draft"
    assert results[1]["published_content_changed"] is False
    assert page.draft_json == draft_before
    assert page.published_json == published_before
    names = {tool["function"]["name"] for tool in transport.schemas[0]}
    assert {"list_site_draft_changes", "undo_site_draft_change"} <= names


def test_conversation_titles_exclude_admin_route_context(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    message = "Improve the collection heading\n\nAdmin page context: Products; route /admin/sites/private/products. Use this as a hint only."
    list(run_turn(db, site, user, message, page_id=page.id,
                  transport=FakeTransport([{"content": "Draft ready."}])))
    conversation = db.query(AgentConversation).one()
    assert conversation.title == "Improve the collection heading"
    assert conversation_title("Products; route /admin/sites/private/products") == "Products"


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


def test_article_tools_create_a_validated_draft_with_a_checkpoint(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    media = SiteMedia(tenant_id=site.tenant_id, site_id=site.id, title="Journal image",
                      alt="A glass on a table", content_type="image/png",
                      storage_key="journal-image", size=10)
    db.add(media)
    db.flush()
    image = f"/site-media/{site.id}/{media.id}"
    transport = FakeTransport([
        call("create_article", {
            "title": "A careful guide",
            "path": "/blog/careful-guide",
            "body": "<p>Useful detail</p><script>ignore()</script>",
            "category": "Guides",
            "tags": "water, reading",
            "excerpt": "A short guide.",
            "author": "Store editor",
            "image": image,
        }),
        {"content": "The article draft is ready."},
    ])

    events = list(run_turn(db, site, user, "Write a guide", page_id=page.id, transport=transport))

    result = json.loads(next(event["summary"] for event in events
                             if event["type"] == "tool_result"))
    assert result["state"] == "draft"
    assert result["published_content_changed"] is False
    article = db.query(type(page)).filter_by(path="/blog/careful-guide").one()
    assert article.kind == "article"
    assert article.published_json is None
    assert article.draft_json["blog"] == {
        "state": "draft", "category_slug": "guides", "tags": ["water", "reading"],
        "author_name": "Store editor", "author_bio": "", "publish_at": "", "excerpt": "A short guide.",
    }
    assert article.draft_json["image"] == image
    body = next(block["body"] for block in article.draft_json["blocks"]
                if block["id"] == "article-body")
    assert "<p>Useful detail</p>" in body
    assert "script" not in body
    assistant = db.query(AgentMessage).filter_by(role="assistant").one()
    assert assistant.checkpoint_revision_id
    tools = {tool["function"]["name"]: tool["function"] for tool in transport.schemas[0]}
    assert {"create_article", "update_article"} <= set(tools)
    assert "action" not in tools["create_article"]["parameters"]["properties"]


def test_article_tools_update_only_the_draft_of_a_published_article(workspace):
    from app.growth_content import article_document

    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    article = content.create_page(db, site, "Live article", "/blog/live-article", "article")
    document = article_document(db, site, article, {
        "title": "Live article", "body": "<p>Published body</p>", "category": "News",
        "tags": "news", "action": "publish",
    })
    content.save_page(db, site, article.id, user.id, document, article.version, "publish")
    published = copy.deepcopy(article.published_json)

    events = list(run_turn(db, site, user, "Revise the article", page_id=page.id,
                           transport=FakeTransport([
                               call("update_article", {
                                   "page_id": article.id,
                                   "path": "/blog/revised-article",
                               }, "published-path"),
                               call("update_article", {
                                   "page_id": article.id,
                                   "title": "Revised article",
                                   "body": "<p>Draft revision</p>",
                                   "category": "Research",
                                   "tags": "reading, research",
                               }),
                               {"content": "The draft revision is ready."},
                           ])))

    result = json.loads(next(event["summary"] for event in events
                             if event["type"] == "tool_result" and '"state"' in event["summary"]))
    assert result["state"] == "draft"
    assert any("published article path" in event["summary"] for event in events
               if event["type"] == "tool_result")
    assert article.path == "/blog/live-article"
    assert article.title == "Revised article"
    assert article.draft_json["blog"]["state"] == "draft"
    assert article.draft_json["blog"]["category_slug"] == "research"
    assert article.draft_json["blog"]["tags"] == ["reading", "research"]
    assert article.published_json == published


def test_article_tool_checkpoint_restores_created_and_updated_drafts(workspace):
    from app.growth_content import article_document

    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    article = content.create_page(db, site, "Existing", "/blog/existing", "article")
    document = article_document(db, site, article, {
        "title": "Existing", "body": "<p>Before</p>", "category": "News", "tags": "news",
    })
    content.save_page(db, site, article.id, user.id, document, article.version)
    before_document = copy.deepcopy(article.draft_json)
    before_path = article.path
    events = list(run_turn(db, site, user, "Prepare article drafts", page_id=page.id,
                           transport=FakeTransport([
                               call("update_article", {
                                   "page_id": article.id,
                                   "title": "Existing draft",
                                   "path": "/blog/existing-draft",
                                   "body": "<p>After</p>",
                                   "category": "News",
                               }, "update"),
                               call("create_article", {
                                   "title": "Temporary article",
                                   "path": "/blog/temporary-article",
                                   "body": "<p>Temporary</p>",
                                   "category": "Temporary category",
                               }, "create"),
                               {"content": "Both drafts are ready."},
                           ])))
    assert events[-1]["type"] == "done"
    assistant = db.query(AgentMessage).filter_by(role="assistant").one()
    assert article.path == "/blog/existing-draft"
    assert db.query(type(page)).filter_by(path="/blog/temporary-article").one()

    restore_checkpoint(db, site, user, assistant.id)

    assert article.path == before_path
    assert article.draft_json == before_document
    assert db.query(type(page)).filter_by(path="/blog/temporary-article").first() is None
    from app.site_blog import categories_for

    assert "news" in {category.slug for category in categories_for(db, site)}
    assert "temporary-category" not in {category.slug for category in categories_for(db, site)}


def test_article_tools_reject_invalid_metadata(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    events = list(run_turn(db, site, user, "Create an article", page_id=page.id,
                           transport=FakeTransport([
                               call("create_article", {
                                   "title": "Invalid article",
                                   "path": "/blog/invalid-article",
                                   "body": "<p>Draft</p>",
                                   "category": "News",
                                   "tags": "not a slug",
                               }),
                               {"content": "I could not prepare that draft."},
                           ])))

    errors = [event["summary"] for event in events if event["type"] == "tool_result"]
    assert any("tag slugs" in summary for summary in errors)
    assert all("Unknown tool." not in summary for summary in errors)
    assert db.query(type(page)).filter_by(path="/blog/invalid-article").first() is None


def test_article_tools_reject_foreign_pages_and_media(workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    other = content.create_site(db, user.id, "Other articles", "other-articles")
    foreign_article = content.create_page(db, other, "Private", "/blog/private", "article")
    foreign_media = SiteMedia(tenant_id=other.tenant_id, site_id=other.id, title="Private image",
                              alt="Private", content_type="image/png", storage_key="private", size=10)
    db.add(foreign_media)
    db.flush()
    foreign_url = f"/site-media/{other.id}/{foreign_media.id}"
    events = list(run_turn(db, site, user, "Try article changes", page_id=page.id,
                           transport=FakeTransport([
                               call("update_article", {
                                   "page_id": foreign_article.id,
                                   "title": "Changed",
                                   "body": "<p>No</p>",
                                   "category": "News",
                               }, "foreign-page"),
                               call("create_article", {
                                   "title": "Foreign media",
                                   "path": "/blog/foreign-media",
                                   "body": "<p>No</p>",
                                   "category": "News",
                                   "image": foreign_url,
                               }, "foreign-media"),
                               {"content": "I could not prepare those drafts."},
                           ])))

    errors = [event["summary"] for event in events if event["type"] == "tool_result"]
    assert len([summary for summary in errors if "error" in summary]) == 2
    assert "Page not found." in errors[0]
    assert "Choose media belonging to this site." in errors[1]
    assert all("Unknown tool." not in summary for summary in errors)
    assert foreign_article.draft_json["title"] == "Private"
    assert db.query(type(page)).filter_by(path="/blog/foreign-media").first() is None
