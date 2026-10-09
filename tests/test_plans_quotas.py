"""Phase 5d plans, quotas, and metering coverage.

Enforcement seams are exercised through the real routes where they exist
(sites, AI generation, catalog products, operator console) and through the
service functions where no route owns the seam (golive publish, connector
apply, builder-review approval). Reads must stay open while every quota is
exhausted; writes must fail before anything half-applied.
"""

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fasthtml.common import fast_app
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from app import connectors, content, plans, site_builder_reviews, site_generation, site_routes
from app.models import (
    Base,
    Category,
    ExternalMapping,
    IntegrationPlan,
    Product,
    ProductType,
    Site,
    SiteBuilderTurn,
    Tenant,
    UsageEvent,
    User,
)


def csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


@pytest.fixture
def workspace(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    # Route modules resolve their engine through their own SessionLocal global;
    # tests bind a fresh in-memory StaticPool engine per workspace (as
    # tests/test_signup.py does).
    monkeypatch.setattr(site_routes, "SessionLocal", sessions)
    monkeypatch.setattr("app.site_catalog.SessionLocal", sessions)
    monkeypatch.setattr("app.plan_routes.SessionLocal", sessions)

    app, rt = fast_app(
        live=False,
        pico=False,
        secret_key="plans-test-cookie-secret",
        sess_https_only=False,
        same_site="lax",
        canonical=False,
    )
    site_routes.register_site_routes(rt)

    @rt("/_sign-in", methods=["POST"])
    async def sign_in(session, request):
        form = await request.form()
        user_id = str(form.get("user_id", ""))
        session["user_id"] = user_id
        session["role"] = "admin"
        return JSONResponse({"ok": True, "user_id": user_id})

    yield SimpleNamespace(
        client=TestClient(app),
        sessions=sessions,
    )
    engine.dispose()


def sign_in(workspace, user_id):
    workspace.client.post("/_sign-in", data={"user_id": user_id})


def seeded_account(workspace, email="merchant@example.test"):
    """A provisioned account: one user plus its first site (like signup)."""
    with workspace.sessions() as db:
        user = User(email=email, name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Quota test", "quota-test")
        db.commit()
        return user.id, site.id, site.tenant_id


def seed_products(db, tenant_id, count):
    product_type = ProductType(tenant_id=tenant_id, name="Physical product", slug="physical")
    category = Category(tenant_id=tenant_id, name="Our collection", slug="collection")
    db.add_all([product_type, category])
    db.flush()
    for index in range(count):
        db.add(Product(
            tenant_id=tenant_id,
            product_type_id=product_type.id,
            category_id=category.id,
            slug=f"filled-{index}",
            name=f"Filled {index}",
        ))
    db.flush()


def test_site_quota_blocks_third_site_with_merchant_readable_notice(workspace):
    user_id, _site_id, _tenant_id = seeded_account(workspace)
    with workspace.sessions() as db:
        content.create_site(db, user_id, "Second site", "second-quota-site")
        db.commit()
    sign_in(workspace, user_id)
    token = csrf(workspace.client.get("/admin/sites"))
    response = workspace.client.post(
        "/admin/sites",
        data={"csrf_token": token, "name": "Third site", "slug": "third-quota-site"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/sites?notice=")
    rendered = workspace.client.get(response.headers["location"])
    assert rendered.status_code == 200
    assert "Your Free plan allows 2 sites" in rendered.text
    assert "you are now using 2 of 2" in rendered.text
    with workspace.sessions() as db:
        assert int(db.scalar(select(func.count(Tenant.id)))) == 2


def test_ai_generation_quota_blocks_route_with_reset_notice(workspace):
    user_id, site_id, tenant_id = seeded_account(workspace)
    month_start, reset = plans.month_window(datetime.now(UTC))
    with workspace.sessions() as db:
        db.add(UsageEvent(
            tenant_id=tenant_id, site_id=site_id, user_id=user_id,
            kind="ai_generation", quantity=3, created_at=month_start,
        ))
        db.commit()
    sign_in(workspace, user_id)
    token = csrf(workspace.client.get("/admin/sites"))
    response = workspace.client.post(
        "/admin/sites/generate",
        data={"csrf_token": token, "business_name": "Blocked Co", "kind": "SaaS",
              "audience": "small teams", "tone": "minimal and precise"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/sites?notice=")
    rendered = workspace.client.get(response.headers["location"])
    assert "you are now using 3 of 3" in rendered.text
    assert f"Credits reset on {plans.human_reset(reset)}" in rendered.text
    with workspace.sessions() as db:
        assert db.scalar(select(Tenant.id).where(Tenant.name == "Blocked Co")) is None


def test_ai_generation_is_metered_only_after_success(workspace, monkeypatch):
    user_id, _site_id, _tenant_id = seeded_account(workspace)
    original = site_generation.generate_plan
    monkeypatch.setattr(
        site_generation, "generate_plan",
        lambda value: original(value, force_guided=True),
    )
    sign_in(workspace, user_id)
    token = csrf(workspace.client.get("/admin/sites"))
    response = workspace.client.post(
        "/admin/sites/generate",
        data={"csrf_token": token, "business_name": "Metered Co", "kind": "SaaS",
              "audience": "small teams", "tone": "minimal and precise"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/sites/")
    with workspace.sessions() as db:
        tenant_id = db.scalar(select(Site.tenant_id).where(Site.name == "Metered Co"))
        event = db.scalar(select(UsageEvent).where(
            UsageEvent.tenant_id == tenant_id, UsageEvent.kind == "ai_generation"))
        assert event is not None and event.quantity == 1
    # The dashboard read stays open and shows the metered panel.
    sign_in(workspace, user_id)
    dashboard = workspace.client.get("/admin/sites")
    assert dashboard.status_code == 200
    assert "Plan &amp; usage — Free" in dashboard.text
    assert "AI generation credits: 1 of 3" in dashboard.text


def test_month_window_counts_only_current_month(workspace):
    user_id, site_id, tenant_id = seeded_account(workspace)
    previous = datetime(2026, 9, 15, tzinfo=UTC)
    current = datetime(2026, 10, 2, tzinfo=UTC)
    with workspace.sessions() as db:
        db.add_all([
            UsageEvent(tenant_id=tenant_id, site_id=site_id, user_id=user_id,
                       kind="ai_generation", quantity=9,
                       created_at=previous, updated_at=previous),
            UsageEvent(tenant_id=tenant_id, site_id=site_id, user_id=user_id,
                       kind="ai_generation", quantity=3,
                       created_at=current, updated_at=current),
        ])
        db.commit()
        used, reset = plans.ai_credits_used(db, user_id, now=datetime(2026, 10, 15, tzinfo=UTC))
        assert used == 3
        assert reset == datetime(2026, 11, 1)
        plans.ensure_ai_generations(db, user_id, now=datetime(2026, 11, 1, 0, 0, 1, tzinfo=UTC))
        with pytest.raises(plans.QuotaExceeded) as blocked:
            plans.ensure_ai_generations(db, user_id, now=datetime(2026, 10, 15, tzinfo=UTC))
        assert "reset on 1 November 2026" in str(blocked.value)


def test_product_quota_blocks_catalog_creation(workspace):
    user_id, site_id, tenant_id = seeded_account(workspace)
    with workspace.sessions() as db:
        seed_products(db, tenant_id, 10)
        db.commit()
    sign_in(workspace, user_id)
    token = csrf(workspace.client.get(f"/admin/sites/{site_id}/products"))
    response = workspace.client.post(
        f"/admin/sites/{site_id}/products",
        data={"csrf_token": token, "name": "Overflow product", "slug": "overflow-product",
              "variants": "Original"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/admin/sites/{site_id}/products?notice=")
    rendered = workspace.client.get(response.headers["location"])
    assert "Your Free plan allows 10 products" in rendered.text
    assert "Overflow product" not in rendered.text
    with workspace.sessions() as db:
        assert db.scalar(select(Product.id).where(Product.slug == "overflow-product")) is None
        # Nothing was created: the ledger has no product_created event either.
        assert db.scalar(select(UsageEvent.id).where(
            UsageEvent.kind == "product_created")) is None


def test_catalog_creation_is_metered_on_success(workspace):
    user_id, site_id, tenant_id = seeded_account(workspace)
    sign_in(workspace, user_id)
    token = csrf(workspace.client.get(f"/admin/sites/{site_id}/products"))
    response = workspace.client.post(
        f"/admin/sites/{site_id}/products",
        data={"csrf_token": token, "name": "Metered mug", "slug": "metered-mug",
              "variants": "Original"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with workspace.sessions() as db:
        event = db.scalar(select(UsageEvent).where(
            UsageEvent.tenant_id == tenant_id,
            UsageEvent.kind == "product_created",
            UsageEvent.site_id == site_id,
        ))
        assert event is not None and event.quantity == 1
        assert db.scalar(select(Product.id).where(Product.slug == "metered-mug")) is not None


def test_builder_product_proposal_blocked_when_quota_exhausted(workspace):
    user_id, site_id, tenant_id = seeded_account(workspace)
    with workspace.sessions() as db:
        site = db.get(Site, site_id)
        seed_products(db, tenant_id, 10)
        db.flush()
        proposal = {"kind": "product", "name": "Proposed product", "slug": "proposed-product",
                    "variants": [{"name": "Original", "price_minor": 1200}]}
        turn = SiteBuilderTurn(
            tenant_id=tenant_id, site_id=site_id, user_id=user_id,
            command_id="blocked-proposal", prompt="Propose a product",
            status="complete", context_json={},
            response_json={
                "proposals": [proposal],
                "review_status": "pending",
                "review_context": site_builder_reviews.review_context(db, site),
                "review_site_version": site.version,
            },
        )
        db.add(turn)
        db.commit()
        with pytest.raises(plans.QuotaExceeded):
            site_builder_reviews._decide(db, site_id, user_id, turn.id, approve=True)
        db.rollback()
        db.expire_all()
        assert db.scalar(select(Product.id).where(Product.slug == "proposed-product")) is None


class StubConnector:
    platform = "csv"
    label = "Stub CSV"
    capabilities = connectors.ConnectorCapabilities(
        imports=("catalog",), exports=(), max_pages=1, max_items=10, timeout_seconds=5,
    )

    def __init__(self):
        self.applied = []

    def credential_state(self, site):
        return connectors.CredentialState(configured=True, message="ready")

    def fetch(self, site, *, transport=None):
        return {}

    def dry_run(self, db, site, user_id, *, transport=None):
        return {}, {}

    def apply_import(self, db, site, user_id, payload):
        self.applied.append(payload)
        product_type = db.scalar(select(ProductType).where(ProductType.tenant_id == site.tenant_id))
        if product_type is None:
            product_type = ProductType(tenant_id=site.tenant_id, name="Physical product", slug="physical")
            db.add(product_type)
        category = db.scalar(select(Category).where(Category.tenant_id == site.tenant_id))
        if category is None:
            category = Category(tenant_id=site.tenant_id, name="Our collection", slug="collection")
            db.add(category)
        db.flush()
        for suffix in ("new-1", "new-2"):
            db.add(Product(
                tenant_id=site.tenant_id,
                product_type_id=product_type.id,
                category_id=category.id,
                slug=f"stub-{suffix}",
                name=f"Stub {suffix}",
            ))
        db.flush()
        return {"products": {"created": 2, "updated": 1}}

    def export_bundle(self, db, site):
        return {}


def use_stub_csv(stub):
    """Swap the built-in csv connector for a stub that must never run."""
    connectors._load_builtins()
    saved = connectors._REGISTRY["csv"]
    connectors._REGISTRY["csv"] = stub
    return saved


def test_connector_import_fails_as_bounded_whole_when_quota_exhausted(workspace):
    stub = StubConnector()
    saved = use_stub_csv(stub)
    try:
        user_id, site_id, tenant_id = seeded_account(workspace)
        with workspace.sessions() as db:
            site = db.get(Site, site_id)
            seed_products(db, tenant_id, 10)
            plan = IntegrationPlan(
                tenant_id=tenant_id, site_id=site_id, user_id=user_id,
                platform="csv", operation="import", schema_version=1, version=1,
                status="pending", report_json={},
                payload_json={"rows": [
                    {"external_id": "fresh-1", "name": "Fresh 1"},
                    {"external_id": "fresh-2", "name": "Fresh 2"},
                ]},
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            db.add(plan)
            db.commit()
            assert connectors.planned_new_products(db, site, "csv", plan.payload_json) == 2
            with pytest.raises(plans.QuotaExceeded):
                connectors.apply_reviewed_plan(db, site, user_id, plan.id, 1)
            db.rollback()
            # Bounded whole: the plan stays pending, nothing imported, no ledger.
            assert stub.applied == []
            assert db.get(IntegrationPlan, plan.id).status == "pending"
            assert db.scalar(select(Product.id).where(Product.slug == "stub-new-1")) is None
            assert db.scalar(select(UsageEvent.id).where(
                UsageEvent.kind == "product_created")) is None
    finally:
        connectors._REGISTRY["csv"] = saved


def test_connector_import_meters_created_products(workspace):
    stub = StubConnector()
    saved = use_stub_csv(stub)
    try:
        user_id, site_id, tenant_id = seeded_account(workspace)
        with workspace.sessions() as db:
            db.add(ProductType(tenant_id=tenant_id, name="Physical product", slug="physical"))
            db.add(Category(tenant_id=tenant_id, name="Our collection", slug="collection"))
            db.add(ExternalMapping(
                tenant_id=tenant_id, site_id=site_id, system="csv",
                resource_type="row", external_id="known-1", local_id="row-1",
            ))
            db.flush()
            site = db.get(Site, site_id)
            plan = IntegrationPlan(
                tenant_id=tenant_id, site_id=site_id, user_id=user_id,
                platform="csv", operation="import", schema_version=1, version=1,
                status="pending", report_json={},
                payload_json={"rows": [
                    {"external_id": "known-1", "name": "Known row"},
                    {"external_id": "fresh-1", "name": "Fresh 1"},
                    {"external_id": "fresh-2", "name": "Fresh 2"},
                ]},
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            db.add(plan)
            db.commit()
            assert connectors.planned_new_products(db, site, "csv", plan.payload_json) == 2
            _plan, result = connectors.apply_reviewed_plan(db, site, user_id, plan.id, 1)
            assert result["products"]["created"] == 2
            db.commit()
            event = db.scalar(select(UsageEvent).where(
                UsageEvent.tenant_id == tenant_id,
                UsageEvent.kind == "product_created",
            ))
            assert event is not None and event.quantity == 2
    finally:
        connectors._REGISTRY["csv"] = saved


def test_publish_quota_blocks_third_publication(workspace, monkeypatch):
    user_id, site_id, _tenant_id = seeded_account(workspace)
    with workspace.sessions() as db:
        first = content.create_site(db, user_id, "Publish one", "publish-one")
        second = content.create_site(db, user_id, "Publish two", "publish-two")
        db.commit()
        from app import site_golive

        # Readiness assessment is not the seam under test; give every site a
        # clean report so the quota check is what fires.
        monkeypatch.setattr(
            site_golive, "assess",
            lambda db, site: SimpleNamespace(failures=[], checks=[]),
        )
        reason = "Reviewed publication ready for the quota test."
        for target in (site_id, first.id):
            site = db.get(Site, target)
            decision = site_golive.publish_site(db, target, user_id, site.version, reason)
            assert decision.applied
        db.commit()
        with pytest.raises(plans.QuotaExceeded) as blocked:
            site_golive.publish_site(
                db, second.id, user_id, db.get(Site, second.id).version, reason,
            )
        assert "published sites" in str(blocked.value)
        db.rollback()
        db.expire_all()
        assert db.get(Site, second.id).status == "draft"
        assert plans.count_published(db, user_id) == 2


def test_platform_operator_gate_denies_other_users(workspace):
    user_id, _site_id, _tenant_id = seeded_account(workspace)
    sign_in(workspace, user_id)
    response = workspace.client.get("/admin/platform/plans")
    assert response.status_code == 400
    assert "Platform operator access is required." in response.text


def test_operator_console_lists_tenants_and_changes_plans(workspace, monkeypatch):
    # require_platform_operator reads app.config.settings at call time.
    monkeypatch.setattr("app.config.settings", SimpleNamespace(admin_email=" Operator@Example.test "))
    user_id, _site_id, tenant_id = seeded_account(workspace)
    with workspace.sessions() as db:
        operator = User(email="operator@example.test", name="Operator")
        db.add(operator)
        db.commit()
        operator_id = operator.id
    sign_in(workspace, operator_id)
    listing = workspace.client.get("/admin/platform/plans")
    assert listing.status_code == 200
    assert "Plans &amp; quotas" in listing.text
    assert "Quota test" in listing.text
    assert "site_created: 1" in listing.text
    token = csrf(listing)
    response = workspace.client.post(
        f"/admin/platform/tenants/{tenant_id}/plan",
        data={"csrf_token": token, "plan": "basic"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    rendered = workspace.client.get(response.headers["location"])
    assert "Plan updated: Basic" in rendered.text
    with workspace.sessions() as db:
        assert db.get(Tenant, tenant_id).plan == "basic"
    # Idempotent repeat of the same assignment stays a clean round.
    again = workspace.client.post(
        f"/admin/platform/tenants/{tenant_id}/plan",
        data={"csrf_token": token, "plan": "basic"},
        follow_redirects=False,
    )
    assert again.status_code == 303
    with workspace.sessions() as db:
        assert db.get(Tenant, tenant_id).plan == "basic"


def test_reads_stay_open_when_every_quota_is_exhausted(workspace):
    user_id, site_id, tenant_id = seeded_account(workspace)
    with workspace.sessions() as db:
        content.create_site(db, user_id, "Second site", "second-quota-site")
        seed_products(db, tenant_id, 10)
        db.add(UsageEvent(
            tenant_id=tenant_id, site_id=site_id, user_id=user_id,
            kind="ai_generation", quantity=3, created_at=plans.month_window(datetime.now(UTC))[0],
        ))
        db.commit()
    sign_in(workspace, user_id)
    dashboard = workspace.client.get("/admin/sites")
    assert dashboard.status_code == 200
    assert "Plan &amp; usage — Free" in dashboard.text
    assert "Sites: 2 of 2" in dashboard.text
    assert workspace.client.get(f"/admin/sites/{site_id}/products").status_code == 200


def test_ledger_records_and_usage_views(workspace):
    user_id, site_id, tenant_id = seeded_account(workspace)
    with workspace.sessions() as db:
        plans.record(db, tenant_id, "product_created", site_id=site_id, user_id=user_id, quantity=4)
        plans.record(db, tenant_id, "ai_generation", site_id=site_id, user_id=user_id)
        plans.record(db, tenant_id, "published", site_id=site_id, user_id=user_id)
        db.commit()
        assert plans.tenant_usage(db, tenant_id) == {
            "site_created": 1, "ai_generation": 1, "product_created": 4, "published": 1,
        }
        plan, rows = plans.account_usage(db, user_id)
        assert plan.id == "free"
        by_key = {row.key: row for row in rows}
        assert by_key["sites"].used == 1 and by_key["sites"].limit == 2
        assert by_key["ai_generations"].used == 1 and by_key["ai_generations"].resets_at
        assert by_key["products"].used == 0 and by_key["products"].limit == 10
        # The ledger records consumption; the products quota counts live rows.
        assert by_key["published"].used == 0