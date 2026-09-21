import re
from uuid import uuid4

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app import content
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Site, SiteBuilderTurn, SiteChangeSet, User
from app.services import CommerceError
from app.site_theme import PRESETS


@pytest.fixture
def editor():
    client = TestClient(app)
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    assert client.post("/login", data={"csrf_token": token, "email": settings.admin_email, "password": settings.admin_password}).status_code == 200
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, owner.id, "Route test", "build-" + uuid4().hex[:10])
        page = content.site_pages(db, site)[0]
        db.commit()
    return client, site, page, token


def message_form(site, page, token):
    return {"csrf_token": token, "version": site.version, "page_id": page.id, "command_id": uuid4().hex, "prompt": "Warm"}


def test_builder_preview_and_classical_share_draft(editor, monkeypatch):
    import app.site_builder_routes as routes
    monkeypatch.setattr(routes, "respond", lambda *args: ({"answer": "Design updated", "operations": [{"op": "theme", "values": PRESETS["warm"]}]}, "fixture"))
    client, site, page, token = editor
    base = f"/admin/sites/{site.id}/build"
    assert "Live draft preview" in client.get(base).text
    assert TestClient(app).get(base).status_code == 400
    form = message_form(site, page, token)
    assert client.post(base + "/message", data=form | {"csrf_token": "wrong"}).status_code == 400
    result = client.post(base + "/message", data=form)
    assert result.status_code == 200 and "Design updated" in result.text
    assert client.post(base + "/message", data=form).status_code == 200
    assert "#26543d" in client.get(base + "?view=design").text
    with SessionLocal() as db:
        saved = db.get(Site, site.id)
        assert saved.settings_json["design"] == PRESETS["warm"]
        assert "design" not in saved.published_settings_json
        assert len(list(db.scalars(select(SiteBuilderTurn).where(SiteBuilderTurn.site_id == site.id)))) == 1


def test_builder_failure_and_secrets_do_not_modify_draft(editor, monkeypatch):
    import app.site_builder_routes as routes
    def unavailable(*args):
        raise CommerceError("Provider unavailable")
    monkeypatch.setattr(routes, "respond", unavailable)
    client, site, page, token = editor
    url = f"/admin/sites/{site.id}/build/message"
    form = message_form(site, page, token)
    assert client.post(url, data=form).status_code == 400
    assert client.post(url, data=form | {"command_id": uuid4().hex, "prompt": "sk_test_do_not_store_this"}).status_code == 400
    with SessionLocal() as db:
        turns = list(db.scalars(select(SiteBuilderTurn).where(SiteBuilderTurn.site_id == site.id)))
        assert len(turns) == 1 and turns[0].status == "failed"
        assert db.get(Site, site.id).settings_json == site.settings_json


def test_classical_settings_save_is_reversible_from_builder(editor):
    client, site, page, token = editor
    base = f"/admin/sites/{site.id}"
    assert client.post(base + "/settings", data={"csrf_token": token, "version": site.version,
        "name": "Manual name", "tagline": "Manual tagline", "action": "draft"}).status_code == 200
    with SessionLocal() as db:
        saved = db.get(Site, site.id)
        change = db.scalar(select(SiteChangeSet).where(SiteChangeSet.site_id == site.id))
        version, change_id = saved.version, change.id
        assert change.source == "classical" and saved.settings_json["tagline"] == "Manual tagline"
    response = client.post(base + "/build/undo", data={"csrf_token": token, "version": version, "change_id": change_id})
    assert response.status_code == 200
    with SessionLocal() as db:
        assert db.get(Site, site.id).settings_json == site.settings_json


def test_cancel_route_is_csrf_protected_and_late_response_cannot_write(editor):
    from app import site_builder_services as builder
    client, site, page, token = editor
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        turn, _ = builder.begin_turn(db, site.id, owner.id, uuid4().hex, "Warm", page.id, site.version)
        command = turn.command_id
        db.commit()
    url = f"/admin/sites/{site.id}/build/cancel"
    form = {"csrf_token": token, "command_id": command, "page_id": page.id}
    assert client.post(url, data=form | {"csrf_token": "bad"}).status_code == 400
    assert client.post(url, data=form).status_code == 200
    assert client.post(url, data=form).status_code == 400
    with SessionLocal() as db:
        saved = db.scalar(select(SiteBuilderTurn).where(SiteBuilderTurn.site_id == site.id))
        assert saved.status == "cancelled"
        assert db.get(Site, site.id).settings_json == site.settings_json


def test_merchant_proposal_requires_csrf_and_explicit_confirmation(editor, monkeypatch):
    import app.site_builder_routes as routes
    from app import commerce, site_samples
    client, site, page, token = editor
    with SessionLocal() as db:
        saved = db.get(Site, site.id)
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site_samples.seed_samples(db, saved, owner.id)
        db.commit()
        site.version = saved.version
    monkeypatch.setattr(routes, "respond", lambda *args: ({"answer": "Review shipping", "operations": [],
        "proposals": [{"kind": "merchant", "values": {"shipping_minor": 1600}}]}, "fixture"))
    base = f"/admin/sites/{site.id}/build"
    result = client.post(base + "/message", data=message_form(site, page, token))
    assert result.status_code == 200 and "Review merchant changes" in result.text
    with SessionLocal() as db:
        turn = db.scalar(select(SiteBuilderTurn).where(SiteBuilderTurn.site_id == site.id))
        assert commerce.settings_for(db, db.get(Site, site.id)).shipping_minor == 1000
        form = {"csrf_token": token, "turn_id": turn.id, "decision": "approve", "page_id": page.id}
    assert client.post(base + "/review", data=form).status_code == 400
    assert client.post(base + "/review", data=form | {"confirmed": "on", "csrf_token": "bad"}).status_code == 400
    assert client.post(base + "/review", data=form | {"confirmed": "on"}).status_code == 200
    assert client.post(base + "/review", data=form | {"confirmed": "on"}).status_code == 200
    with SessionLocal() as db:
        saved = db.get(Site, site.id)
        assert commerce.settings_for(db, saved).shipping_minor == 1600
        assert saved.settings_json["sample_fields"]["shipping_minor"]["source"] == "ai_proposed"
        config = commerce.settings_for(db, saved)
        config_version = config.version
    blocked = client.post(f"/admin/sites/{site.id}/commerce", data={"csrf_token": token,
        "version": config_version, "mode": "sandbox", "states": "CA", "origin_country": "EE",
        "shipping_minor": "1600", "free_shipping_threshold_minor": "7500"})
    assert blocked.status_code == 400 and "Review merchant details" in blocked.text
    with SessionLocal() as db:
        assert commerce.settings_for(db, db.get(Site, site.id)).mode == "disabled"
