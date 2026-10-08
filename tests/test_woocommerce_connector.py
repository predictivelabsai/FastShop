import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app import connectors, content
from app.config import settings
from app.db import SessionLocal
from app.integrations.woocommerce import (
    WooCommerceConnector,
    _fixture_transport,
    decimal_amount,
    money_minor,
)
from app.main import app
from app.models import (
    Base,
    Category,
    ExternalMapping,
    IntegrationPlan,
    Order,
    OrderLine,
    Product,
    ProductVariant,
    ShopCustomer,
    SiteOrder,
    User,
    VariantChannelListing,
)
from app.services import CommerceError

FIXTURE = Path(__file__).parent / "fixtures" / "woocommerce_store.json"


@pytest.fixture
def workspace(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="woo-owner@example.test", name="Woo owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Woo target", "woo-target")
        prefix = f"FASTSHOP_WOOCOMMERCE_{site.id.upper()}_"
        for name, value in {
            "ENABLED": "true",
            "URL": "https://store.example.test",
            "CONSUMER_KEY": "ck_fixture",
            "CONSUMER_SECRET": "cs_fixture",
            "CURRENCY": "USD",
            "CURRENCY_SCALE": "2",
        }.items():
            monkeypatch.setenv(prefix + name, value)
        db.commit()
        yield db, owner, site
    engine.dispose()


def test_money_uses_source_scale_half_up_and_exact_export():
    assert money_minor("31.005", currency="USD", scale=2) == 3101
    assert money_minor("149.5", currency="JPY", scale=0) == 150
    assert money_minor("1.2345", currency="KWD", scale=3) == 1235
    assert decimal_amount(3101, 2) == "31.01"
    assert decimal_amount(150, 0) == "150"
    with pytest.raises(CommerceError):
        money_minor("1e3", currency="USD", scale=2)


def test_dry_run_apply_reimport_and_export_round_trip(workspace):
    db, owner, site = workspace
    transport = _fixture_transport(FIXTURE)
    before = {
        Product: db.scalar(select(func.count()).select_from(Product)),
        Order: db.scalar(select(func.count()).select_from(Order)),
        ShopCustomer: db.scalar(select(func.count()).select_from(ShopCustomer)),
    }
    plan = connectors.create_dry_run(db, site, owner.id, "woocommerce", transport=transport)
    assert plan.report_json["counts"]["products"] == {
        "fetched": 2, "create": 2, "update": 0, "skip": 0,
    }
    assert any(item["type"] == "customer" for item in plan.report_json["unmapped_items"])
    assert any(item["type"] == "order_line" for item in plan.report_json["unmapped_items"])
    assert before == {
        Product: db.scalar(select(func.count()).select_from(Product)),
        Order: db.scalar(select(func.count()).select_from(Order)),
        ShopCustomer: db.scalar(select(func.count()).select_from(ShopCustomer)),
    }

    _, applied = connectors.apply_reviewed_plan(db, site, owner.id, plan.id, plan.version)
    db.flush()
    assert applied["products"] == {"created": 2, "updated": 0}
    assert applied["orders"] == {"created": 1, "updated": 0}
    assert db.scalar(select(func.count()).select_from(Category).where(
        Category.tenant_id == site.tenant_id)) == 3  # two source categories + one fallback
    assert db.scalar(select(func.count()).select_from(Product).where(
        Product.tenant_id == site.tenant_id)) == 2
    assert db.scalar(select(func.count()).select_from(ProductVariant).where(
        ProductVariant.tenant_id == site.tenant_id)) == 3
    assert db.scalar(select(func.count()).select_from(ShopCustomer).where(
        ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id)) == 1
    assert db.scalar(select(func.count()).select_from(SiteOrder).where(
        SiteOrder.site_id == site.id, SiteOrder.tenant_id == site.tenant_id)) == 1
    assert db.scalar(select(func.count()).select_from(OrderLine)) == 2
    rounded = db.scalar(select(VariantChannelListing).join(
        ProductVariant, ProductVariant.id == VariantChannelListing.variant_id
    ).where(ProductVariant.sku == "EVENING-40"))
    assert rounded.price_minor == 3101
    with pytest.raises(CommerceError, match="already been used"):
        connectors.apply_reviewed_plan(db, site, owner.id, plan.id, plan.version + 1)

    totals = {model: db.scalar(select(func.count()).select_from(model)) for model in
              (Category, Product, ProductVariant, ShopCustomer, Order, SiteOrder, ExternalMapping)}
    second = connectors.create_dry_run(
        db, site, owner.id, "woocommerce", transport=_fixture_transport(FIXTURE)
    )
    assert second.report_json["counts"]["products"]["update"] == 2
    _, reapplied = connectors.apply_reviewed_plan(db, site, owner.id, second.id, second.version)
    db.flush()
    assert reapplied["products"] == {"created": 0, "updated": 2}
    assert {model: db.scalar(select(func.count()).select_from(model)) for model in totals} == totals

    bundle = connectors.export_bundle(db, site, "woocommerce")
    exported = {entry["external_id"]: entry["payload"] for entry in bundle["products"]}
    assert bundle["mode"] == "review-only" and bundle["currency"] == "USD"
    assert exported["100"]["variations"][0]["regular_price"] == "29.95"
    assert exported["100"]["variations"][1]["regular_price"] == "31.01"
    assert exported["101"]["regular_price"] == "18.50"


def test_mapping_and_apply_are_site_tenant_scoped(workspace, monkeypatch):
    db, owner, site = workspace
    other_owner = User(email="other-woo@example.test", name="Other")
    db.add(other_owner)
    db.flush()
    other = content.create_site(db, other_owner.id, "Other Woo", "other-woo")
    prefix = f"FASTSHOP_WOOCOMMERCE_{other.id.upper()}_"
    for name, value in {"ENABLED": "true", "URL": "https://other.example.test",
                        "CONSUMER_KEY": "ck_fixture", "CONSUMER_SECRET": "cs_fixture"}.items():
        monkeypatch.setenv(prefix + name, value)
    plan = connectors.create_dry_run(
        db, other, other_owner.id, "woocommerce", transport=_fixture_transport(FIXTURE)
    )
    connectors.apply_reviewed_plan(db, other, other_owner.id, plan.id, plan.version)
    assert db.scalar(select(func.count()).select_from(ExternalMapping).where(
        ExternalMapping.site_id == other.id, ExternalMapping.tenant_id == other.tenant_id)) > 0
    assert db.scalar(select(func.count()).select_from(ExternalMapping).where(
        ExternalMapping.site_id == site.id, ExternalMapping.tenant_id == site.tenant_id)) == 0
    with pytest.raises(CommerceError):
        connectors.apply_reviewed_plan(db, site, owner.id, plan.id, plan.version + 1)


def test_pagination_ceiling_is_enforced(workspace):
    _, _, site = workspace

    def respond(request):
        if request.url.path.endswith("/categories"):
            return httpx.Response(200, json=[{"id": i + 1, "name": "Category"} for i in range(50)])
        return httpx.Response(200, json=[])

    with pytest.raises(CommerceError, match="5-page import limit"):
        WooCommerceConnector().fetch(site, transport=httpx.MockTransport(respond))


def test_empty_connector_migration_and_alembic_check(tmp_path):
    path = tmp_path / "empty-connectors.db"
    environment = os.environ | {"DB_URL": "sqlite:///" + path.as_posix()}
    for args in (("upgrade", "head"), ("check",)):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], env=environment,
            capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    engine = create_engine(environment["DB_URL"])
    assert {"site_id", "external_id"} <= {column["name"] for column in inspect(engine).get_columns("external_mappings")}
    assert "integration_plans" in inspect(engine).get_table_names()
    engine.dispose()


def test_populated_pre_connector_migration_preserves_legacy_mapping(tmp_path):
    path = tmp_path / "populated-connectors.db"
    url = "sqlite:///" + path.as_posix()
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = User(email="connector-migration@example.test", name="Migration")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Legacy connector", "legacy-connector")
        tenant_id = site.tenant_id
        db.commit()
    IntegrationPlan.__table__.drop(engine)
    ExternalMapping.__table__.drop(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE external_mappings (id VARCHAR(32) NOT NULL PRIMARY KEY, tenant_id VARCHAR(32) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, system VARCHAR(50) NOT NULL, resource_type VARCHAR(80) NOT NULL, local_id VARCHAR(64) NOT NULL, external_id VARCHAR(140) NOT NULL, version VARCHAR(80) NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, UNIQUE (tenant_id, system, resource_type, local_id))"))
        connection.execute(text("CREATE INDEX ix_external_mappings_tenant_id ON external_mappings (tenant_id)"))
        connection.execute(text("INSERT INTO external_mappings (id, tenant_id, system, resource_type, local_id, external_id, version) VALUES ('legacy', :tenant, 'legacy-system', 'product', 'local-1', 'external-1', 'v1')"), {"tenant": tenant_id})
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('20261008_0020')"))
    environment = os.environ | {"DB_URL": url}
    for args in (("upgrade", "head"), ("check",)):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], env=environment,
            capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    with engine.connect() as connection:
        row = connection.execute(text("SELECT external_id, version, site_id FROM external_mappings WHERE id='legacy'"))
        assert row.one() == ("external-1", "v1", None)
    assert "integration_plans" in inspect(engine).get_table_names()
    engine.dispose()


def _signed_in():
    client = TestClient(app)
    login = client.get("/login?next=/admin/sites")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login.text).group(1)
    response = client.post("/login", data={"csrf_token": token, "email": settings.admin_email,
                                           "password": settings.admin_password})
    assert response.status_code == 200
    return client, token


def test_site_integration_routes_review_exact_plan_and_prg(monkeypatch):
    client, token = _signed_in()
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, owner.id, "Connector route " + uuid4().hex[:6],
                                   "connector-" + uuid4().hex[:8])
        site_id = site.id
        db.commit()
    base = f"/admin/sites/{site_id}/integrations"
    missing = client.get(base)
    assert "Needs operator setup" in missing.text
    assert "Ask the operator" in missing.text
    assert "Run import dry run" not in missing.text

    monkeypatch.setenv("FASTSHOP_WOOCOMMERCE_FIXTURE_PATH", str(FIXTURE))
    page = client.get(base)
    assert page.status_code == 200
    assert "WooCommerce" in page.text and "Development fixture store ready" in page.text
    assert "CONSUMER_SECRET" not in page.text and "ck_fixture" not in page.text

    denied = client.post(base + "/woocommerce/dry-run", data={}, follow_redirects=False)
    assert denied.status_code == 303 and "session+expired" in denied.headers["location"]
    preview = client.post(base + "/woocommerce/dry-run", data={"csrf_token": token},
                          follow_redirects=False)
    assert preview.status_code == 303 and "plan=" in preview.headers["location"]
    review = client.get(preview.headers["location"])
    assert "Reviewed import plan" in review.text and "Archived gift wrap" in review.text
    plan_id = re.search(r'name="plan_id" value="([^"]+)"', review.text).group(1)
    version = re.search(r'name="plan_version" value="([^"]+)"', review.text).group(1)

    unconfirmed = client.post(base + "/woocommerce/apply", data={"csrf_token": token,
        "plan_id": plan_id, "plan_version": version}, follow_redirects=False)
    assert unconfirmed.status_code == 303 and "Confirm+that+you+reviewed" in unconfirmed.headers["location"]
    applied = client.post(base + "/woocommerce/apply", data={"csrf_token": token,
        "plan_id": plan_id, "plan_version": version, "confirmed": "on"}, follow_redirects=False)
    assert applied.status_code == 303 and "successfully" in applied.headers["location"]
    replay = client.post(base + "/woocommerce/apply", data={"csrf_token": token,
        "plan_id": plan_id, "plan_version": version, "confirmed": "on"}, follow_redirects=False)
    assert replay.status_code == 303 and "already+been+used" in replay.headers["location"]

    exported = client.get(base + "/woocommerce/export.json")
    assert exported.status_code == 200 and exported.json()["mode"] == "review-only"
    assert exported.headers["cache-control"] == "private, no-store"
    with SessionLocal() as db:
        plan = db.get(IntegrationPlan, plan_id)
        assert plan.status == "applied" and plan.consumed_at is not None
