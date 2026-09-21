import copy
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import content
from app import site_builder_services as builder
from app.integrations.site_builder_llm import guided
from app.models import Base, SiteBuilderTurn, SiteChangeSet, User
from app.services import CommerceError
from app.site_theme import PRESETS, theme_style, validate_theme


@pytest.fixture
def workspace():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner, outsider = User(email="owner@example.test", name="Owner"), User(email="outsider@example.test", name="Other")
        db.add_all([owner, outsider])
        db.flush()
        site = content.create_site(db, owner.id, "Builder test", "builder-test")
        other = content.create_site(db, outsider.id, "Other", "other-site")
        db.commit()
        yield db, owner, outsider, site, other


def test_theme_rejects_css_injection_and_preserves_legacy():
    assert theme_style({}) == ""
    assert "--store-accent:#26543d" in theme_style({"design": PRESETS["warm"]})
    for value in ({"accent": "red;background:url(https://bad.test)"}, {"font": "url(bad)"}, {"other": "x"}, {"radius": 2}):
        with pytest.raises(CommerceError):
            validate_theme(value)


def test_navigation_uses_safe_shared_draft_and_undo(workspace):
    db, owner, _, site, _ = workspace
    expected = builder.snapshot(db, site)
    links = [{"label": "Home", "path": "/"}, {"label": "Our story", "path": "/pages/about"}]
    change = builder.apply_operations(db, site.id, owner.id, expected, [{"op": "navigation", "items": links}])
    assert site.settings_json["navigation"] == links
    assert site.published_settings_json.get("navigation") != links
    builder.undo_change(db, site.id, owner.id, change.id, site.version)
    assert site.settings_json == expected["settings"]
    with pytest.raises(CommerceError):
        builder.apply_operations(db, site.id, owner.id, builder.snapshot(db, site),
            [{"op": "navigation", "items": [{"label": "Unsafe", "path": "javascript:alert(1)"}]}])


def test_design_and_copy_share_draft_and_undo(workspace):
    db, owner, _, site, _ = workspace
    expected = builder.snapshot(db, site)
    page = content.site_pages(db, site)[0]
    published = copy.deepcopy(site.published_settings_json)
    change = builder.apply_operations(db, site.id, owner.id, expected, [
        {"op": "theme", "values": PRESETS["warm"]},
        {"op": "section", "page_id": page.id, "section_id": page.draft_json["sections"][0]["id"], "values": {"heading": "A quieter everyday"}}])
    db.commit()
    assert site.settings_json["design"]["accent"] == "#26543d"
    assert page.draft_json["sections"][0]["heading"] == "A quieter everyday"
    assert site.published_settings_json == published and page.published_json is None
    builder.undo_change(db, site.id, owner.id, change.id, site.version)
    assert site.settings_json == expected["settings"]
    assert page.draft_json == expected["pages"][page.id]["document"]


def test_cross_tenant_commands_and_undo_denied(workspace):
    db, owner, outsider, site, other = workspace
    with pytest.raises(CommerceError):
        builder.apply_operations(db, site.id, outsider.id, builder.snapshot(db, site), [])
    other_page = content.site_pages(db, other)[0]
    with pytest.raises(CommerceError):
        builder.apply_operations(db, site.id, owner.id, builder.snapshot(db, site), [
            {"op": "section", "page_id": other_page.id, "section_id": other_page.draft_json["sections"][0]["id"], "values": {"heading": "Intrusion"}}])
    db.rollback()
    assert "Intrusion" not in str(other_page.draft_json)


def test_stale_ai_never_overwrites_manual_page_edit(workspace):
    db, owner, _, site, _ = workspace
    expected = builder.snapshot(db, site)
    page = content.site_pages(db, site)[0]
    doc = copy.deepcopy(page.draft_json)
    doc["title"] = "Manual edit"
    content.save_page(db, site, page.id, owner.id, doc, page.version)
    db.commit()
    with pytest.raises(CommerceError, match="changed"):
        builder.apply_operations(db, site.id, owner.id, expected, [{"op": "theme", "values": PRESETS["warm"]}])
    assert "design" not in site.settings_json


@pytest.mark.parametrize("operation", [{"op": "publish"}, {"op": "charge"}, {"op": "theme", "values": {"accent": "url(bad)"}}, {"op": "brand", "values": {"shipping_minor": 0}}])
def test_rejected_operations_leave_no_changes(workspace, operation):
    db, owner, _, site, _ = workspace
    expected = builder.snapshot(db, site)
    with pytest.raises(CommerceError):
        builder.apply_operations(db, site.id, owner.id, expected, [operation])
    db.rollback()
    assert builder.snapshot(db, site) == expected
    assert not db.scalar(select(SiteChangeSet.id))


def test_durable_turn_idempotency_and_guided_preset(workspace):
    db, owner, _, site, _ = workspace
    page = content.site_pages(db, site)[0]
    command = uuid4().hex
    turn, fresh = builder.begin_turn(db, site.id, owner.id, command, "Warm", page.id, site.version)
    assert fresh
    db.commit()
    duplicate, fresh = builder.begin_turn(db, site.id, owner.id, command, "Warm", page.id, site.version)
    assert duplicate.id == turn.id and not fresh
    reply = guided("Warm", turn.context_json)
    builder.finish_turn(db, site.id, owner.id, turn.id, reply, "guided")
    db.commit()
    assert turn.status == "complete" and site.settings_json["design"] == PRESETS["warm"]
    assert len(list(db.scalars(select(SiteBuilderTurn)))) == 1
    with pytest.raises(CommerceError, match="no longer pending"):
        builder.finish_turn(db, site.id, owner.id, turn.id, reply, "guided")


def test_create_page_then_undo(workspace):
    db, owner, _, site, _ = workspace
    count = len(content.site_pages(db, site))
    change = builder.apply_operations(db, site.id, owner.id, builder.snapshot(db, site), [
        {"op": "create_page", "title": "Our values", "path": "/pages/values"}])
    assert len(content.site_pages(db, site)) == count + 1
    builder.undo_change(db, site.id, owner.id, change.id, site.version)
    assert len(content.site_pages(db, site)) == count


def test_no_change_request_cannot_hide_version_conflict(workspace):
    db, owner, _, site, _ = workspace
    expected = builder.snapshot(db, site)
    site.version += 1
    db.commit()
    with pytest.raises(CommerceError, match="changed"):
        builder.apply_operations(db, site.id, owner.id, expected, [])


def test_selected_section_is_owned_and_guided_edit_targets_it(workspace):
    db, owner, _, site, other = workspace
    page = content.site_pages(db, site)[0]
    foreign = content.site_pages(db, other)[0].draft_json["sections"][0]["id"]
    with pytest.raises(CommerceError, match="belonging"):
        builder.begin_turn(db, site.id, owner.id, uuid4().hex, "Headline: New", page.id, site.version, foreign)
    section = page.draft_json["sections"][0]["id"]
    turn, _ = builder.begin_turn(db, site.id, owner.id, uuid4().hex, "Headline: New", page.id, site.version, section)
    reply = guided(turn.prompt, turn.context_json)
    assert reply["operations"][0]["section_id"] == section
    builder.finish_turn(db, site.id, owner.id, turn.id, reply, "guided")
    assert page.draft_json["sections"][0]["heading"] == "New"


def test_cancelled_turn_cannot_apply_a_late_response(workspace):
    db, owner, _, site, _ = workspace
    page = content.site_pages(db, site)[0]
    before = builder.snapshot(db, site)
    turn, _ = builder.begin_turn(db, site.id, owner.id, uuid4().hex, "Warm", page.id, site.version)
    db.commit()
    assert builder.end_pending_turn(db, site.id, owner.id, turn.command_id)
    db.commit()
    db.expire_all()
    with pytest.raises(CommerceError, match="no longer pending"):
        builder.finish_turn(db, site.id, owner.id, turn.id, guided("Warm", turn.context_json), "guided")
    assert not builder.end_pending_turn(db, site.id, owner.id, turn.command_id, failed=True)
    assert db.get(SiteBuilderTurn, turn.id).status == "cancelled"
    assert builder.snapshot(db, site) == before


def test_cannot_cancel_another_merchants_request(workspace):
    from app.models import Membership
    db, owner, outsider, site, _ = workspace
    db.add(Membership(tenant_id=site.tenant_id, user_id=outsider.id, role="editor"))
    db.flush()
    page = content.site_pages(db, site)[0]
    turn, _ = builder.begin_turn(db, site.id, owner.id, uuid4().hex, "Warm", page.id, site.version)
    db.commit()
    with pytest.raises(CommerceError, match="not yours"):
        builder.end_pending_turn(db, site.id, outsider.id, turn.command_id)
    assert db.get(SiteBuilderTurn, turn.id).status == "pending"


def test_replay_cannot_change_the_target(workspace):
    db, owner, _, site, _ = workspace
    page = content.site_pages(db, site)[0]
    turn, _ = builder.begin_turn(db, site.id, owner.id, uuid4().hex, "Warm", page.id, site.version)
    db.commit()
    with pytest.raises(CommerceError, match="already in use"):
        builder.begin_turn(db, site.id, owner.id, turn.command_id, "Warm", page.id, site.version, page.draft_json["sections"][0]["id"])
