import re
from uuid import uuid4

from sqlalchemy import select
from starlette.testclient import TestClient

from app import content
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import DemoWorkspace, User


def test_private_demo_requires_membership_csrf_and_explicit_start():
    client = TestClient(app)
    login = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login.text).group(1)
    assert client.post("/login", data={"csrf_token": token, "email": settings.admin_email, "password": settings.admin_password}).status_code == 200
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, owner.id, "Demo route", "demo-" + uuid4().hex[:10])
        stranger = User(email=uuid4().hex + "@example.test", name="Stranger")
        db.add(stranger)
        db.flush()
        foreign = content.create_site(db, stranger.id, "Foreign demo", "demo-" + uuid4().hex[:10])
        db.commit()
    base = f"/admin/sites/{site.id}/demo"
    assert TestClient(app).get(base).status_code == 400
    assert client.get(f"/admin/sites/{foreign.id}/demo").status_code == 400
    response = client.get(base)
    assert response.status_code == 200 and "Start private demo" in response.text
    with SessionLocal() as db:
        assert not db.scalar(select(DemoWorkspace).where(DemoWorkspace.site_id == site.id))
    assert client.post(base + "/start", data={"csrf_token": "bad"}).status_code == 400
    assert client.post(base + "/start", data={"csrf_token": token}).status_code == 200
    with SessionLocal() as db:
        workspace = db.scalar(select(DemoWorkspace).where(DemoWorkspace.site_id == site.id))
        assert workspace.version == 1
    form = {"csrf_token": token, "command_id": uuid4().hex, "version": 1, "action": "cart",
            "sku": "sample-cup", "quantity": "1", "view": "shop"}
    assert client.post(base + "/action", data=form | {"csrf_token": "bad"}).status_code == 400
    assert client.post(base + "/action", data=form).status_code == 200
    assert client.post(base + "/action", data=form).status_code == 200
    with SessionLocal() as db:
        workspace = db.scalar(select(DemoWorkspace).where(DemoWorkspace.site_id == site.id))
        assert len(workspace.state_json["cart"]) == 1 and workspace.version == 2
    assert "No payment, real email" in client.get(base).text
