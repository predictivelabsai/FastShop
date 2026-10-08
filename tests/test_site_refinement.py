import copy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import content
from app import site_builder_services as builder
from app.integrations.site_builder_llm import guided
from app.models import Base, SiteChangeSet, SiteRevision, User
from app.services import CommerceError
from app.site_blocks import add_block, get_block, patch_block
from app.site_refinement import derive_operations, diff_documents


@pytest.fixture
def workspace():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="refinement@example.test", name="Refinement owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Refinement", "refinement")
        page = content.site_pages(db, site)[0]
        page.draft_json = add_block(page.draft_json, {
            "id": "details", "type": "text", "heading": "Details", "body": "Original details",
        })
        page.draft_json = add_block(page.draft_json, {
            "id": "faq", "type": "faq", "heading": "Questions", "items": [{"heading": "When?", "body": "Soon."}],
        })
        db.commit()
        yield db, owner, site, page


def _turn(db, owner, site, page, prompt, section_id=""):
    turn, _ = builder.begin_turn(db, site.id, owner.id, uuid4().hex, prompt, page.id, site.version, section_id)
    response = guided(prompt, turn.context_json)
    builder.finish_turn(db, site.id, owner.id, turn.id, response, "guided")
    return turn


def test_full_document_and_direct_patch_converge_to_one_minimal_op(workspace):
    db, _, site, page = workspace
    snapshot = builder.snapshot(db, site)
    hero = page.draft_json["blocks"][0]
    direct = [{"op": "patch", "page_id": page.id, "block_id": hero["id"], "after": {"heading": "A precise heading"}}]
    desired = patch_block(page.draft_json, hero["id"], {"heading": "A precise heading"}, locale="en")
    _, direct_ops = derive_operations(snapshot, direct)
    _, document_ops = derive_operations(snapshot, [{"op": "document", "page_id": page.id, "document": desired}])
    def comparable(operation):
        return {key: value for key, value in operation.items() if key not in {"op_id", "description"}}
    assert len(direct_ops) == len(document_ops) == 1
    assert comparable(direct_ops[0]) == comparable(document_ops[0])
    assert direct_ops[0]["before"] == {"heading": hero["heading"]}
    assert direct_ops[0]["after"] == {"heading": "A precise heading"}


def test_full_document_diff_preserves_other_locales(workspace):
    db, _, site, page = workspace
    hero_id = page.draft_json["blocks"][0]["id"]
    page.draft_json = patch_block(page.draft_json, hero_id, {"heading": {"en": "Welcome", "et": "Tere"}})
    snapshot = builder.snapshot(db, site)
    desired = patch_block(page.draft_json, hero_id, {"heading": "Hello"})
    _, operations = derive_operations(snapshot, [{"op": "document", "page_id": page.id, "document": desired}])
    assert operations[0]["after"]["heading"] == {"en": "Hello", "et": "Tere"}


def test_reject_leaves_page_revision_and_change_history_untouched(workspace):
    db, owner, site, page = workspace
    before = copy.deepcopy(page.draft_json)
    revisions = len(list(db.scalars(select(SiteRevision).where(SiteRevision.page_id == page.id))))
    changes = len(list(db.scalars(select(SiteChangeSet).where(SiteChangeSet.site_id == site.id))))
    turn = _turn(db, owner, site, page, "Headline: Review me", page.draft_json["blocks"][0]["id"])
    operation = turn.response_json["refinement"]["operations"][0]
    assert page.draft_json == before and operation["status"] == "pending"
    builder.decide_refinement(db, site.id, owner.id, turn.id, "reject", operation["op_id"], site.version)
    assert page.draft_json == before
    assert len(list(db.scalars(select(SiteRevision).where(SiteRevision.page_id == page.id)))) == revisions
    assert len(list(db.scalars(select(SiteChangeSet).where(SiteChangeSet.site_id == site.id)))) == changes


def test_accept_applies_exact_patch_records_revision_and_undo(workspace):
    db, owner, site, page = workspace
    before = builder.snapshot(db, site)
    hero_id = page.draft_json["blocks"][0]["id"]
    turn = _turn(db, owner, site, page, "Headline: Accepted heading", hero_id)
    count, status = builder.decide_refinement(db, site.id, owner.id, turn.id, "accept", "all", site.version)
    assert (count, status) == (1, "accepted")
    assert get_block(page.draft_json, hero_id)["heading"] == "Accepted heading"
    change = db.get(SiteChangeSet, turn.response_json["change_id"])
    assert change.source == "chat-refinement"
    assert db.scalar(select(SiteRevision).where(SiteRevision.page_id == page.id).order_by(SiteRevision.created_at.desc())).action == "draft"
    builder.undo_change(db, site.id, owner.id, change.id, site.version)
    assert builder.snapshot(db, site)["pages"][page.id]["document"] == before["pages"][page.id]["document"]


def test_reorder_and_remove_are_independent_per_op_decisions(workspace):
    db, owner, site, page = workspace
    before = copy.deepcopy(page.draft_json)
    ids = [block["id"] for block in before["blocks"]]
    desired = copy.deepcopy(before)
    desired["blocks"] = [block for block in desired["blocks"] if block["id"] != "faq"]
    desired["blocks"][0], desired["blocks"][1] = desired["blocks"][1], desired["blocks"][0]
    operations = diff_documents(page.id, before, desired)
    assert [operation["op"] for operation in operations] == ["remove", "reorder"]
    turn, _ = builder.begin_turn(db, site.id, owner.id, uuid4().hex, "Refine structure", page.id, site.version)
    builder.finish_turn(db, site.id, owner.id, turn.id, {"answer": "Review", "operations": [
        {"op": "document", "page_id": page.id, "document": desired},
    ]}, "fixture")
    remove, reorder = turn.response_json["refinement"]["operations"]
    builder.decide_refinement(db, site.id, owner.id, turn.id, "reject", remove["op_id"], site.version)
    builder.decide_refinement(db, site.id, owner.id, turn.id, "accept", reorder["op_id"], site.version)
    after_ids = [block["id"] for block in page.draft_json["blocks"]]
    assert "faq" in after_ids
    assert [block_id for block_id in after_ids if block_id != "faq"] == [ids[1], ids[0]]


def test_compliance_blocks_accept_and_keeps_preview_pending(workspace):
    db, owner, site, page = workspace
    before = copy.deepcopy(page.draft_json)
    hero_id = page.draft_json["blocks"][0]["id"]
    turn, _ = builder.begin_turn(db, site.id, owner.id, uuid4().hex, "Unsafe", page.id, site.version, hero_id)
    builder.finish_turn(db, site.id, owner.id, turn.id, {"answer": "Review", "operations": [{
        "op": "patch", "page_id": page.id, "block_id": hero_id,
        "after": {"heading": "Clinically proven to cure diabetes"},
    }]}, "fixture")
    operation = turn.response_json["refinement"]["operations"][0]
    with pytest.raises(CommerceError, match="banned wording"):
        builder.decide_refinement(db, site.id, owner.id, turn.id, "accept", operation["op_id"], site.version)
    db.rollback()
    assert page.draft_json == before


def test_preview_ttl_cleanup_makes_old_edit_inert(workspace):
    db, owner, site, page = workspace
    before = copy.deepcopy(page.draft_json)
    hero_id = page.draft_json["blocks"][0]["id"]
    turn = _turn(db, owner, site, page, "Headline: Too late", hero_id)
    turn.created_at = datetime.now(UTC) - timedelta(minutes=31)
    db.flush()
    operation = turn.response_json["refinement"]["operations"][0]
    with pytest.raises(CommerceError, match="no longer pending"):
        builder.decide_refinement(db, site.id, owner.id, turn.id, "accept", operation["op_id"], site.version)
    assert turn.response_json["refinement"]["status"] == "expired"
    assert page.draft_json == before


def test_preview_rejects_copy_over_chat_limit(workspace):
    db, owner, site, page = workspace
    hero_id = page.draft_json["blocks"][0]["id"]
    turn, _ = builder.begin_turn(db, site.id, owner.id, uuid4().hex, "Too much copy", page.id, site.version)
    with pytest.raises(CommerceError, match="too long"):
        builder.finish_turn(db, site.id, owner.id, turn.id, {"answer": "Review", "operations": [{
            "op": "patch", "page_id": page.id, "block_id": hero_id, "after": {"body": "x" * 8001},
        }]}, "fixture")
