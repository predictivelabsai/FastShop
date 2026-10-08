import json
import re
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app import connectors, content
from app.config import settings
from app.db import SessionLocal
from app.integrations.shopify import (
    API_VERSION,
    ShopifyConnector,
    _fixture_transport,
    currency_scale,
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

FIXTURE = Path(__file__).parent / "fixtures" / "shopify_store.json"


@pytest.fixture
def workspace(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="shopify-owner@example.test", name="Shopify owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Shopify target", "shopify-target")
        prefix = f"FASTSHOP_SHOPIFY_{site.id.upper()}_"
        monkeypatch.setenv(prefix + "ENABLED", "true")
        monkeypatch.setenv(prefix + "SHOP", "migration-dev.myshopify.com")
        monkeypatch.setenv(prefix + "ADMIN_API_ACCESS_TOKEN", "shpat_offline_test_token")
        db.commit()
        yield db, owner, site
    engine.dispose()


def test_money_uses_source_scale_half_up_and_rejects_non_strings():
    assert currency_scale("USD") == 2
    assert currency_scale("JPY") == 0
    assert currency_scale("KWD") == 3
    assert money_minor("31.005", currency="USD", scale=2) == 3101
    assert money_minor("149.5", currency="JPY", scale=0) == 150
    assert money_minor("1.2345", currency="KWD", scale=3) == 1235
    for value in (1.25, "1e3", "NaN", "-1.00"):
        with pytest.raises(CommerceError):
            money_minor(value, currency="USD", scale=2)


def test_graphql_uses_versioned_endpoint_token_header_and_post(workspace):
    _, _, site = workspace
    seen = {}

    def respond(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["token"] = request.headers.get("x-shopify-access-token")
        body = json.loads(request.content)
        assert body["operationName"] == "Shop"
        return httpx.Response(200, json={"data": {"shop": {"currencyCode": "USD"}}})

    from app.integrations.shopify import ShopifyGateway

    gateway = ShopifyGateway(site, tenant_id=site.tenant_id, transport=httpx.MockTransport(respond))
    assert gateway.shop_currency() == "USD"
    assert seen == {
        "method": "POST",
        "path": f"/admin/api/{API_VERSION}/graphql.json",
        "token": "shpat_offline_test_token",
    }


def test_dry_run_apply_and_reimport_are_idempotent(workspace):
    db, owner, site = workspace
    before = {
        Product: db.scalar(select(func.count()).select_from(Product)),
        Order: db.scalar(select(func.count()).select_from(Order)),
        ShopCustomer: db.scalar(select(func.count()).select_from(ShopCustomer)),
    }
    plan = connectors.create_dry_run(
        db, site, owner.id, "shopify", transport=_fixture_transport(FIXTURE)
    )
    assert plan.report_json["counts"]["collections"] == {
        "fetched": 2, "create": 2, "update": 0, "skip": 0,
    }
    assert plan.report_json["counts"]["products"] == {
        "fetched": 2, "create": 2, "update": 0, "skip": 0,
    }
    assert any(item["type"] == "customer" for item in plan.report_json["unmapped_items"])
    assert any(item["type"] == "product" for item in plan.report_json["unmapped_items"])
    assert any(item["type"] == "order_line" for item in plan.report_json["unmapped_items"])
    assert any("shop currency" in item for item in plan.report_json["warnings"])
    assert before == {
        Product: db.scalar(select(func.count()).select_from(Product)),
        Order: db.scalar(select(func.count()).select_from(Order)),
        ShopCustomer: db.scalar(select(func.count()).select_from(ShopCustomer)),
    }

    _, applied = connectors.apply_reviewed_plan(db, site, owner.id, plan.id, plan.version)
    db.flush()
    assert applied["collections"] == {"created": 2, "updated": 0}
    assert applied["products"] == {"created": 2, "updated": 0}
    assert applied["orders"] == {"created": 1, "updated": 0}
    assert db.scalar(select(func.count()).select_from(Category).where(
        Category.tenant_id == site.tenant_id)) == 3
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
    imported_order = db.scalar(select(Order).where(Order.tenant_id == site.tenant_id))
    assert imported_order.currency == "USD" and imported_order.total_minor == 7369

    totals = {
        model: db.scalar(select(func.count()).select_from(model))
        for model in (Category, Product, ProductVariant, ShopCustomer, Order, SiteOrder, ExternalMapping)
    }
    second = connectors.create_dry_run(
        db, site, owner.id, "shopify", transport=_fixture_transport(FIXTURE)
    )
    assert all(
        second.report_json["counts"][name]["create"] == 0
        for name in ("collections", "products", "customers", "orders")
    )
    _, reapplied = connectors.apply_reviewed_plan(db, site, owner.id, second.id, second.version)
    db.flush()
    assert reapplied["products"] == {"created": 0, "updated": 2}
    assert reapplied["orders"] == {"created": 0, "updated": 1}
    assert {model: db.scalar(select(func.count()).select_from(model)) for model in totals} == totals


def test_mapping_and_apply_are_site_tenant_scoped(workspace, monkeypatch):
    db, owner, site = workspace
    other_owner = User(email="other-shopify@example.test", name="Other")
    db.add(other_owner)
    db.flush()
    other = content.create_site(db, other_owner.id, "Other Shopify", "other-shopify")
    prefix = f"FASTSHOP_SHOPIFY_{other.id.upper()}_"
    monkeypatch.setenv(prefix + "ENABLED", "true")
    monkeypatch.setenv(prefix + "SHOP", "other-dev.myshopify.com")
    monkeypatch.setenv(prefix + "ADMIN_API_ACCESS_TOKEN", "shpat_other_test_token")
    plan = connectors.create_dry_run(
        db, other, other_owner.id, "shopify", transport=_fixture_transport(FIXTURE)
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
        body = json.loads(request.content)
        operation = body["operationName"]
        if operation == "Shop":
            data = {"shop": {"currencyCode": "USD"}}
        elif operation == "Collections":
            nodes = [
                {"id": f"gid://shopify/Collection/{index}", "title": "Collection"}
                for index in range(1, 51)
            ]
            data = {"collections": {
                "nodes": nodes,
                "pageInfo": {"hasNextPage": True, "endCursor": uuid4().hex},
            }}
        else:
            raise AssertionError(operation)
        return httpx.Response(200, json={"data": data})

    with pytest.raises(CommerceError, match="5-page import limit"):
        ShopifyConnector().fetch(site, transport=httpx.MockTransport(respond))


def _signed_in():
    client = TestClient(app)
    login = client.get("/login?next=/admin/sites")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login.text).group(1)
    response = client.post("/login", data={
        "csrf_token": token,
        "email": settings.admin_email,
        "password": settings.admin_password,
    })
    assert response.status_code == 200
    return client, token


def test_site_integration_routes_show_shopify_and_apply_exact_plan(monkeypatch):
    client, token = _signed_in()
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(
            db, owner.id, "Shopify route " + uuid4().hex[:6], "shopify-" + uuid4().hex[:8]
        )
        site_id = site.id
        db.commit()
    base = f"/admin/sites/{site_id}/integrations"
    missing = client.get(base)
    assert missing.status_code == 200
    assert "Shopify" in missing.text and "Needs operator setup" in missing.text

    monkeypatch.setenv("FASTSHOP_SHOPIFY_FIXTURE_PATH", str(FIXTURE))
    page = client.get(base)
    assert page.status_code == 200
    assert "Shopify" in page.text and "Development fixture store ready" in page.text
    assert "shpat_fixture" not in page.text and "ADMIN_API_ACCESS_TOKEN" not in page.text

    denied = client.post(base + "/shopify/dry-run", data={}, follow_redirects=False)
    assert denied.status_code == 303 and "session+expired" in denied.headers["location"]
    preview = client.post(
        base + "/shopify/dry-run", data={"csrf_token": token}, follow_redirects=False
    )
    assert preview.status_code == 303 and "plan=" in preview.headers["location"]
    review = client.get(preview.headers["location"])
    assert "Reviewed import plan" in review.text and "Archived gift wrap" in review.text
    assert "Collections" in review.text
    plan_id = re.search(r'name="plan_id" value="([^"]+)"', review.text).group(1)
    version = re.search(r'name="plan_version" value="([^"]+)"', review.text).group(1)

    wrong_connector = client.post(base + "/woocommerce/apply", data={
        "csrf_token": token,
        "plan_id": plan_id,
        "plan_version": version,
        "confirmed": "on",
    }, follow_redirects=False)
    assert "does+not+match" in wrong_connector.headers["location"]
    applied = client.post(base + "/shopify/apply", data={
        "csrf_token": token,
        "plan_id": plan_id,
        "plan_version": version,
        "confirmed": "on",
    }, follow_redirects=False)
    assert applied.status_code == 303 and "successfully" in applied.headers["location"]
    with SessionLocal() as db:
        plan = db.get(IntegrationPlan, plan_id)
        assert plan.status == "applied" and plan.consumed_at is not None
