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

from app import admin_gap_routes, content
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
        yield SimpleNamespace(client=client, site=site, other=other, owner=owner, editor=editor, actor=actor, sessions=sessions)
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
