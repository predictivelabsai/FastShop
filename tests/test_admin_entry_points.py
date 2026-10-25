"""Merchant entry contracts, using a fresh database for every HTTP scenario.

Run with PYTHON_DOTENV_DISABLED=1 and disposable FASTSHOP_DATA_DIR/DB_URL before
collection: main's bootstrap must never see the developer's storage or providers.
"""

import base64
import json
import re
import sys
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from itsdangerous import TimestampSigner
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app import admin_entry, auth, commerce, content, main, onboarding
from app.models import Base, Membership, OnboardingState, Site, Tenant, User

LEGACY = {
    "products": "products",
    "orders": "orders",
    "customers": "customers",
    "inventory": "inventory",
    "promotions": "discounts",
    "content": "content/pages",
    "channels": "settings/channels",
    "integrations": "settings/integrations",
    "integrations/stripe": "settings/integrations/stripe",
    "integrations/woocommerce": "settings/integrations/woocommerce",
    "integrations/paypal": "settings/integrations/paypal",
    "integrations/fasterp": "settings/integrations/fasterp",
    "assistant": "assistant",
}
MANAGER_ONLY = {"orders", "channels", "assistant", "integrations", *(
    "integrations/" + provider for provider in ("stripe", "woocommerce", "paypal", "fasterp")
)}
PASSWORD = "disposable-entry-test-passphrase"


def cookie_session(client):
    if "session_" not in client.cookies:
        return {}
    return json.loads(base64.b64decode(client.cookies["session_"].split(".")[0]))


def csrf(page):
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


@pytest.fixture
def entry(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    # All registered handlers and shell/middleware reads must share this database.
    for name, module in tuple(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", sessions)
    monkeypatch.setattr(auth, "_password_attempts", auth.deque())
    monkeypatch.setattr(main, "settings", replace(main.settings, signup_open=True))
    monkeypatch.setattr(main, "dispatch_mail", lambda _: None)
    from app import marketing_routes
    monkeypatch.setattr(marketing_routes, "dispatch_mail", lambda _: None)
    monkeypatch.setattr(marketing_routes, "settings", replace(main.settings, signup_open=True))
    with sessions() as db:
        owner = User(email="owner@example.test", name="Store owner",
                     password_hash=auth.hash_admin_password(PASSWORD),
                     email_verified_at=datetime.now(UTC))
        outsider = User(email="outsider@example.test", name="Other owner")
        db.add_all([owner, outsider])
        db.flush()
        # Insert in reverse alphabetical order to exercise deterministic selection.
        stores = [content.create_site(db, owner.id, name, slug)
                  for name, slug in (("Zulu store", "zulu-store"),
                                     ("Alpha store", "alpha-store"))]
        foreign = content.create_site(db, outsider.id, "Foreign store", "foreign-store")
        db.commit()

    def signed_in(role="admin", remembered=None, user=owner):
        client = TestClient(main.app)
        payload = {"user_id": user.id, "role": role, "email": user.email,
                   "csrf_token": "entry-csrf"}
        if remembered:
            payload["admin_site_id"] = remembered
        encoded = base64.b64encode(json.dumps(payload).encode())
        client.cookies.set("session_", TimestampSigner(main.settings.session_secret)
                           .sign(encoded).decode(), domain="testserver.local", path="/")
        return client

    yield SimpleNamespace(sessions=sessions, owner=owner, outsider=outsider,
                          stores=stores, site=stores[1], foreign=foreign, signed_in=signed_in)
    engine.dispose()


def set_role(entry, role):
    with entry.sessions() as db:
        for row in db.scalars(select(Membership).where(Membership.user_id == entry.owner.id)):
            row.role = role
        db.commit()


@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("role", ["admin", "merchant", "editor"])
def test_admin_and_password_defaults(entry, count, role):
    with entry.sessions() as db:
        db.execute(delete(Membership).where(Membership.user_id == entry.owner.id))
        # Keep a membership even with zero stores, avoiding orphan provisioning.
        tenant = Tenant(slug="empty-workspace", name="Empty workspace")
        db.add(tenant)
        db.flush()
        db.add(Membership(user_id=entry.owner.id, tenant_id=tenant.id, role=role))
        for site in entry.stores[:count]:
            db.add(Membership(user_id=entry.owner.id, tenant_id=site.tenant_id, role=role))
        db.commit()
    destination = f"/admin/sites/{entry.stores[0].id}" if count == 1 else "/admin/sites"
    client = TestClient(main.app)
    response = client.post("/login", data={"email": entry.owner.email, "password": PASSWORD,
                           "csrf_token": csrf(client.get("/login"))}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == destination
    assert client.get("/admin", follow_redirects=False).headers["location"] == destination
    page = client.get(destination)
    assert page.status_code == 200 and 'id="admin-main"' in page.text
    assert entry.foreign.name not in page.text
    if not count:
        assert 'action="/admin/sites"' in page.text


@pytest.mark.parametrize("section,suffix", LEGACY.items())
@pytest.mark.parametrize("count", [0, 1, 2])
def test_every_legacy_redirect_and_anonymous_next(entry, section, suffix, count):
    path = "/admin/" + section
    anonymous = TestClient(main.app).get(path + "?message=hello%20world&ignored=x",
                                       follow_redirects=False)
    assert anonymous.status_code == 303
    assert urlsplit(anonymous.headers["location"]).path == "/login"
    assert parse_qs(urlsplit(anonymous.headers["location"]).query) == {
        "next": [path + "?message=hello%20world&ignored=x"]}
    with entry.sessions() as db:
        if count < 2:
            db.execute(delete(Membership).where(
                Membership.user_id == entry.owner.id,
                Membership.tenant_id.in_([s.tenant_id for s in entry.stores[count:]])))
            db.commit()
    response = entry.signed_in().get(path, follow_redirects=False)
    expected_site = entry.stores[0] if count == 1 else entry.site
    expected = f"/admin/sites/{expected_site.id}/{suffix}" if count else "/admin/sites"
    assert response.status_code == 303 and response.headers["location"] == expected


def test_order_query_mapping_keeps_repeated_supported_filters(entry):
    response = entry.signed_in().get(
        "/admin/orders?q=blue%20hat&payment=paid&fulfilment=unfulfilled&tag=one&tag=two"
        "&view=archived&start=2026-01-01&end=2026-12-31&channel=web&sort=oldest"
        "&page=2&per_page=25&status_filter=paid&tenant_id=foreign&next=//example.test",
        follow_redirects=False)
    target = urlsplit(response.headers["location"])
    assert target.path == f"/admin/sites/{entry.site.id}/orders"
    assert parse_qs(target.query) == {
        "q": ["blue hat"], "payment": ["paid"], "fulfilment": ["unfulfilled"],
        "tag": ["one", "two"], "view": ["archived"], "start": ["2026-01-01"],
        "end": ["2026-12-31"], "channel": ["web"], "sort": ["oldest"],
        "page": ["2"], "per_page": ["25"], "status_filter": ["paid"]}
    other = entry.signed_in().get("/admin/products?message=Saved&payment=paid",
                                  follow_redirects=False)
    assert parse_qs(urlsplit(other.headers["location"]).query) == {"message": ["Saved"]}


@pytest.mark.parametrize("role", ["admin", "merchant", "editor"])
def test_role_boundaries_cover_legacy_and_destination(entry, role):
    set_role(entry, role)
    client = entry.signed_in(role)
    for section, suffix in LEGACY.items():
        denied = role == "editor" and section in MANAGER_ONLY
        response = client.get("/admin/" + section, follow_redirects=False)
        assert response.status_code == (403 if denied else 303), section
        target = client.get(f"/admin/sites/{entry.site.id}/{suffix}", follow_redirects=False)
        destination_denied = role == "editor" and (
            section in MANAGER_ONLY or section == "promotions")
        assert target.status_code == (400 if destination_denied else 200), suffix
    response = client.get("/developers", follow_redirects=False)
    assert response.status_code == (403 if role == "editor" else 303)
    if role != "editor":
        assert response.headers["location"] == f"/admin/sites/{entry.site.id}/settings/developers"


def test_selection_switching_revocation_and_foreign_ids(entry):
    client = entry.signed_in()
    for site in entry.stores:
        assert client.get(f"/admin/sites/{site.id}").status_code == 200
        assert cookie_session(client)["admin_site_id"] == site.id
        assert client.get("/admin/products", follow_redirects=False).headers["location"] == (
            f"/admin/sites/{site.id}/products")
    with entry.sessions() as db:
        db.execute(delete(Membership).where(Membership.user_id == entry.owner.id,
                                            Membership.tenant_id == entry.site.tenant_id))
        db.commit()
    # Revocation must keep requiring a deliberate choice on subsequent requests.
    for _ in range(2):
        assert client.get("/admin/products", follow_redirects=False).headers["location"] == (
            "/admin/sites")
    assert "admin_site_id" not in cookie_session(client)
    assert client.get(f"/admin/sites/{entry.stores[0].id}").status_code == 200
    assert client.get("/admin/products", follow_redirects=False).headers["location"].endswith(
        f"/{entry.stores[0].id}/products")
    for remembered in (entry.foreign.id, "missing-store"):
        stale = entry.signed_in(remembered=remembered)
        assert stale.get("/admin/products", follow_redirects=False).headers["location"] == (
            "/admin/sites")
        assert stale.get(f"/admin/sites/{entry.foreign.id}").status_code == 400


UNSAFE_NEXT = ["https://example.test", "//example.test", "///example.test",
               "/\\example.test", "/%5cexample.test", "/%255cexample.test",
               "/%2fexample.test", "/%252fexample.test", "/%0d%0aLocation:bad",
               "/%2500bad", "/path\tbad", "relative/path"]


@pytest.mark.parametrize("destination", UNSAFE_NEXT)
def test_unsafe_next_is_rejected_through_password_login(entry, destination):
    assert admin_entry.safe_next_path(destination) == ""
    client = TestClient(main.app)
    page = client.get("/login", params={"next": destination})
    response = client.post("/login", data={"email": entry.owner.email, "password": PASSWORD,
                           "csrf_token": csrf(page)}, follow_redirects=False)
    assert response.headers["location"] == "/admin/sites"
    assert "login_next" not in cookie_session(client)


@pytest.mark.parametrize("destination", ["/wishlist", "/admin/billing?notice=hello%20world"])
def test_explicit_safe_next_wins_and_is_consumed(entry, destination):
    client = TestClient(main.app)
    page = client.get("/login", params={"next": destination})
    response = client.post("/login", data={"email": entry.owner.email, "password": PASSWORD,
                           "csrf_token": csrf(page)}, follow_redirects=False)
    assert response.headers["location"] == destination
    assert client.get("/login", follow_redirects=False).headers["location"] == "/admin/sites"


def test_customer_default_and_login_csrf_rejection(entry):
    set_role(entry, "customer")
    client = TestClient(main.app)
    token = csrf(client.get("/login"))
    for supplied in ("", "wrong"):
        response = client.post("/login", data={"email": entry.owner.email,
            "password": PASSWORD, "csrf_token": supplied}, follow_redirects=False)
        assert response.headers["location"] == "/login?error=Session+expired"
        assert "user_id" not in cookie_session(client)
    response = client.post("/login", data={"email": entry.owner.email,
        "password": PASSWORD, "csrf_token": token}, follow_redirects=False)
    assert response.headers["location"] == "/account"


@pytest.mark.parametrize("mode", ["login", "signup"])
@pytest.mark.parametrize("destination", ["", "/wishlist?from=oauth"])
def test_oauth_default_and_explicit_next(entry, monkeypatch, mode, destination):
    monkeypatch.setattr(auth, "google_enabled", lambda: True)
    monkeypatch.setattr(auth, "exchange_code", lambda *_: {
        "email": entry.owner.email, "name": entry.owner.name})
    client = TestClient(main.app)
    client.get("/auth/google", params={"signup": "1" if mode == "signup" else "",
                                      "next": destination}, follow_redirects=False)
    state = cookie_session(client)["google_oauth_state"]
    response = client.get("/auth/google/callback", params={"code": "local", "state": state},
                          follow_redirects=False)
    assert response.headers["location"] == (destination or "/admin/sites")
    assert "login_next" not in cookie_session(client)


@pytest.mark.parametrize("flow", ["signup", "oauth", "password-orphan"])
def test_explicit_next_survives_required_onboarding(entry, monkeypatch, flow):
    client = TestClient(main.app)
    destination = "/wishlist?from=setup"
    email = "new-owner@example.test"
    if flow == "signup":
        page = client.get("/signup", params={"next": destination})
        response = client.post("/signup", data={"csrf_token": csrf(page),
            "name": "New store", "email": email, "password": PASSWORD,
            "password_confirmation": PASSWORD}, follow_redirects=False)
    elif flow == "oauth":
        monkeypatch.setattr(auth, "google_enabled", lambda: True)
        monkeypatch.setattr(auth, "exchange_code", lambda *_: {"email": email, "name": "New store"})
        client.get("/auth/google", params={"signup": "1", "next": destination},
                   follow_redirects=False)
        state = cookie_session(client)["google_oauth_state"]
        response = client.get("/auth/google/callback", params={"code": "local", "state": state},
                              follow_redirects=False)
    else:
        with entry.sessions() as db:
            db.add(User(email=email, name="New store", password_hash=auth.hash_admin_password(PASSWORD)))
            db.commit()
        page = client.get("/login", params={"next": destination})
        response = client.post("/login", data={"csrf_token": csrf(page), "email": email,
                                               "password": PASSWORD}, follow_redirects=False)
    assert response.headers["location"].startswith("/admin/onboarding/")
    setup = response.headers["location"]
    assert cookie_session(client)["login_next"] == destination
    page = client.get(setup)
    response = client.post(setup + "/skip", data={"csrf_token": csrf(page)}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == destination
    assert "login_next" not in cookie_session(client)
    with entry.sessions() as db:
        state = db.scalar(select(OnboardingState).where(OnboardingState.site_id == setup.rsplit("/", 1)[1]))
        assert state.status == "skipped"


def test_inactive_user_cannot_enter_or_authenticate(entry):
    with entry.sessions() as db:
        db.get(User, entry.owner.id).is_active = False
        db.commit()
    client = entry.signed_in()
    response = client.get("/admin/products?q=private", follow_redirects=False)
    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers["location"]).query) == {
        "next": ["/admin/products?q=private"]}
    with entry.sessions() as db:
        assert admin_entry.accessible_sites(db, entry.owner.id) == []
    client = TestClient(main.app)
    page = client.get("/login")
    response = client.post("/login", data={"email": entry.owner.email, "password": PASSWORD,
                           "csrf_token": csrf(page)}, follow_redirects=False)
    assert response.headers["location"] == "/login?error=Invalid+email+or+password"
    assert "user_id" not in cookie_session(client)


@pytest.mark.parametrize("role", ["admin", "merchant"])
@pytest.mark.parametrize("provider", ["stripe", "woocommerce", "paypal", "fasterp"])
def test_provider_guides_are_scoped_manager_only(entry, provider, role):
    set_role(entry, role)
    client = entry.signed_in()
    path = f"/admin/sites/{entry.site.id}/settings/integrations/{provider}"
    page = client.get(path)
    assert page.status_code == 200 and page.text.count('id="admin-main"') == 1
    assert entry.foreign.name not in page.text
    assert client.get(path.replace(entry.site.id, entry.foreign.id)).status_code == 400
    set_role(entry, "editor")
    assert client.get(path).status_code == 400


@pytest.mark.parametrize("path", ["/signup", "/auth/google"])
@pytest.mark.parametrize("destination", UNSAFE_NEXT)
def test_signup_and_oauth_do_not_remember_unsafe_next(entry, path, destination):
    client = TestClient(main.app)
    client.get(path, params={"next": destination}, follow_redirects=False)
    assert "login_next" not in cookie_session(client)


@pytest.mark.parametrize("legacy", [False, True])
def test_sync_revalidates_role_tenant_and_csrf(entry, monkeypatch, legacy):
    calls = []
    monkeypatch.setattr(main.fasterp, "deliver_pending",
                        lambda db, **kw: calls.append(kw["tenant_ids"]) or (2, 0))
    path = ("/admin/integrations/fasterp/sync" if legacy else
            f"/admin/sites/{entry.site.id}/settings/integrations/fasterp/sync")
    client = entry.signed_in(remembered=entry.site.id)
    for token in ("", "wrong"):
        assert client.post(path, data={"csrf_token": token}, follow_redirects=False).status_code == (
            403 if legacy else 400)
    assert calls == []
    response = client.post(path, data={"csrf_token": "entry-csrf"}, follow_redirects=False)
    assert response.status_code == 303
    assert urlsplit(response.headers["location"]).path == (
        f"/admin/sites/{entry.site.id}/settings/integrations/fasterp")
    assert calls == [{entry.site.tenant_id}]
    if not legacy:
        foreign = path.replace(entry.site.id, entry.foreign.id)
        assert client.post(foreign, data={"csrf_token": "entry-csrf"},
                           follow_redirects=False).status_code == 400
        assert calls == [{entry.site.tenant_id}]
    set_role(entry, "editor")
    assert client.post(path, data={"csrf_token": "entry-csrf"}, follow_redirects=False).status_code == (403 if legacy else 400)
    assert calls == [{entry.site.tenant_id}]


def test_commerce_save_retains_persistence_and_security(entry):
    with entry.sessions() as db:
        config = commerce.settings_for(db, db.get(Site, entry.site.id), create=True)
        db.commit()
        version = config.version
    client = entry.signed_in()
    path = f"/admin/sites/{entry.site.id}/commerce"
    assert 'id="admin-main"' in client.get(path).text
    values = {"csrf_token": "entry-csrf", "site_version": entry.site.version,
              "version": version, "mode": "disabled", "states": "TX",
              "origin_country": "EE", "shipping_minor": "1234",
              "free_shipping_threshold_minor": "9000"}
    for token in ("", "wrong"):
        assert client.post(path, data=values | {"csrf_token": token},
                           follow_redirects=False).status_code == 400
    response = client.post(path, data=values, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == path
    with entry.sessions() as db:
        saved = commerce.settings_for(db, db.get(Site, entry.site.id))
        assert (saved.shipping_minor, saved.free_shipping_threshold_minor) == (1234, 9000)
        assert saved.version == version + 1
    assert client.post(path.replace(entry.site.id, entry.foreign.id), data=values,
                       follow_redirects=False).status_code == 400
    set_role(entry, "editor")
    assert client.post(path, data=values, follow_redirects=False).status_code == 400


def test_picker_preserves_setup_links_and_onboarding_ownership(entry):
    with entry.sessions() as db:
        onboarding.create_state(db, db.get(Site, entry.site.id), entry.owner.id)
        db.commit()
    path = f"/admin/onboarding/{entry.site.id}"
    client = entry.signed_in()
    assert f'href="{path}"' in client.get("/admin/sites").text
    assert client.get(path).status_code == 200
    outsider = entry.signed_in(user=entry.outsider)
    assert outsider.get(path).status_code == 403
    for action in ("brief", "skip", "generate"):
        assert outsider.post(path + "/" + action, data={
            "csrf_token": "entry-csrf",
            "business_description": "An independent shop selling useful home goods.",
            "product_context": "online shop", "design_direction": "Warm and natural",
        }, follow_redirects=False).status_code == 403
    set_role(entry, "editor")
    assert f'href="{path}"' not in client.get("/admin/sites").text
    assert client.get(path).status_code == 403
    for action in ("brief", "skip", "generate"):
        response = client.post(path + "/" + action, data={
            "csrf_token": "entry-csrf",
            "business_description": "An independent shop selling useful home goods.",
            "product_context": "online shop", "design_direction": "Warm and natural",
        }, follow_redirects=False)
        assert response.status_code == 403
    with entry.sessions() as db:
        assert db.scalar(select(OnboardingState.status)) == "brief"


def test_billing_shell_callbacks_and_csrf_preserve_plan(entry):
    client = entry.signed_in(remembered=entry.site.id)
    page = client.get("/admin/billing")
    assert page.status_code == 200 and page.text.count('id="admin-main"') == 1
    for callback in ("success?session_id=untrusted-local-id", "cancel"):
        response = client.get("/admin/billing/" + callback, follow_redirects=False)
        assert response.status_code == 303
        assert urlsplit(response.headers["location"]).path == "/admin/billing"
    response = client.post("/admin/billing/checkout", data={"plan": "pro"}, follow_redirects=False)
    assert response.status_code == 303
    assert "session expired" in parse_qs(urlsplit(response.headers["location"]).query)["notice"][0].lower()
    with entry.sessions() as db:
        assert db.get(Tenant, entry.site.tenant_id).plan == "free"


@pytest.mark.parametrize("path", ["revenue", "deliveries", "plans", "live-credentials"])
def test_platform_views_remain_operator_only(entry, path):
    url = "/admin/platform/" + path
    assert entry.signed_in().get(url).status_code == 400
    with entry.sessions() as db:
        operator = User(email=main.settings.admin_email, name="Platform operator")
        db.add(operator)
        db.commit()
    page = entry.signed_in(user=operator).get(url)
    assert page.status_code == 200
    assert 'id="admin-main"' not in page.text
    assert entry.signed_in(user=operator).get(f"/admin/sites/{entry.foreign.id}").status_code == 400


@pytest.mark.parametrize("action", ["store", "verify", "accept", "disable"])
def test_platform_credential_actions_reject_merchants_before_provider_calls(entry, action):
    client = entry.signed_in()
    path = f"/admin/platform/sites/{entry.site.id}/live-credentials/{action}"
    response = client.post(path, data={"csrf_token": "entry-csrf", "confirmed": "on",
                                      "reason": "Local permission boundary regression check."},
                           follow_redirects=False)
    assert response.status_code == 303
    notice = parse_qs(urlsplit(response.headers["location"]).query)["notice"][0]
    assert "operator" in notice.lower()
    from app.models import SiteStripeLiveCredential
    with entry.sessions() as db:
        assert db.scalar(select(SiteStripeLiveCredential)) is None
