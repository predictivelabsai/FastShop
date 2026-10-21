import re

import pytest
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


def test_per_user_password_login_uses_membership_role(monkeypatch):
    from dataclasses import replace

    from sqlalchemy import select

    from app import auth
    from app.auth import hash_admin_password
    from app.db import SessionLocal
    from app.models import Membership, Tenant, User

    monkeypatch.setattr(
        auth,
        "settings",
        replace(
            auth.settings,
            environment="production",
            signup_open=True,
            allow_password_login=False,
            admin_email="",
            admin_password_hash="",
            admin_password="",
        ),
    )
    monkeypatch.setattr(auth, "_password_attempts", auth.deque())

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


@pytest.mark.parametrize("membership_count", [0, 1, 2])
@pytest.mark.parametrize("catalog_state", ["priced", "free", "unpriced", "inactive"])
def test_admin_views_follow_membership_tenants(monkeypatch, membership_count, catalog_state):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app import main
    from app.auth import hash_admin_password
    from app.models import (
        Base,
        Category,
        Channel,
        Membership,
        Order,
        Product,
        ProductType,
        ProductVariant,
        Stock,
        Tenant,
        User,
        VariantChannelListing,
        Voucher,
        Warehouse,
    )
    from app.services import products

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    password = "tenant-admin-test-passphrase"
    with sessions() as db:
        operator = User(email="admin@fastshop.example", name="Operator")
        merchant = User(email="scoped-merchant@example.com", name="Scoped merchant",
                        password_hash=hash_admin_password(password))
        db.add_all([operator, merchant])
        db.flush()
        tenant_ids = []
        for index, slug in enumerate(("fastshop-demo", "other-store")):
            tenant = Tenant(slug=slug, name=slug)
            db.add(tenant)
            db.flush()
            tenant_ids.append(tenant.id)
            channel = Channel(tenant_id=tenant.id, slug="europe", name=slug)
            category = Category(tenant_id=tenant.id, slug="catalog", name=slug)
            product_type = ProductType(tenant_id=tenant.id, slug="physical", name=slug)
            warehouse = Warehouse(tenant_id=tenant.id, code="main", name=slug)
            staff = User(email=f"staff-{index}@example.com", name=f"Tenant staff {index}")
            customer = User(email=f"buyer-{index}@example.com", name=f"Tenant buyer {index}")
            db.add_all([channel, category, product_type, warehouse, staff, customer])
            db.flush()
            db.add(Membership(tenant_id=tenant.id, user_id=staff.id, role="merchant"))
            # A single-tenant merchant belongs to the non-demo store.
            if index >= 2 - membership_count:
                db.add(Membership(tenant_id=tenant.id, user_id=merchant.id, role="merchant"))
            product = Product(tenant_id=tenant.id, category_id=category.id,
                              product_type_id=product_type.id, slug=f"product-{index}",
                              name=f"Tenant product {index}", is_published=True)
            db.add(product)
            db.flush()
            variant = ProductVariant(tenant_id=tenant.id, product_id=product.id,
                                     sku=f"TENANT-SKU-{index}", name=f"Variant {index}",
                                     is_active=catalog_state != "inactive")
            db.add(variant)
            db.flush()
            if catalog_state in {"priced", "free"}:
                db.add(VariantChannelListing(variant_id=variant.id, channel_id=channel.id,
                                             currency="EUR", price_minor=0 if catalog_state == "free" else 1000))
            db.add_all([
                Stock(variant_id=variant.id, warehouse_id=warehouse.id, quantity=10 + index),
                Voucher(tenant_id=tenant.id, code=f"TENANT-VOUCHER-{index}"),
                Order(tenant_id=tenant.id, channel_id=channel.id, user_id=customer.id,
                      number=f"TENANT-ORDER-{index}", idempotency_key=f"order-{index}",
                      email=customer.email, currency="EUR", subtotal_minor=1000 * (index + 1),
                      total_minor=1000 * (index + 1), payment_status="paid"),
            ])
        db.commit()
        actor = merchant if membership_count else operator
        expected_ids = set(tenant_ids[2 - membership_count:]) if membership_count else None
        assert main.membership_tenants(db, {"id": actor.id}) == expected_ids
        # The shared service's default remains the public demo catalog.
        public_cards = products(db)
        priced = catalog_state in {"priced", "free"}
        assert [card.product.name for card in public_cards] == (["Tenant product 0"] if priced else [])
        assert all(isinstance(card.price_minor, int) for card in public_cards)
        admin_cards = products(db, tenant_ids=expected_ids, admin_catalog=True)
        assert all((card.price_minor is not None) == priced for card in admin_cards)
        assert products(db, tenant_ids=set(), admin_catalog=True) == []

    try:
        with TestClient(app) as http:
            response = http.post("/login", data={
                "email": actor.email,
                "password": password if membership_count else "FastShop2026$",
                "csrf_token": _login_csrf(http),
            }, follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"] == "/admin"
            assert http.get("/login", follow_redirects=False).headers["location"] == "/admin"
            for path, markers in {
                "/admin": ["TENANT-ORDER"],
                "/admin/products": ["Tenant product"],
                "/admin/orders": ["TENANT-ORDER"],
                "/admin/inventory": ["TENANT-SKU"],
                "/admin/promotions": ["TENANT-VOUCHER"],
                "/admin/customers": ["staff", "buyer"],
            }.items():
                response = http.get(path)
                assert response.status_code == 200, path
                for index in range(2):
                    visible = membership_count != 1 or index == 1
                    for marker in markers:
                        separator = " " if marker == "Tenant product" else "-"
                        assert (f"{marker}{separator}{index}" in response.text) == visible, (path, marker, index)
                if path == "/admin/products":
                    catalog_rows = re.findall(r"<tr>(.*?)</tr>", response.text, re.DOTALL)[1:]
                    assert len(catalog_rows) == (1 if membership_count == 1 else 2)
                    for index in range(2):
                        if membership_count == 1 and index == 0:
                            continue
                        row = next(row for row in catalog_rows if f"Tenant product {index}" in row)
                        expected_price = ("€0.00" if catalog_state == "free" else "€10.00") if priced else "Price to be confirmed"
                        assert f"<td>{expected_price}</td>" in row
                        available = 0 if catalog_state == "inactive" else 10 + index
                        assert f"<td>{available}</td>" in row
                if path == "/admin":
                    expected_kpis = ["€20.00", "1", "1", "11"] if membership_count == 1 else ["€30.00", "2", "2", "21"]
                    assert re.findall(r"<strong>(.*?)</strong>", response.text)[:4] == expected_kpis
            public = http.get("/products")
            assert public.status_code == 200
            assert ("Tenant product 0" in public.text) == priced
            assert "Tenant product 1" not in public.text
            assert "Price to be confirmed" not in public.text
            assert http.get("/admin/content", follow_redirects=False).headers["location"] == "/admin/sites"
    finally:
        engine.dispose()
