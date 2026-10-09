import re
import secrets
from types import SimpleNamespace

import pytest
from fasthtml.common import fast_app
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from app import marketing_routes, onboarding_routes, signup_services, site_routes
from app.models import (
    Base,
    Channel,
    CommerceMail,
    Membership,
    OnboardingState,
    SignupAttempt,
    SignupEmailVerification,
    Site,
    SiteMenu,
    SitePage,
    Tenant,
    UsageEvent,
    User,
)
from app.services import CommerceError


def csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


@pytest.fixture
def signup_workspace(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    route_settings = SimpleNamespace(signup_open=True)
    service_settings = SimpleNamespace(
        session_secret="signup-test-session-secret",
        public_url="http://testserver",
    )
    sent = []
    monkeypatch.setattr(marketing_routes, "SessionLocal", sessions)
    monkeypatch.setattr(marketing_routes, "settings", route_settings)
    monkeypatch.setattr(marketing_routes, "dispatch_mail", lambda message_id: sent.append(message_id))
    monkeypatch.setattr(signup_services, "settings", service_settings)
    monkeypatch.setattr(site_routes, "SessionLocal", sessions)
    monkeypatch.setattr(onboarding_routes, "SessionLocal", sessions)
    monkeypatch.setattr(site_routes, "dispatch_mail", lambda message_id: sent.append(message_id))

    app, rt = fast_app(
        live=False,
        pico=False,
        secret_key="signup-test-cookie-secret",
        sess_https_only=False,
        same_site="lax",
        canonical=False,
    )

    def csrf_token(session):
        session.setdefault("csrf_token", secrets.token_urlsafe(32))
        return session["csrf_token"]

    def require_csrf(session, supplied):
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(expected, supplied):
            raise CommerceError("Session expired.")

    def establish_session(session, user, role):
        session["user_id"] = user.id
        session["role"] = role
        session["email"] = user.email

    site_routes.register_site_routes(rt)
    marketing_routes.register_marketing_routes(
        rt, csrf_token, require_csrf, establish_session
    )

    @rt("/_session", methods=["GET"])
    def get(session):
        return JSONResponse(dict(session))

    yield SimpleNamespace(
        client=TestClient(app),
        sessions=sessions,
        settings=route_settings,
        sent=sent,
    )
    engine.dispose()


def signup(client, *, name="North & Pine", email="owner@example.test"):
    page = client.get("/signup")
    password = "correct-horse-battery-staple"
    return client.post(
        "/signup",
        data={
            "csrf_token": csrf(page),
            "name": name,
            "email": email,
            "password": password,
            "password_confirmation": password,
        },
        follow_redirects=False,
    )


def test_signup_kill_switch_off_and_on(signup_workspace):
    workspace = signup_workspace
    workspace.settings.signup_open = False
    closed = workspace.client.get("/signup")
    assert closed.status_code == 200
    assert "Public signup is currently closed" in closed.text
    assert 'action="/signup"' not in closed.text
    post = workspace.client.post("/signup", data={}, follow_redirects=False)
    assert post.status_code == 303 and post.headers["location"] == "/signup?status=closed"
    with workspace.sessions() as db:
        assert db.scalar(select(func.count(User.id))) == 0

    workspace.settings.signup_open = True
    opened = workspace.client.get("/signup")
    assert opened.status_code == 200
    assert "Create your FastShop workspace" in opened.text
    assert 'name="csrf_token"' in opened.text
    assert 'action="/signup"' in opened.text


def test_happy_path_provisions_clean_site_and_authenticates_session(signup_workspace):
    response = signup(signup_workspace.client)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/onboarding/")
    session = signup_workspace.client.get("/_session").json()
    assert session["role"] == "admin"
    assert session["email"] == "owner@example.test"
    onboarding_page = signup_workspace.client.get(response.headers["location"])
    assert onboarding_page.status_code == 200
    assert "Tell us what you are building" in onboarding_page.text

    with signup_workspace.sessions() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.test"))
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
        tenant = db.get(Tenant, membership.tenant_id)
        site = db.scalar(select(Site).where(Site.tenant_id == tenant.id))
        assert response.headers["location"] == f"/admin/onboarding/{site.id}"
        assert membership.role == "admin"
        assert tenant.name == "North & Pine"
        assert tenant.plan == "free"
        startup_usage = db.scalar(
            select(UsageEvent).where(
                UsageEvent.tenant_id == tenant.id,
                UsageEvent.kind == "site_created",
            )
        )
        assert startup_usage is not None and startup_usage.quantity == 1
        assert startup_usage.site_id == site.id
        assert site.hostname is None and site.status == "draft"
        assert db.scalar(
            select(func.count(SitePage.id)).where(
                SitePage.site_id == site.id, SitePage.tenant_id == tenant.id
            )
        ) == 8
        assert db.scalar(
            select(func.count(SiteMenu.id)).where(
                SiteMenu.site_id == site.id, SiteMenu.tenant_id == tenant.id
            )
        ) == 2
        assert db.scalar(
            select(func.count(Channel.id)).where(Channel.tenant_id == tenant.id)
        ) == 1
        assert "hydrogen" not in str(site.settings_json).lower()
        onboarding_state = db.scalar(
            select(OnboardingState).where(
                OnboardingState.site_id == site.id,
                OnboardingState.tenant_id == tenant.id,
                OnboardingState.user_id == user.id,
            )
        )
        assert onboarding_state and onboarding_state.status == "brief"
        verification = db.scalar(
            select(SignupEmailVerification).where(
                SignupEmailVerification.user_id == user.id,
                SignupEmailVerification.site_id == site.id,
                SignupEmailVerification.tenant_id == tenant.id,
            )
        )
        mail = db.scalar(
            select(CommerceMail).where(
                CommerceMail.user_id == user.id,
                CommerceMail.site_id == site.id,
                CommerceMail.tenant_id == tenant.id,
            )
        )
        assert verification and verification.consumed_at is None
        assert mail.kind == "account_verification"
        assert mail.id in signup_workspace.sent


def test_google_signup_provisions_verified_workspace_without_password_or_mail(
    signup_workspace,
):
    with signup_workspace.sessions() as db:
        result = signup_services.provision_google_signup(
            db,
            "Google Merchant",
            "google-merchant@example.test",
            "192.0.2.42",
        )
        db.commit()
        user_id = result.user.id
        site_id = result.site.id

    with signup_workspace.sessions() as db:
        user = db.get(User, user_id)
        site = db.get(Site, site_id)
        membership = db.scalar(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.tenant_id == site.tenant_id,
            )
        )
        assert user.password_hash is None
        assert user.email_verified_at is not None
        assert membership.role == "admin"
        assert db.scalar(
            select(OnboardingState.status).where(OnboardingState.site_id == site_id)
        ) == "brief"
        assert db.scalar(select(func.count(CommerceMail.id))) == 0
        assert db.scalar(
            select(func.count(SitePage.id)).where(SitePage.site_id == site_id)
        ) == 8


def test_verification_token_is_single_use(signup_workspace):
    assert signup(signup_workspace.client).status_code == 303
    with signup_workspace.sessions() as db:
        verification = db.scalar(select(SignupEmailVerification))
        tenant_id = verification.tenant_id
        verification_id = verification.id
        token = signup_services.verification_token(verification)

    path = f"/signup/verify/{tenant_id}/{verification_id}"
    page = signup_workspace.client.get(path)
    response = signup_workspace.client.post(
        path,
        data={"csrf_token": csrf(page), "token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/signup/verified"
    with signup_workspace.sessions() as db:
        verification = db.get(SignupEmailVerification, verification_id)
        assert verification.consumed_at is not None
        assert db.get(User, verification.user_id).email_verified_at is not None

    page = signup_workspace.client.get(path)
    reused = signup_workspace.client.post(
        path,
        data={"csrf_token": csrf(page), "token": token},
        follow_redirects=False,
    )
    assert reused.status_code == 303
    assert reused.headers["location"].endswith("?status=invalid")


def test_rate_limit_is_independent_per_client_and_email(signup_workspace):
    with signup_workspace.sessions() as db:
        for index in range(signup_services.SIGNUP_FAILURE_LIMIT):
            signup_services.record_attempt(
                db,
                "signup",
                f"person-{index}@example.test",
                "192.0.2.10",
                accepted=False,
            )
        assert signup_services.rate_limited(
            db, "signup", "new@example.test", "192.0.2.10"
        )
        assert not signup_services.rate_limited(
            db, "signup", "new@example.test", "192.0.2.11"
        )
        db.execute(delete(SignupAttempt))
        for index in range(signup_services.SIGNUP_FAILURE_LIMIT):
            signup_services.record_attempt(
                db,
                "signup",
                "same@example.test",
                f"198.51.100.{index}",
                accepted=False,
            )
        assert signup_services.rate_limited(
            db, "signup", "same@example.test", "203.0.113.20"
        )
        assert not signup_services.rate_limited(
            db, "signup", "other@example.test", "203.0.113.20"
        )
        db.execute(delete(SignupAttempt))
        for _ in range(signup_services.SIGNUP_RESEND_LIMIT):
            signup_services.record_attempt(
                db,
                "resend",
                "resend@example.test",
                "203.0.113.30",
                accepted=True,
            )
        assert signup_services.rate_limited(
            db, "resend", "resend@example.test", "203.0.113.99"
        )


def test_duplicate_email_has_generic_non_enumerating_result(signup_workspace):
    assert signup(signup_workspace.client).status_code == 303
    duplicate = signup(signup_workspace.client, name="Another business")
    assert duplicate.status_code == 303
    assert duplicate.headers["location"] == "/signup?status=unable#signup-form"
    result = signup_workspace.client.get(duplicate.headers["location"])
    assert "We could not complete signup with these details." in result.text
    assert "Account already exists" not in result.text
    assert "valid email address" not in result.text.lower()
    with signup_workspace.sessions() as db:
        assert db.scalar(select(func.count(User.id))) == 1
        assert db.scalar(select(func.count(Tenant.id))) == 1


def test_new_email_validation_errors_are_accessible_and_do_not_retain_password(
    signup_workspace,
):
    page = signup_workspace.client.get("/signup")
    response = signup_workspace.client.post(
        "/signup",
        data={
            "csrf_token": csrf(page),
            "name": "",
            "email": "not-an-email",
            "password": "short",
            "password_confirmation": "different",
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == "/signup?status=unable#signup-form"
    result = signup_workspace.client.get(response.headers["location"])
    assert 'id="name-error"' in result.text
    assert 'aria-describedby="name-error"' in result.text
    assert 'aria-invalid="true"' in result.text
    assert 'id="password-help"' in result.text
    assert 'value="short"' not in result.text


def test_missing_csrf_is_rejected_without_creating_account(signup_workspace):
    response = signup_workspace.client.post(
        "/signup",
        data={
            "name": "No Token",
            "email": "no-token@example.test",
            "password": "correct-horse-battery-staple",
            "password_confirmation": "correct-horse-battery-staple",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/signup?status=session#signup-form"
    with signup_workspace.sessions() as db:
        assert db.scalar(select(func.count(User.id))) == 0


def test_slug_collision_uses_bounded_unique_suffix(signup_workspace):
    first = signup(
        signup_workspace.client,
        name="Shared Store",
        email="first-owner@example.test",
    )
    assert first.status_code == 303
    other_client = TestClient(signup_workspace.client.app)
    second = signup(
        other_client,
        name="Shared Store",
        email="second-owner@example.test",
    )
    assert second.status_code == 303
    with signup_workspace.sessions() as db:
        slugs = list(db.scalars(select(Site.slug).order_by(Site.slug)))
        assert slugs == ["shared-store", "shared-store-2"]
        assert all(3 <= len(slug) <= signup_services.SIGNUP_SLUG_MAX_LENGTH for slug in slugs)
