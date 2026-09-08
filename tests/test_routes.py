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
