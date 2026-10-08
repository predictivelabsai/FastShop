"""Shared builder mutations retain localized content and historical undo."""

import copy
import re
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app import content
from app import site_builder_services as builder
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Base, Site, User
from app.services import CommerceError


@pytest.fixture
def block_workspace():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="block-builder@example.test", name="Owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Blocks", "block-builder")
        page = content.site_pages(db, site)[0]
        page.draft_json = {"version": 1, "title": "Localized", "blocks": [
            {"id": "hero", "version": 1, "type": "hero", "heading": {"en": "Original", "et": "Algne"}}]}
        db.commit()
        yield db, owner, site, page


def test_chat_changes_default_locale_and_undo_accepts_legacy_history(block_workspace):
    db, owner, site, page = block_workspace
    change = builder.apply_operations(db, site.id, owner.id, builder.snapshot(db, site), [
        {"op": "section", "page_id": page.id, "section_id": "hero", "values": {"heading": "Changed"}}])
    assert page.draft_json["blocks"][0]["heading"] == {"en": "Changed", "et": "Algne"}
    # Simulate revisions written before the canonical read adapter shipped.
    for field in ("before_json", "after_json"):
        state = copy.deepcopy(getattr(change, field))
        for item in state["pages"].values():
            document = item["document"]
            document["sections"] = document.pop("blocks")
            document.pop("version")
        setattr(change, field, state)
    builder.undo_change(db, site.id, owner.id, change.id, site.version)
    assert page.draft_json["blocks"][0]["heading"] == {"en": "Original", "et": "Algne"}


def test_chat_limits_every_translation(block_workspace):
    db, owner, site, page = block_workspace
    with pytest.raises(CommerceError, match="too long"):
        builder.apply_operations(db, site.id, owner.id, builder.snapshot(db, site), [
            {"op": "section", "page_id": page.id, "section_id": "hero",
             "values": {"heading": {"en": "Short", "et": "x" * 8001}}}])


def test_classical_nested_item_deletion_retains_correct_translations():
    client = TestClient(app)
    response = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
    assert client.post("/login", data={"csrf_token": token, "email": settings.admin_email,
        "password": settings.admin_password}).status_code == 200
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, owner.id, "Localized editor", "localized-" + uuid4().hex[:10])
        page = content.site_pages(db, site)[0]
        page.draft_json = {"version": 1, "title": "FAQ", "blocks": [{"id": "faq", "type": "faq", "items": [
            {"heading": {"en": "Remove", "et": "Eemalda"}, "body": {"en": "First", "et": "Esimene"}},
            {"heading": {"en": "Keep", "et": "Hoia"}, "body": {"en": "Second", "et": "Teine"}},
        ]}]}
        db.commit()
        site_id, page_id, version = site.id, page.id, page.version
    url = f"/admin/sites/{site_id}/pages/{page_id}"
    assert "Keep" in client.get(url).text
    response = client.post(url, data={"csrf_token": token, "version": version, "title": "FAQ",
        "section_order": "faq", "section_0_item_0_remove": "on",
        "section_0_item_1_heading": "Kept", "section_0_item_1_body": "Updated"})
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        site = db.get(Site, site_id)
        saved = content.site_page(db, site, page_id).draft_json["blocks"][0]["items"]
        assert len(saved) == 1
        assert saved[0]["heading"] == {"en": "Kept", "et": "Hoia"}
        assert saved[0]["body"] == {"en": "Updated", "et": "Teine"}
