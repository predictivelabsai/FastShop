"""Block contracts, locale preservation, and legacy persistence compatibility."""

import copy
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.content import create_site, site_pages
from app.models import Base, User
from app.services import CommerceError
from app.site_blocks import (
    add_block,
    default_locale,
    get_block,
    normalize_document,
    patch_block,
    remove_block,
    reorder_blocks,
    resolve_document,
)


def legacy_document():
    return {"title": "Our story", "description": "About us", "sections": [
        {"type": "hero", "heading": "Welcome", "body": "Hello", "id": "intro"},
        {"type": "faq", "items": [{"heading": "Why?", "body": "Because."}]},
    ]}


def test_legacy_normalization_is_stable_idempotent_and_preserves_metadata():
    source = legacy_document()
    before = copy.deepcopy(source)
    normalized = normalize_document(source)
    assert source == before
    assert normalized["version"] == 1 and "sections" not in normalized
    assert normalized["title"] == source["title"]
    assert normalized["description"] == source["description"]
    assert normalized["blocks"][0]["id"] == "intro"
    assert normalized["blocks"][1]["id"]
    assert normalized == normalize_document(source) == normalize_document(normalized)
    normalized["blocks"][1]["items"][0]["body"] = "Changed"
    assert source == before


def test_bare_legacy_section_list_normalizes():
    result = normalize_document(legacy_document()["sections"])
    assert result["version"] == 1
    assert len(result["blocks"]) == 2


@pytest.mark.parametrize("block", [
    {"id": "bad", "type": "raw_html"},
    {"id": "bad", "type": "hero", "heading": 42},
    {"id": "bad", "type": "hero", "heading": {"en": 42}},
    {"id": "bad", "type": "faq", "items": "wrong"},
    {"id": "bad", "type": "faq", "items": ["wrong"]},
    {"id": "bad", "type": "faq", "items": [{"body": "Missing question"}]},
    {"id": "bad", "type": "research", "items": [{"heading": "Missing URL"}]},
    {"id": "bad", "type": "text", "hidden": "false"},
    {"id": "bad", "type": "text", "image": "javascript:alert(1)"},
    {"id": "bad", "type": "text", "image": {"en": "/safe.png", "et": "javascript:alert(1)"}},
])
def test_invalid_types_nested_fields_and_localized_urls_are_rejected(block):
    with pytest.raises(CommerceError):
        normalize_document({"version": 1, "blocks": [block]})


def test_duplicate_block_ids_and_unknown_document_versions_are_rejected():
    with pytest.raises(CommerceError):
        normalize_document({"version": 1, "blocks": [
            {"id": "same", "type": "text"}, {"id": "same", "type": "hero"},
        ]})
    with pytest.raises(CommerceError):
        normalize_document({"version": 999, "blocks": []})


def test_locale_edit_keeps_other_translations_and_resolves_nested_content():
    document = normalize_document({"title": "FAQ", "sections": [{
        "id": "faq", "type": "faq", "heading": {"en": "Questions", "et": "Küsimused"},
        "items": [{"heading": {"en": "Why?", "et": "Miks?"}, "body": "Plain answer"}],
    }]})
    before = copy.deepcopy(document)
    changed = patch_block(document, "faq", {"heading": "New questions"}, locale="en")
    assert get_block(changed, "faq")["heading"] == {"en": "New questions", "et": "Küsimused"}
    resolved = resolve_document(changed, locale="et")
    assert resolved["blocks"][0]["heading"] == "Küsimused"
    assert resolved["blocks"][0]["items"][0] == {"heading": "Miks?", "body": "Plain answer"}
    assert document == before


def test_block_operations_are_pure_and_preserve_stable_identifiers():
    document = normalize_document(legacy_document())
    before = copy.deepcopy(document)
    ids = [block["id"] for block in document["blocks"]]
    assert [block["id"] for block in reorder_blocks(document, ids[::-1])["blocks"]] == ids[::-1]
    added = add_block(document, {"id": "footer", "type": "text", "body": "Goodbye"})
    assert get_block(added, "footer")["body"] == "Goodbye"
    assert remove_block(added, "footer") == document
    assert get_block(patch_block(document, "intro", {"body": "Updated"}), "intro")["id"] == "intro"
    found = get_block(document, "intro")
    found["body"] = "Mutated lookup result"
    assert document == before


def test_invalid_targeting_is_rejected_without_changing_document():
    document = normalize_document(legacy_document())
    before = copy.deepcopy(document)
    for operation in (
        lambda: get_block(document, "missing"),
        lambda: patch_block(document, "missing", {"heading": "Oops"}),
        lambda: remove_block(document, "missing"),
        lambda: reorder_blocks(document, ["intro"]),
        lambda: reorder_blocks(document, ["intro", "intro"]),
        lambda: add_block(document, {"id": "intro", "type": "text"}),
    ):
        with pytest.raises(CommerceError):
            operation()
    assert document == before


def test_site_default_locale_uses_draft_or_published_settings_with_english_fallback():
    site = SimpleNamespace(settings_json={}, published_settings_json={})
    assert default_locale(site) == "en"
    site.settings_json = {"default_locale": "et"}
    site.published_settings_json = {"default_locale": "de"}
    assert default_locale(site, preview=True) == "et"
    assert default_locale(site, preview=False) == "de"


def test_legacy_rows_normalize_on_read_without_rewriting_database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="blocks@example.test", name="Blocks")
        db.add(user)
        db.flush()
        site = create_site(db, user.id, "Block site", "block-site")
        page = site_pages(db, site)[0]
        legacy = legacy_document()
        raw = json.dumps(legacy)
        db.execute(text("UPDATE site_pages SET draft_json=:payload, published_json=:payload WHERE id=:id"),
                   {"payload": raw, "id": page.id})
        db.commit()
        db.expire(page)
        assert page.draft_json == normalize_document(legacy)
        assert page.published_json == page.draft_json
        stored = db.execute(text("SELECT draft_json FROM site_pages WHERE id=:id"), {"id": page.id}).scalar_one()
        assert json.loads(stored) == legacy
        page.draft_json = patch_block(page.draft_json, "intro", {"body": "Edited"})
        db.commit()
        stored = db.execute(text("SELECT draft_json FROM site_pages WHERE id=:id"), {"id": page.id}).scalar_one()
        assert "sections" not in json.loads(stored)
        assert json.loads(stored)["blocks"][0]["body"] == "Edited"
    engine.dispose()
