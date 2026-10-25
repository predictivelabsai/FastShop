"""Draft manager, ownership and CSRF route contracts."""
from types import SimpleNamespace

import pytest
from fasthtml.common import Div, Input, fast_app
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import content, draft_order_routes, draft_orders
from app.models import Base, DraftOrder, Membership, Order, User
from app.services import CommerceError


@pytest.fixture
def workspace(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as db:
        owner, editor, outsider = [User(email=f"draft-{name}@example.test", name=name) for name in ("owner", "editor", "outsider")]
        db.add_all([owner, editor, outsider])
        db.flush()
        site = content.create_site(db, owner.id, "Draft shop", "draft-shop")
        other = content.create_site(db, outsider.id, "Private draft shop", "private-draft-shop")
        db.add(Membership(tenant_id=site.tenant_id, user_id=editor.id, role="editor"))
        secret = draft_orders.create(db, other)
        db.commit()
    monkeypatch.setattr(draft_order_routes, "SessionLocal", sessions)
    app, rt = fast_app(secret_key="draft-tests-only")
    current = {"id": owner.id}
    def csrf(session):
        return Input(type="hidden", name="csrf_token", value="draft-csrf")
    def check_csrf(session, form):
        if form.get("csrf_token") != "draft-csrf":
            raise CommerceError("Invalid CSRF token.")
    draft_order_routes.register_draft_order_routes(rt, lambda session: current["id"], csrf, check_csrf,
        lambda title, *children: Div(*children), lambda exc: Response(str(exc), status_code=400))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sessions=sessions, site=site, other=other,
            owner=owner, editor=editor, current=current, secret=secret)
    engine.dispose()


def test_draft_routes_create_custom_save_invoice_complete_and_retry(workspace):
    w = workspace
    base = f"/admin/sites/{w.site.id}/drafts"
    response = w.client.post(base, data={"csrf_token": "draft-csrf"}, follow_redirects=False)
    assert response.status_code == 303
    path = response.headers["location"]
    assert "Manual payment only" in w.client.get(path).text
    response = w.client.post(path+"/lines", data={"csrf_token": "draft-csrf", "version": 1,
        "title": "Personal consultation", "unit_price": "15.25", "quantity": 2}, follow_redirects=False)
    assert response.status_code == 303, response.text
    response = w.client.post(path, data={"csrf_token": "draft-csrf", "version": 2,
        "customer_email": "draft-buyer@example.test", "customer_name": "Draft buyer",
        "discount": "0.50", "shipping": "1.00", "line1": "12 Test Road", "country": "GB"}, follow_redirects=False)
    assert response.status_code == 303, response.text
    assert "Personal consultation" in w.client.get(path).text
    assert "draft-buyer@example.test" in w.client.get(base+"?q=draft-buyer").text or "Draft buyer" in w.client.get(base+"?q=draft-buyer").text
    response = w.client.post(path+"/invoice", data={"csrf_token": "draft-csrf", "version": 3}, follow_redirects=False)
    assert response.status_code == 303, response.text
    assert w.client.post(path+"/complete", data={"csrf_token": "draft-csrf"}).status_code == 400
    for _ in range(2):
        response = w.client.post(path+"/complete", data={"csrf_token": "draft-csrf", "mark_as_paid": "on"}, follow_redirects=False)
        assert response.status_code == 303, response.text
    assert "View order" in w.client.get(path).text
    with w.sessions() as db:
        assert len(list(db.scalars(select(Order)))) == 1
        row = db.scalar(select(DraftOrder).where(DraftOrder.site_id == w.site.id))
        assert db.get(Order, row.order_id).total_minor == 3100
    assert w.client.get(base+"/variants?q=test").json() == {"items": []}
    assert w.client.get(base+"/customers?q=draft-buyer").json()["items"]


def test_routes_deny_tenant_editor_and_csrf(workspace):
    w = workspace
    base = f"/admin/sites/{w.site.id}"
    for path in ("drafts", "drafts/variants", "drafts/customers", "abandoned-checkouts"):
        assert w.client.get(f"/admin/sites/{w.other.id}/"+path).status_code == 400
    assert w.client.get(base+"/drafts/"+w.secret.id).status_code == 400
    assert w.client.post(base+"/drafts", data={}).status_code == 400
    with w.sessions() as db:
        row = draft_orders.create(db, w.site)
        db.commit()
    for suffix in ("", "/lines", "/invoice", "/complete", "/lines/missing/remove"):
        assert w.client.post(base+"/drafts/"+row.id+suffix, data={}).status_code == 400
    w.current["id"] = w.editor.id
    for path in ("drafts", "drafts/variants", "drafts/customers", "abandoned-checkouts"):
        assert w.client.get(base+"/"+path).status_code == 400
    assert w.client.post(base+"/drafts", data={"csrf_token": "draft-csrf"}).status_code == 400


def test_abandoned_empty_state_and_invalid_minor_inputs(workspace):
    w = workspace
    response = w.client.get(f"/admin/sites/{w.site.id}/abandoned-checkouts")
    assert response.status_code == 200
    assert "Tracking starts when checkout records exist" in response.text
    for value in ("-1", "1.005", "NaN", "Infinity", "abc"):
        with pytest.raises(CommerceError):
            draft_order_routes.minor(value)
    assert draft_order_routes.minor("12.34") == 1234
