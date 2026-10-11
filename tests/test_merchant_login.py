import base64
import json
import re
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app import auth, main
from app.db import SessionLocal
from app.models import CommerceMail, Membership, OnboardingState, Site, User


def session(client):
    return json.loads(base64.b64decode(client.cookies["session_"].split(".")[0]))


@pytest.mark.parametrize("mode", ["login", "signup"])
@pytest.mark.parametrize("existing", [False, True])
def test_google_orphan_gets_workspace(monkeypatch, mode, existing):
    email = uuid4().hex + "@example.test"
    if existing:
        with SessionLocal() as db:
            user = User(email=email, name="Orphan")
            db.add(user)
            db.commit()
            original_id = user.id
    monkeypatch.setattr(main, "settings", replace(main.settings, signup_open=True))
    monkeypatch.setattr(auth, "google_enabled", lambda: True)
    monkeypatch.setattr(auth, "exchange_code", lambda code, verifier: {
        "email": email, "name": "Google Merchant"
    })
    client = TestClient(main.app)
    client.get("/auth/google" + ("?signup=1" if mode == "signup" else ""), follow_redirects=False)
    before = session(client)
    response = client.get("/auth/google/callback", params={
        "code": "fixture", "state": before["google_oauth_state"]
    }, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/onboarding/")
    after = session(client)
    assert after["role"] == "admin"
    assert after["csrf_token"]
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if existing:
            assert user.id == original_id
        assert user.email_verified_at is not None
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
        site = db.scalar(select(Site).where(Site.tenant_id == membership.tenant_id))
        assert response.headers["location"] == "/admin/onboarding/" + site.id
        assert db.scalar(select(OnboardingState.id).where(
            OnboardingState.site_id == site.id, OnboardingState.tenant_id == site.tenant_id))
        assert not db.scalar(select(CommerceMail.id).where(
            CommerceMail.site_id == site.id, CommerceMail.tenant_id == site.tenant_id))


@pytest.mark.parametrize("existing", [False, True])
def test_google_without_workspace_when_signup_closed(monkeypatch, existing):
    email = uuid4().hex + "@example.test"
    if existing:
        with SessionLocal() as db:
            db.add(User(email=email, name="Orphan"))
            db.commit()
    monkeypatch.setattr(main, "settings", replace(main.settings, signup_open=False))
    from app import marketing_routes
    monkeypatch.setattr(marketing_routes, "settings", replace(main.settings, signup_open=False))
    monkeypatch.setattr(auth, "google_enabled", lambda: True)
    monkeypatch.setattr(auth, "exchange_code", lambda code, verifier: {
        "email": email, "name": "Google Merchant"
    })
    client = TestClient(main.app)
    client.get("/auth/google", follow_redirects=False)
    state = session(client)["google_oauth_state"]
    response = client.get("/auth/google/callback", params={"code": "fixture", "state": state},
                          follow_redirects=False)
    assert response.headers["location"] == "/signup?status=no-workspace"
    assert "session_" not in client.cookies
    page = client.get(response.headers["location"])
    assert "No workspace found for this email and public signup is closed; contact the operator." in page.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert bool(user) == existing
        if user:
            assert not db.scalar(select(Membership.id).where(Membership.user_id == user.id))


@pytest.mark.parametrize("verified", [False, True])
def test_password_orphan_gets_workspace_and_verification(monkeypatch, verified):
    from datetime import UTC, datetime

    email = uuid4().hex + "@example.test"
    password = "merchant-private-test-password"
    with SessionLocal() as db:
        user = User(email=email, name="Password Merchant",
                    password_hash=auth.hash_admin_password(password),
                    email_verified_at=datetime.now(UTC) if verified else None)
        db.add(user)
        db.commit()
    sent = []
    monkeypatch.setattr(main, "dispatch_mail", sent.append)
    client = TestClient(main.app)
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post("/login", data={"email": email, "password": password, "csrf_token": token},
                           follow_redirects=False)
    assert response.headers["location"].startswith("/admin/onboarding/")
    assert session(client)["role"] == "admin"
    assert session(client)["csrf_token"] != token
    assert bool(sent) == (not verified)

