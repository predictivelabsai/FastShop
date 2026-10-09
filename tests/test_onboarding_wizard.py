import re
import secrets
from types import SimpleNamespace

import pytest
from fasthtml.common import fast_app
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from app import (
    content,
    onboarding,
    onboarding_routes,
    site_builder_routes,
    site_generation,
    site_routes,
)
from app.models import (
    Base,
    Membership,
    OnboardingState,
    Product,
    Site,
    SiteMenu,
    SitePage,
    UsageEvent,
    User,
)
from app.services import CommerceError


def csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


@pytest.fixture
def onboarding_workspace(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(site_routes, "SessionLocal", sessions)
    monkeypatch.setattr(onboarding_routes, "SessionLocal", sessions)
    monkeypatch.setattr(site_builder_routes, "SessionLocal", sessions)

    with sessions() as db:
        owner = User(email="owner@example.test", name="North & Pine")
        outsider = User(email="outsider@example.test", name="Outside User")
        editor = User(email="editor@example.test", name="Site Editor")
        db.add_all([owner, outsider, editor])
        db.flush()
        site = content.create_site(db, owner.id, "North & Pine", "north-and-pine")
        onboarding.create_state(db, site, owner.id)
        db.add(Membership(tenant_id=site.tenant_id, user_id=editor.id, role="editor"))
        db.commit()
        owner_id = owner.id
        outsider_id = outsider.id
        editor_id = editor.id
        site_id = site.id

    app, rt = fast_app(
        live=False,
        pico=False,
        secret_key="onboarding-test-cookie-secret",
        sess_https_only=False,
        same_site="lax",
        canonical=False,
    )
    site_routes.register_site_routes(rt)

    @rt("/_test/login/{user_id}", methods=["GET"])
    def get(session, user_id: str):
        session["user_id"] = user_id
        session["role"] = "admin"
        session["csrf_token"] = secrets.token_urlsafe(32)
        return JSONResponse({"csrf_token": session["csrf_token"]})

    def signed_in(user_id=owner_id):
        client = TestClient(app)
        token = client.get(f"/_test/login/{user_id}").json()["csrf_token"]
        return client, token

    yield SimpleNamespace(
        sessions=sessions,
        signed_in=signed_in,
        owner_id=owner_id,
        outsider_id=outsider_id,
        editor_id=editor_id,
        site_id=site_id,
    )
    engine.dispose()


def save_brief(client, site_id, token, **overrides):
    values = {
        "csrf_token": token,
        "business_description": (
            "A small independent shop for people who want useful, considered home goods."
        ),
        "product_context": "online shop",
        "design_direction": "Warm and natural",
    }
    values.update(overrides)
    return client.post(
        f"/admin/onboarding/{site_id}/brief",
        data=values,
        follow_redirects=False,
    )


def guided_counter(monkeypatch):
    calls = []
    original = site_generation.generate_plan

    def generate(value):
        calls.append(value)
        return original(value, force_guided=True)

    monkeypatch.setattr(site_generation, "generate_plan", generate)
    return calls


def structure_counts(workspace):
    with workspace.sessions() as db:
        state = db.scalar(select(OnboardingState))
        site_id = state.site_id
        tenant_id = state.tenant_id
        return (
            db.scalar(select(func.count(SitePage.id)).where(SitePage.site_id == site_id)),
            db.scalar(select(func.count(SiteMenu.id)).where(SiteMenu.site_id == site_id)),
            db.scalar(select(func.count(Product.id)).where(Product.tenant_id == tenant_id)),
        )


def test_happy_path_saves_brief_generates_same_site_and_completes(
    onboarding_workspace, monkeypatch
):
    workspace = onboarding_workspace
    client, token = workspace.signed_in()
    assert save_brief(client, workspace.site_id, token).headers["location"] == (
        f"/admin/onboarding/{workspace.site_id}"
    )
    calls = guided_counter(monkeypatch)

    decision = client.get(f"/admin/onboarding/{workspace.site_id}")
    assert "Your brief is ready" in decision.text
    response = client.post(
        f"/admin/onboarding/{workspace.site_id}/generate",
        data={"csrf_token": csrf(decision)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/admin/sites/{workspace.site_id}?")
    assert len(calls) == 1
    with workspace.sessions() as db:
        state = db.scalar(select(OnboardingState))
        assert state.status == "complete"
        assert state.generation_source == "guided"
        assert state.result_json["pages_generated"] >= 5
        assert state.result_json["products_seeded"] == 3
        assert state.site_id == workspace.site_id
        usage = list(
            db.scalars(
                select(UsageEvent).where(
                    UsageEvent.site_id == workspace.site_id,
                    UsageEvent.kind == "ai_generation",
                )
            )
        )
        assert len(usage) == 1 and usage[0].quantity == 1
        paths = set(db.scalars(select(SitePage.path).where(SitePage.site_id == workspace.site_id)))
        assert {"/", "/shop", "/blogs/learn"} <= paths
    overview = client.get(response.headers["location"])
    assert "Draft created with guided presets" in overview.text


def test_skip_is_terminal_without_generation_and_builder_remains_available(
    onboarding_workspace, monkeypatch
):
    workspace = onboarding_workspace
    client, token = workspace.signed_in()
    save_brief(client, workspace.site_id, token)
    calls = []
    monkeypatch.setattr(
        site_generation,
        "generate_plan",
        lambda _value: calls.append("called") or (_ for _ in ()).throw(AssertionError),
    )
    before = structure_counts(workspace)
    page = client.get(f"/admin/onboarding/{workspace.site_id}")
    response = client.post(
        f"/admin/onboarding/{workspace.site_id}/skip",
        data={"csrf_token": csrf(page)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert calls == []
    assert structure_counts(workspace) == before == (8, 2, 0)
    with workspace.sessions() as db:
        state = db.scalar(select(OnboardingState))
        assert state.status == "skipped"
        assert state.result_json == {"pages_kept": 8, "generation_calls": 0}
    assert client.get(f"/admin/sites/{workspace.site_id}/build").status_code == 200


def test_saved_brief_resumes_in_a_new_session(onboarding_workspace):
    workspace = onboarding_workspace
    first_client, first_token = workspace.signed_in()
    save_brief(first_client, workspace.site_id, first_token)
    first_client.close()

    second_client, _ = workspace.signed_in()
    page = second_client.get(f"/admin/onboarding/{workspace.site_id}")
    assert page.status_code == 200
    assert "Your brief is ready" in page.text
    assert "useful, considered home goods" in page.text
    assert "Generate with AI" in page.text
    assert "<fieldset" in page.text and "<legend>Choose a look and feel</legend>" in page.text
    assert 'data-generation-status=""' in page.text


def test_generation_failure_preserves_brief_and_retry_completes(
    onboarding_workspace, monkeypatch
):
    workspace = onboarding_workspace
    client, token = workspace.signed_in()
    save_brief(client, workspace.site_id, token)
    original = site_generation.generate_plan
    calls = []

    def flaky(value):
        calls.append(value)
        if len(calls) == 1:
            raise CommerceError("provider-internal-token-must-not-render")
        return original(value, force_guided=True)

    monkeypatch.setattr(site_generation, "generate_plan", flaky)
    page = client.get(f"/admin/onboarding/{workspace.site_id}")
    failed = client.post(
        f"/admin/onboarding/{workspace.site_id}/generate",
        data={"csrf_token": csrf(page)},
        follow_redirects=True,
    )
    assert "Something went wrong" in failed.text
    assert "provider-internal-token" not in failed.text
    assert structure_counts(workspace) == (8, 2, 0)
    with workspace.sessions() as db:
        state = db.scalar(select(OnboardingState))
        assert state.status == "failed"
        assert "useful, considered home goods" in state.business_description
        assert db.scalar(
            select(func.count(UsageEvent.id)).where(
                UsageEvent.kind == "ai_generation"
            )
        ) == 0

    retried = client.post(
        f"/admin/onboarding/{workspace.site_id}/generate",
        data={"csrf_token": csrf(failed)},
        follow_redirects=False,
    )
    assert retried.headers["location"].startswith(f"/admin/sites/{workspace.site_id}?")
    assert len(calls) == 2
    with workspace.sessions() as db:
        assert db.scalar(select(OnboardingState.status)) == "complete"
        assert db.scalar(
            select(func.count(UsageEvent.id)).where(
                UsageEvent.kind == "ai_generation"
            )
        ) == 1


def test_generation_quota_redirects_before_claim_or_provider_call(
    onboarding_workspace, monkeypatch
):
    workspace = onboarding_workspace
    client, token = workspace.signed_in()
    save_brief(client, workspace.site_id, token)
    calls = guided_counter(monkeypatch)
    with workspace.sessions() as db:
        state = db.scalar(select(OnboardingState))
        db.add_all(
            [
                UsageEvent(
                    tenant_id=state.tenant_id,
                    site_id=state.site_id,
                    user_id=workspace.owner_id,
                    kind="ai_generation",
                )
                for _ in range(3)
            ]
        )
        db.commit()

    page = client.get(f"/admin/onboarding/{workspace.site_id}")
    response = client.post(
        f"/admin/onboarding/{workspace.site_id}/generate",
        data={"csrf_token": csrf(page)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(
        f"/admin/onboarding/{workspace.site_id}?notice="
    )
    notice = client.get(response.headers["location"])
    assert "Your Free plan allows 3 AI generation credits this month" in notice.text
    assert calls == []
    with workspace.sessions() as db:
        state = db.scalar(select(OnboardingState))
        assert state.status == "ready"
        assert state.generation_token == ""


def test_duplicate_generate_post_is_idempotent(onboarding_workspace, monkeypatch):
    workspace = onboarding_workspace
    client, token = workspace.signed_in()
    save_brief(client, workspace.site_id, token)
    calls = guided_counter(monkeypatch)
    page = client.get(f"/admin/onboarding/{workspace.site_id}")
    post_data = {"csrf_token": csrf(page)}
    first = client.post(
        f"/admin/onboarding/{workspace.site_id}/generate",
        data=post_data,
        follow_redirects=False,
    )
    counts = structure_counts(workspace)
    second = client.post(
        f"/admin/onboarding/{workspace.site_id}/generate",
        data=post_data,
        follow_redirects=False,
    )
    assert first.status_code == second.status_code == 303
    assert len(calls) == 1
    assert structure_counts(workspace) == counts


def test_generation_does_not_clobber_content_created_while_provider_runs(
    onboarding_workspace, monkeypatch
):
    workspace = onboarding_workspace
    client, token = workspace.signed_in()
    save_brief(client, workspace.site_id, token)
    original = site_generation.generate_plan

    def edit_then_generate(value):
        with workspace.sessions() as db:
            state = db.scalar(select(OnboardingState))
            site = db.get(Site, state.site_id)
            content.create_page(
                db,
                site,
                "Merchant note",
                "/pages/merchant-note",
                document={
                    "title": "Merchant note",
                    "sections": [
                        {
                            "type": "text",
                            "heading": "Keep this page",
                            "body": "Created while the provider was running.",
                        }
                    ],
                },
            )
            db.commit()
        return original(value, force_guided=True)

    monkeypatch.setattr(site_generation, "generate_plan", edit_then_generate)
    page = client.get(f"/admin/onboarding/{workspace.site_id}")
    response = client.post(
        f"/admin/onboarding/{workspace.site_id}/generate",
        data={"csrf_token": csrf(page)},
        follow_redirects=True,
    )
    assert "existing site edits are safe" in response.text
    with workspace.sessions() as db:
        state = db.scalar(select(OnboardingState))
        paths = set(db.scalars(select(SitePage.path).where(SitePage.site_id == state.site_id)))
        assert state.status == "failed"
        assert "/pages/merchant-note" in paths
        assert db.scalar(select(func.count(Product.id)).where(
            Product.tenant_id == state.tenant_id
        )) == 0


def test_non_member_and_editor_cannot_open_or_post(onboarding_workspace):
    workspace = onboarding_workspace
    for user_id in (workspace.outsider_id, workspace.editor_id):
        client, token = workspace.signed_in(user_id)
        assert client.get(f"/admin/onboarding/{workspace.site_id}").status_code == 403
        response = save_brief(client, workspace.site_id, token)
        assert response.status_code == 403
    with workspace.sessions() as db:
        state = db.scalar(select(OnboardingState))
        assert state.status == "brief" and not state.business_description


def test_csrf_and_bounded_input_rejections_do_not_change_state(onboarding_workspace):
    workspace = onboarding_workspace
    client, token = workspace.signed_in()
    missing = save_brief(client, workspace.site_id, "")
    invalid = save_brief(client, workspace.site_id, "not-the-token")
    assert missing.status_code == invalid.status_code == 400
    with workspace.sessions() as db:
        state = db.scalar(select(OnboardingState))
        assert state.status == "brief" and not state.business_description

    too_long = save_brief(
        client,
        workspace.site_id,
        token,
        business_description="x" * (onboarding.BUSINESS_DESCRIPTION_MAX_LENGTH + 1),
    )
    assert too_long.status_code == 303
    page = client.get(too_long.headers["location"])
    assert "500 characters or fewer" in page.text
    with workspace.sessions() as db:
        state = db.scalar(select(OnboardingState))
        assert state.status == "brief" and not state.business_description
