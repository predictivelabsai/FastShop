"""CSRF, roles and tenant/user isolation of the added management routes."""

import json
from types import SimpleNamespace

import pytest
from fasthtml.common import Div, Input, fast_app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import admin_gap_routes, admin_gap_ui, content
from app.models import Base, Membership, User
from app.services import CommerceError


@pytest.fixture
def routes(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as db:
        owner, editor, stranger = [User(email=email + "@example.test") for email in ("gap-owner", "gap-editor", "gap-stranger")]
        db.add_all([owner, editor, stranger])
        db.flush()
        site = content.create_site(db, owner.id, "Gap shop", "gap-shop")
        other = content.create_site(db, stranger.id, "Other", "gap-other")
        db.add(Membership(user_id=editor.id, tenant_id=site.tenant_id, role="editor"))
        db.commit()
    monkeypatch.setattr(admin_gap_routes, "SessionLocal", sessions)
    app, rt = fast_app(secret_key="test-only")
    actor = {"id": owner.id}
    def check(session, form):
        if form.get("csrf_token") != "token":
            raise CommerceError("Invalid CSRF.")
    admin_gap_routes.register_admin_gap_routes(rt, lambda session: actor["id"],
        lambda session: Input(name="csrf_token", value="token", type="hidden"), check,
        lambda title, *children: Div(*children), lambda exc: Response(str(exc), status_code=400))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, site=site, other=other, owner=owner, editor=editor, stranger=stranger, actor=actor, sessions=sessions)
    engine.dispose()


def test_location_routes_roles_csrf_scoping_and_audit(routes):
    w = routes
    base = f"/admin/sites/{w.site.id}"
    payload = dict(csrf_token="token", name="Test location", country_code="US", is_active="on", fulfils_online_orders="on")
    assert w.client.post(base + "/settings/locations", data={"name": "Denied"}).status_code == 400
    assert w.client.post(base + "/settings/locations", data=payload).status_code == 200
    assert "Test location" in w.client.get(base + "/settings/locations").text
    assert "Location created." in w.client.get(base + "/activity").text
    w.actor["id"] = w.editor.id
    assert w.client.get(base + "/settings/locations").status_code == 200
    assert w.client.post(base + "/settings/locations", data=payload).status_code == 400
    for path in ("activity", "settings/locations"):
        assert w.client.get(f"/admin/sites/{w.other.id}/" + path).status_code == 400


def test_saved_view_routes_personal_scope_and_csrf(routes):
    w = routes
    base = f"/admin/sites/{w.site.id}/views/products"
    payload = dict(csrf_token="token", name="My drafts", filters=json.dumps({"status": "draft"}), sort="title", columns="Product")
    assert w.client.post(base, data={"name": "Denied"}).status_code == 400
    response = w.client.post(base, data=payload, follow_redirects=False)
    assert response.status_code == 303 and "status=draft" in response.headers["location"]
    from app.admin_views import views_for
    with w.sessions() as db:
        row = views_for(db, w.site, w.owner.id, "products")[0]
    w.actor["id"] = w.editor.id
    assert w.client.post(base + "/manage", data=dict(csrf_token="token", view_id=row.id, action="delete")).status_code == 400
    assert w.client.post(base + "/columns", data=dict(csrf_token="token", columns="Product"), follow_redirects=False).status_code == 303
    assert w.client.post(f"/admin/sites/{w.other.id}/views/products", data=payload).status_code == 400


def test_activity_display_is_readable_compact_and_tenant_scoped(routes):
    from datetime import datetime

    from fastcore.xml import to_xml

    from app.admin_activity import record_activity

    w = routes
    with w.sessions() as db:
        owner = db.get(User, w.owner.id)
        owner.name = "Alex"
        for index in range(7):
            row = record_activity(db, w.site, owner.id, "gift_card", "card-id",
                "balance_adjusted", f"Gift card balance changed {index}.")
            row.created_at = datetime(2026, 10, 25, 12, index, 30, 123456)
        record_activity(db, w.site, None,
            "location", "warehouse", "updated", "Location updated.")
        db.commit()
        preview = to_xml(admin_gap_ui.activity_card(db, w.site, "gift_card", "card-id"))
        assert preview.count("Gift card balance changed") == 5
        assert "Alex" in preview and "25 Oct 2026 at 12:06 UTC" in preview
        assert "123456" not in preview and owner.id not in preview
        assert "<table" not in preview and "View activity" in preview
        # An actor outside this store must not reveal an unrelated account.
        record_activity(db, w.site, w.stranger.id, "location", "x", "updated", "Location updated.")
        record_activity(db, w.site, w.editor.id, "location", "x", "updated", "Location updated.")
        db.commit()
    response = w.client.get(f"/admin/sites/{w.site.id}/activity")
    assert response.status_code == 200
    assert "Balance adjusted" in response.text and "Gift card" in response.text
    assert "Alex" in response.text and "balance_adjusted" not in response.text
    assert w.owner.id not in response.text and "123456" not in response.text
    assert "Former team member" in response.text and "System" in response.text
    assert w.stranger.email not in response.text and w.stranger.id not in response.text
    assert w.editor.email in response.text
