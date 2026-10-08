"""Offline WooCommerce migration evaluation using the committed fixture store."""

from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import connectors, content
from app.integrations.woocommerce import _fixture_transport
from app.models import Base, ExternalMapping, Order, Product, ShopCustomer, User

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "woocommerce_store.json"
OUTPUT = ROOT / "output" / "evals"


def run() -> dict:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            owner = User(email="woo-eval@example.test", name="Woo eval")
            db.add(owner)
            db.flush()
            site = content.create_site(db, owner.id, "Woo fixture migration", "woo-eval")
            prefix = f"FASTSHOP_WOOCOMMERCE_{site.id.upper()}_"
            values = {"ENABLED": "true", "URL": "https://fixture.example.test",
                      "CONSUMER_KEY": "ck_fixture", "CONSUMER_SECRET": "cs_fixture",
                      "CURRENCY": "USD", "CURRENCY_SCALE": "2"}
            previous = {prefix + key: os.environ.get(prefix + key) for key in values}
            try:
                for key, value in values.items():
                    os.environ[prefix + key] = value
                first = connectors.create_dry_run(
                    db, site, owner.id, "woocommerce", transport=_fixture_transport(FIXTURE)
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
                second = connectors.create_dry_run(
                    db, site, owner.id, "woocommerce", transport=_fixture_transport(FIXTURE)
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
                exported = connectors.export_bundle(db, site, "woocommerce")
            finally:
                for name, value in previous.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
    finally:
        engine.dispose()

    checks = {
        "dry_run_has_no_writes": before_apply == {"products": 0, "orders": 0, "customers": 0},
        "dry_run_reports_all_types": set(first.report_json["counts"]) == {
            "categories", "products", "customers", "orders"
        },
        "unmapped_items_warned": bool(first.report_json["unmapped_items"]),
        "first_apply_created_resources": (
            first_result["products"]["created"] == 2
            and first_result["orders"]["created"] == 1
            and after_first["customers"] == 1
        ),
        "reimport_plans_updates": all(
            second.report_json["counts"][name]["create"] == 0
            for name in ("categories", "products", "customers", "orders")
        ),
        "reimport_has_no_duplicates": after_second == after_first,
        "reimport_applied_updates": (
            second_result["products"]["updated"] == 2
            and second_result["orders"]["updated"] == 1
        ),
        "export_is_review_only": exported["mode"] == "review-only",
        "export_rounds_prices_exactly": sorted(
            price
            for entry in exported["products"]
            for price in (
                [entry["payload"]["regular_price"]]
                if "regular_price" in entry["payload"]
                else [row["regular_price"] for row in entry["payload"].get("variations", [])]
            )
        ) == ["18.50", "29.95", "31.01"],
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
        "export_counts": {"categories": len(exported["categories"]),
                          "products": len(exported["products"])},
    }


def main() -> int:
    result = run()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "woocommerce_connector_results.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = ["# WooCommerce connector eval", "",
             f"Result: **{result['passed']}/{result['total']} checks passed** (offline).", "",
             "| Check | Result |", "| --- | --- |"]
    lines.extend(f"| {name.replace('_', ' ').title()} | {'Pass' if passed else 'Fail'} |"
                 for name, passed in result["checks"].items())
    lines.extend(["", "The fixture exercises dry-run → apply → re-import, unmapped warnings, "
                  "idempotent mappings, HALF_UP price conversion, and the review-only export bundle.", ""])
    (OUTPUT / "woocommerce_connector_results.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "total": result["total"], "offline": True}))
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
