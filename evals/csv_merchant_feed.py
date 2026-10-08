"""Offline CSV catalog migration and Merchant Center feed evaluation."""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import connectors, content
from app.models import Base, ExternalMapping, Product, ProductVariant, Stock, User

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "csv_catalog_edge_cases.csv"
OUTPUT = ROOT / "output" / "evals"
G = "http://base.google.com/ns/1.0"


def _source():
    return b"\xef\xbb\xbf" + FIXTURE.read_text(encoding="utf-8").replace("\n", "\r\n").encode()


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


def _publish(db, site, owner, product):
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


def run() -> dict:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            owner = User(email="csv-eval@example.test", name="CSV eval")
            db.add(owner)
            db.flush()
            site = content.create_site(db, owner.id, "CSV eval", "csv-eval")
            site.hostname = "csv-eval.example.test"
            before = _counts(db, site)
            first = connectors.create_dry_run(db, site, owner.id, "csv", transport=_source())
            after_preview = _counts(db, site)
            _, first_result = connectors.apply_reviewed_plan(
                db, site, owner.id, first.id, first.version
            )
            after_first = _counts(db, site)
            mug = db.scalar(
                select(Product).where(
                    Product.tenant_id == site.tenant_id,
                    Product.name == "Trail Mug, Large",
                )
            )
            mug_variant = db.scalar(
                select(ProductVariant).where(ProductVariant.product_id == mug.id)
            )
            mug_stock = db.scalar(select(Stock).where(Stock.variant_id == mug_variant.id))
            second = connectors.create_dry_run(db, site, owner.id, "csv", transport=_source())
            _, second_result = connectors.apply_reviewed_plan(
                db, site, owner.id, second.id, second.version
            )
            after_second = _counts(db, site)
            products = list(
                db.scalars(
                    select(Product)
                    .where(Product.tenant_id == site.tenant_id)
                    .order_by(Product.name)
                )
            )
            invalid = next(product for product in products if product.name == "Pocket Brewer")
            invalid.description = ""
            invalid.subtitle = ""
            invalid.image_url = ""
            for product in products:
                _publish(db, site, owner, product)
            feed = connectors.export_artifact(db, site, "csv")
            feed_root = ET.fromstring(feed.content)
            feed_items = feed_root.findall("./channel/item")
            review = connectors.export_review_artifact(db, site, "csv")
            review_rows = list(csv.DictReader(io.StringIO(review.content.decode())))
    finally:
        engine.dispose()

    required = {
        "id",
        "title",
        "description",
        "link",
        "image_link",
        "price",
        "condition",
        "availability",
    }
    first_item = feed_items[0]
    checks = {
        "preview_has_no_catalog_writes": after_preview == before,
        "bom_crlf_and_quoted_comma_parsed": (
            first.payload_json["rows"][0]["name"] == "Trail Mug, Large"
            and first.payload_json["rows"][0]["description"] == "A quoted description, with a comma"
        ),
        "decimal_rounding_is_exact": first.payload_json["rows"][0]["price_minor"] == 3101,
        "per_row_status_is_complete": len(first.report_json["rows"]) == 2
        and all(row["status"] == "create" for row in first.report_json["rows"]),
        "first_apply_created_catalog": first_result == {"products": {"created": 2, "updated": 0}},
        "stock_and_row_mappings_applied": mug_stock.quantity == 12 and after_first["mappings"] == 4,
        "reimport_plans_updates": second.report_json["counts"]["products"]["update"] == 2,
        "reimport_has_no_duplicates": after_second == after_first
        and second_result == {"products": {"created": 0, "updated": 2}},
        "merchant_feed_is_well_formed_and_complete": (
            feed_root.tag == "rss"
            and len(feed_items) == 1
            and {node.tag.removeprefix(f"{{{G}}}") for node in first_item} == required
            and first_item.findtext(f"{{{G}}}price") == "31.01 USD"
        ),
        "validation_report_names_invalid_product": any(
            row["title"] == "Pocket Brewer"
            and row["status"] == "error"
            and "description is missing" in row["errors"]
            and "image_link is missing or invalid" in row["errors"]
            for row in review_rows
        ),
    }
    return {
        "fixture": str(FIXTURE.relative_to(ROOT)).replace("\\", "/"),
        "offline": True,
        "checks": checks,
        "passed": sum(checks.values()),
        "total": len(checks),
        "first_report": first.report_json,
        "first_apply": first_result,
        "second_report": second.report_json,
        "second_apply": second_result,
        "feed_items": len(feed_items),
        "feed_review": review_rows,
    }


def main() -> int:
    result = run()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "csv_merchant_feed_results.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# CSV catalog and Merchant Center feed eval",
        "",
        f"Result: **{result['passed']}/{result['total']} checks passed** (offline).",
        "",
        "| Check | Result |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| {name.replace('_', ' ').title()} | {'Pass' if passed else 'Fail'} |"
        for name, passed in result["checks"].items()
    )
    lines.extend(
        [
            "",
            "The fixture exercises UTF-8 BOM + CRLF parsing, quoted commas, exact Decimal rounding, immutable normalized-row planning, first apply, idempotent re-import, stock, RSS 2.0 generation, and the downloadable validation report.",
            "",
        ]
    )
    (OUTPUT / "csv_merchant_feed_results.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "total": result["total"], "offline": True}))
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
