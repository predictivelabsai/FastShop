import re

from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_public_surface_and_discovery_routes():
    for path in (
        "/",
        "/products",
        "/categories",
        "/products/harbour-runner",
        "/assistant",
        "/developers",
        "/healthz",
        "/readyz",
        "/api/v1/health",
        "/api/v1/products",
        "/robots.txt",
        "/sitemap.xml",
        "/llms.txt",
    ):
        assert client.get(path).status_code == 200, path


def test_admin_requires_login_and_local_admin_can_sign_in():
    session_client = TestClient(app)
    response = session_client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    login = session_client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login.text).group(1)
    response = session_client.post(
        "/login",
        data={
            "email": "admin@fastshop.example",
            "password": "FastShop2026$",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert session_client.get("/admin").status_code == 200


def test_login_preserves_only_same_origin_return_paths():
    session_client = TestClient(app)
    login = session_client.get("/login?next=/wishlist")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login.text).group(1)
    response = session_client.post(
        "/login",
        data={
            "email": "admin@fastshop.example",
            "password": "FastShop2026$",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == "/wishlist"

    rejected = TestClient(app)
    login = rejected.get("/login?next=//malicious.example")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login.text).group(1)
    response = rejected.post(
        "/login",
        data={
            "email": "admin@fastshop.example",
            "password": "FastShop2026$",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == "/admin"


def test_shopper_assistant_has_no_key_fallback():
    session_client = TestClient(app)
    page = session_client.get("/assistant")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = session_client.post(
        "/chat",
        data={"message": "/products", "route": "/assistant", "csrf_token": token},
    )
    assert response.status_code == 200
    assert "Available now" in response.json()["answer"]


def _login_csrf(http):
    page = http.get("/login")
    import re as _re
    return _re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


def test_per_user_password_login_uses_membership_role():
    from sqlalchemy import select

    from app.auth import hash_admin_password
    from app.db import SessionLocal
    from app.models import Membership, Tenant, User

    password = "merchant-test-passphrase-24-chars"
    email = "merchant-user@fastshop.example"
    with SessionLocal() as db:
        tenant_id = db.scalar(select(Tenant.id).where(Tenant.slug == "fastshop-demo"))
        assert tenant_id
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(
                email=email,
                name="Merchant User",
                password_hash=hash_admin_password(password),
            )
            db.add(user)
            db.flush()
        membership = db.scalar(
            select(Membership).where(Membership.tenant_id == tenant_id, Membership.user_id == user.id)
        )
        if not membership:
            db.add(Membership(tenant_id=tenant_id, user_id=user.id, role="merchant"))
        db.commit()

    session_client = TestClient(app)
    response = session_client.post(
        "/login",
        data={"email": email.upper(), "password": password, "csrf_token": _login_csrf(session_client)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert session_client.get("/admin").status_code == 200


def test_per_user_login_rejects_wrong_password_and_passwordless_users():
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        if not db.scalar(select(User).where(User.email == "loginless@fastshop.example")):
            db.add(User(email="loginless@fastshop.example"))
            db.commit()

    rejected = TestClient(app)
    response = rejected.post(
        "/login",
        data={
            "email": "merchant-user@fastshop.example",
            "password": "deliberately-wrong-passphrase-xx",
            "csrf_token": _login_csrf(rejected),
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == "/login?error=Invalid+email+or+password"

    passwordless = TestClient(app)
    response = passwordless.post(
        "/login",
        data={
            "email": "loginless@fastshop.example",
            "password": "any-passphrase-of-24-characters",
            "csrf_token": _login_csrf(passwordless),
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == "/login?error=Invalid+email+or+password"
