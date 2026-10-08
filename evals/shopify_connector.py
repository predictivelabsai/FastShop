"""Offline Shopify migration evaluation using the committed GraphQL fixture store."""

from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import connectors, content
from app.integrations.shopify import API_VERSION, _fixture_transport
from app.models import (
    Base,
    ExternalMapping,
    Order,
    Product,
    ProductVariant,
    ShopCustomer,
    User,
    VariantChannelListing,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "shopify_store.json"
OUTPUT = ROOT / "output" / "evals"


def run() -> dict:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            owner = User(email="shopify-eval@example.test", name="Shopify eval")
            db.add(owner)
            db.flush()
            site = content.create_site(db, owner.id, "Shopify fixture migration", "shopify-eval")
            prefix = f"FASTSHOP_SHOPIFY_{site.id.upper()}_"
            values = {
                "ENABLED": "true",
                "SHOP": "fixture-eval.myshopify.com",
                "ADMIN_API_ACCESS_TOKEN": "shpat_offline_eval_token",
            }
            previous = {prefix + key: os.environ.get(prefix + key) for key in values}
            try:
                for key, value in values.items():
                    os.environ[prefix + key] = value
                first = connectors.create_dry_run(
                    db, site, owner.id, "shopify", transport=_fixture_transport(FIXTURE)
                )
                before_apply = {
                    "products": db.scalar(select(func.count()).select_from(Product)),
                    "orders": db.scalar(select(func.count()).select_from(Order)),
                    "customers": db.scalar(select(func.count()).select_from(ShopCustomer)),
                }
                _, first_result = connectors.apply_reviewed_plan(
                    db, site, owner.id, first.id, first.version
                )
                after_first = {
                    "products": db.scalar(select(func.count()).select_from(Product)),
                    "orders": db.scalar(select(func.count()).select_from(Order)),
                    "customers": db.scalar(select(func.count()).select_from(ShopCustomer)),
                    "mappings": db.scalar(select(func.count()).select_from(ExternalMapping)),
                }
                rounded = db.scalar(select(VariantChannelListing).join(
                    ProductVariant, ProductVariant.id == VariantChannelListing.variant_id
                ).where(ProductVariant.sku == "EVENING-40"))
                imported_order = db.scalar(select(Order).where(Order.tenant_id == site.tenant_id))
                second = connectors.create_dry_run(
                    db, site, owner.id, "shopify", transport=_fixture_transport(FIXTURE)
                )
                _, second_result = connectors.apply_reviewed_plan(
                    db, site, owner.id, second.id, second.version
                )
                after_second = {
                    "products": db.scalar(select(func.count()).select_from(Product)),
                    "orders": db.scalar(select(func.count()).select_from(Order)),
                    "customers": db.scalar(select(func.count()).select_from(ShopCustomer)),
                    "mappings": db.scalar(select(func.count()).select_from(ExternalMapping)),
                }
            finally:
                for name, value in previous.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
    finally:
        engine.dispose()

    checks = {
        "dry_run_has_no_writes": before_apply == {
            "products": 0, "orders": 0, "customers": 0,
        },
        "dry_run_reports_all_types": set(first.report_json["counts"]) == {
            "collections", "products", "customers", "orders",
        },
        "unmapped_items_warned": {
            item["type"] for item in first.report_json["unmapped_items"]
        } >= {"customer", "product", "order_line"},
        "shop_currency_decision_reported": any(
            "shop currency" in warning for warning in first.report_json["warnings"]
        ),
        "first_apply_created_resources": (
            first_result["collections"]["created"] == 2
            and first_result["products"]["created"] == 2
            and first_result["orders"]["created"] == 1
            and after_first["customers"] == 1
        ),
        "shop_money_and_half_up_are_exact": (
            rounded.price_minor == 3101
            and imported_order.currency == "USD"
            and imported_order.total_minor == 7369
        ),
        "reimport_plans_updates": all(
            second.report_json["counts"][name]["create"] == 0
            for name in ("collections", "products", "customers", "orders")
        ),
        "reimport_has_no_duplicates": after_second == after_first,
        "reimport_applied_updates": (
            second_result["products"]["updated"] == 2
            and second_result["orders"]["updated"] == 1
        ),
        "api_is_pinned": API_VERSION == "2026-10",
    }
    return {
        "fixture": str(FIXTURE.relative_to(ROOT)).replace("\\", "/"),
        "offline": True,
        "api_version": API_VERSION,
        "checks": checks,
        "passed": sum(checks.values()),
        "total": len(checks),
        "first_report": first.report_json,
        "first_apply": first_result,
        "second_report": second.report_json,
        "second_apply": second_result,
        "after_first": after_first,
        "after_second": after_second,
    }


def main() -> int:
    result = run()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "shopify_connector_results.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Shopify connector eval",
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
    lines.extend([
        "",
        "The fixture exercises GraphQL dry-run → apply → re-import, collection and catalog "
        "mapping, invalid customer and historic-line warnings, shop-currency selection, exact "
        "HALF_UP conversion, and duplicate-free mapped updates.",
        "",
    ])
    (OUTPUT / "shopify_connector_results.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print(json.dumps({
        "passed": result["passed"], "total": result["total"], "offline": True,
    }))
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
