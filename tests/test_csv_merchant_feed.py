import csv
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app import connectors, content
from app.config import settings
from app.db import SessionLocal
from app.integrations.csv_catalog import MAX_CELL_CHARS, MAX_FILE_BYTES
from app.main import app
from app.models import (
    Base,
    ExternalMapping,
    IntegrationPlan,
    Product,
    ProductVariant,
    Stock,
    User,
    VariantChannelListing,
)
from app.services import CommerceError

FIXTURE = Path(__file__).parent / "fixtures" / "csv_catalog_edge_cases.csv"
G = "http://base.google.com/ns/1.0"


@pytest.fixture
def workspace():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="csv-owner@example.test", name="CSV owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "CSV target", "csv-target")
        db.commit()
        yield db, owner, site
    engine.dispose()


def _fixture_bytes():
    text = FIXTURE.read_text(encoding="utf-8").replace("\n", "\r\n")
    return b"\xef\xbb\xbf" + text.encode("utf-8")


def _counts(db, site):
    return {
        "products": db.scalar(
            select(func.count()).select_from(Product).where(Product.tenant_id == site.tenant_id)
        ),
        "variants": db.scalar(
            select(func.count())
            .select_from(ProductVariant)
            .where(ProductVariant.tenant_id == site.tenant_id)
        ),
        "mappings": db.scalar(
            select(func.count())
            .select_from(ExternalMapping)
            .where(
                ExternalMapping.tenant_id == site.tenant_id,
                ExternalMapping.site_id == site.id,
                ExternalMapping.system == "csv",
            )
        ),
    }


def test_csv_bom_crlf_quoted_commas_apply_and_reimport_are_idempotent(workspace):
    db, owner, site = workspace
    before = _counts(db, site)
    plan = connectors.create_dry_run(db, site, owner.id, "csv", transport=_fixture_bytes())
    assert plan.report_json["counts"]["products"] == {
        "fetched": 2,
        "create": 2,
        "update": 0,
        "skip": 0,
    }
    assert plan.report_json["rows"][0]["name"] == "Trail Mug, Large"
    assert plan.payload_json["rows"][0]["description"] == "A quoted description, with a comma"
    assert plan.payload_json["rows"][0]["price_minor"] == 3101
    assert "content" not in plan.payload_json
    assert _counts(db, site) == before

    _, applied = connectors.apply_reviewed_plan(db, site, owner.id, plan.id, plan.version)
    assert applied == {"products": {"created": 2, "updated": 0}}
    after_first = _counts(db, site)
    assert after_first == {
        "products": before["products"] + 2,
        "variants": before["variants"] + 2,
        "mappings": 4,
    }
    mug = db.scalar(
        select(Product).where(
            Product.tenant_id == site.tenant_id,
            Product.name == "Trail Mug, Large",
        )
    )
    variant = db.scalar(select(ProductVariant).where(ProductVariant.product_id == mug.id))
    listing = db.scalar(
        select(VariantChannelListing).where(
            VariantChannelListing.variant_id == variant.id,
            VariantChannelListing.channel_id == site.channel_id,
        )
    )
    stock = db.scalar(select(Stock).where(Stock.variant_id == variant.id))
    assert listing.price_minor == 3101 and listing.currency == "USD"
    assert stock.quantity == 12 and variant.attributes_json == {"Color": "Blue", "Size": "Large"}

    second = connectors.create_dry_run(db, site, owner.id, "csv", transport=_fixture_bytes())
    assert second.report_json["counts"]["products"]["update"] == 2
    _, reapplied = connectors.apply_reviewed_plan(db, site, owner.id, second.id, second.version)
    assert reapplied == {"products": {"created": 0, "updated": 2}}
    assert _counts(db, site) == after_first


def test_csv_row_errors_are_explicit_for_prices_duplicates_and_oversize_cells(workspace):
    db, owner, site = workspace
    oversized = "x" * (MAX_CELL_CHARS + 1)
    source = (
        "name,sku,description,price,currency,category\r\n"
        "Negative,NEG,bad,-1,USD,Errors\r\n"
        "Zero,ZERO,bad,0,USD,Errors\r\n"
        "Duplicate,DUP,first,1.00,USD,Errors\r\n"
        "Duplicate again,DUP,second,2.00,USD,Errors\r\n"
        f"Oversize,BIG,{oversized},3.00,USD,Errors\r\n"
    ).encode()
    plan = connectors.create_dry_run(db, site, owner.id, "csv", transport=source)
    counts = plan.report_json["counts"]["products"]
    assert counts == {"fetched": 5, "create": 0, "update": 0, "skip": 5}
    rows = {row["row_number"]: row for row in plan.report_json["rows"]}
    assert "plain non-negative decimal" in " ".join(rows[2]["errors"])
    assert "resolve to" in " ".join(rows[3]["errors"])
    assert "duplicate CSV row identity" in " ".join(rows[4]["errors"])
    assert "duplicate CSV row identity" in " ".join(rows[5]["errors"])
    assert "cell limit" in " ".join(rows[6]["errors"])
    assert plan.payload_json["rows"] == []


@pytest.mark.parametrize(
    ("source", "message"),
    [
        pytest.param(b"name,category,price\nBad,Things,\xff\n", "valid UTF-8", id="bad-utf8"),
        pytest.param(b"x" * (MAX_FILE_BYTES + 1), "limited to", id="file-too-large"),
        pytest.param(
            b"name,category,price,price_minor\nBad,Things,1.00,100\n",
            "exactly one price",
            id="ambiguous-price-columns",
        ),
        pytest.param(
            b"name,category,price_minor\nBad,Things,1.5\n",
            "integer minor-unit string",
            id="decimal-in-minor-column",
        ),
    ],
)
def test_csv_refuses_invalid_encoding_size_and_ambiguous_money(workspace, source, message):
    db, owner, site = workspace
    if len(source) > MAX_FILE_BYTES or b"\xff" in source or b"price,price_minor" in source:
        with pytest.raises(CommerceError, match=message):
            connectors.create_dry_run(db, site, owner.id, "csv", transport=source)
    else:
        plan = connectors.create_dry_run(db, site, owner.id, "csv", transport=source)
        assert message in " ".join(plan.report_json["rows"][0]["errors"])


def _publish_product_page(db, site, owner, product):
    page = content.create_page(
        db,
        site,
        product.name,
        "/products/" + product.slug,
        "product",
        {
            "title": product.name,
            "sections": [
                {
                    "type": "product",
                    "heading": product.name,
                    "body": product.description or "Product details",
                    "image": product.image_url,
                }
            ],
        },
    )
    page.product_id = product.id
    content.save_page(db, site, page.id, owner.id, page.draft_json, page.version, "publish")


def test_merchant_center_feed_is_well_formed_exact_and_reports_invalid_products(workspace):
    db, owner, site = workspace
    site.hostname = "shop.example.test"
    plan = connectors.create_dry_run(db, site, owner.id, "csv", transport=_fixture_bytes())
    connectors.apply_reviewed_plan(db, site, owner.id, plan.id, plan.version)
    products = list(
        db.scalars(
            select(Product).where(Product.tenant_id == site.tenant_id).order_by(Product.name)
        )
    )
    invalid = next(product for product in products if product.name == "Pocket Brewer")
    invalid.description = ""
    invalid.subtitle = ""
    invalid.image_url = ""
    for product in products:
        _publish_product_page(db, site, owner, product)

    feed = connectors.export_artifact(db, site, "csv")
    assert feed.media_type == "application/rss+xml"
    assert feed.filename.endswith("-merchant-center.xml")
    root = ET.fromstring(feed.content)
    assert root.tag == "rss" and root.get("version") == "2.0"
    items = root.findall("./channel/item")
    assert len(items) == 1
    item = items[0]
    expected = {
        "id": "MUG-L",
        "title": "Trail Mug, Large",
        "description": "A quoted description, with a comma",
        "link": "https://shop.example.test/products/trail-mug-large",
        "image_link": "https://images.example.test/mug-large.jpg",
        "price": "31.01 USD",
        "condition": "new",
        "availability": "in_stock",
    }
    assert {name: item.findtext(f"{{{G}}}{name}") for name in expected} == expected

    report = connectors.export_review_artifact(db, site, "csv")
    assert report.media_type.startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(report.content.decode())))
    assert {row["status"] for row in rows} == {"ready", "error"}
    failed = next(row for row in rows if row["status"] == "error")
    assert failed["title"] == "Pocket Brewer"
    assert "description is missing" in failed["errors"]
    assert "image_link is missing or invalid" in failed["errors"]


def _signed_in():
    client = TestClient(app)
    login = client.get("/login?next=/admin/sites")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login.text).group(1)
    response = client.post(
        "/login",
        data={
            "csrf_token": token,
            "email": settings.admin_email,
            "password": settings.admin_password,
        },
    )
    assert response.status_code == 200
    return client, token


def test_csv_upload_route_uses_csrf_prg_stored_rows_apply_and_feed_download():
    client, token = _signed_in()
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(
            db,
            owner.id,
            "CSV route " + uuid4().hex[:6],
            "csv-route-" + uuid4().hex[:8],
        )
        site_id = site.id
        db.commit()
    base = f"/admin/sites/{site_id}/integrations"
    page = client.get(base)
    assert page.status_code == 200
    assert "CSV catalog &amp; Merchant Center" in page.text
    assert 'enctype="multipart/form-data"' in page.text
    denied = client.post(
        base + "/csv/dry-run",
        files={"catalog_file": ("catalog.csv", _fixture_bytes(), "text/csv")},
        follow_redirects=False,
    )
    assert denied.status_code == 303 and "session+expired" in denied.headers["location"]
    preview = client.post(
        base + "/csv/dry-run",
        data={"csrf_token": token},
        files={"catalog_file": ("catalog.csv", _fixture_bytes(), "text/csv")},
        follow_redirects=False,
    )
    assert preview.status_code == 303 and "plan=" in preview.headers["location"]
    review = client.get(preview.headers["location"])
    assert "Per-row status (2)" in review.text
    assert "Trail Mug, Large" in review.text
    plan_id = re.search(r'name="plan_id" value="([^"]+)"', review.text).group(1)
    version = re.search(r'name="plan_version" value="([^"]+)"', review.text).group(1)
    applied = client.post(
        base + "/csv/apply",
        data={
            "csrf_token": token,
            "plan_id": plan_id,
            "plan_version": version,
            "confirmed": "on",
        },
        follow_redirects=False,
    )
    assert applied.status_code == 303 and "successfully" in applied.headers["location"]
    feed = client.get(base + "/csv/export")
    report = client.get(base + "/csv/export?review_report=1")
    assert feed.status_code == 200 and ET.fromstring(feed.content).tag == "rss"
    assert feed.headers["content-type"].startswith("application/rss+xml")
    assert report.status_code == 200 and report.text.startswith("product_id,variant_id")
    with SessionLocal() as db:
        stored = db.get(IntegrationPlan, plan_id)
        assert stored.status == "applied" and stored.consumed_at is not None
        assert stored.payload_json["rows"][0]["name"] == "Trail Mug, Large"
