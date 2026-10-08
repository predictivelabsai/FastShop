"""Offline Phase 4a readiness evals; writes a deterministic JSON report."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import content, site_golive
from app.models import Base, SitePage, User


def main() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cases = []
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="eval-operator@example.test", name="Eval operator")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Readiness eval", "readiness-eval")
        db.flush()

        initial = site_golive.assess(db, site)
        cases.append({
            "case": "draft-only home is rejected",
            "expected": False,
            "actual": next(check.passed for check in initial.checks if check.key == "required_pages"),
        })
        cases.append({
            "case": "catalog checks are absent until commerce is requested",
            "expected": False,
            "actual": any(check.key == "catalog" for check in initial.checks),
        })

        for path in ("/", "/shop", "/pages/about-us", "/pages/contact"):
            page = db.scalar(select(SitePage).where(
                SitePage.site_id == site.id,
                SitePage.tenant_id == site.tenant_id,
                SitePage.path == path,
            ))
            page.published_json = copy.deepcopy(page.draft_json)
        db.flush()
        ready = site_golive.assess(db, site)
        cases.append({
            "case": "published menu targets satisfy publication readiness",
            "expected": True,
            "actual": ready.passed,
        })

        home = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.path == "/"))
        document = copy.deepcopy(home.draft_json)
        document["blocks"][0]["body"] = "Guaranteed to cure diabetes."
        home.draft_json = document
        db.flush()
        compliance = next(check for check in site_golive.assess(db, site).checks if check.key == "compliance")
        cases.append({
            "case": "noncompliant draft is rejected even when public snapshot is clean",
            "expected": False,
            "actual": compliance.passed,
        })

        commerce = site_golive.assess(db, site, commerce_requested=True)
        cases.append({
            "case": "commerce request adds catalog, price and domain gates",
            "expected": ["catalog", "channel_prices", "custom_domain"],
            "actual": sorted(check.key for check in commerce.failures
                             if check.key in {"catalog", "channel_prices", "custom_domain"}),
        })

    passed = all(case["expected"] == case["actual"] for case in cases)
    report = {
        "suite": "phase4a-golive-readiness",
        "offline": True,
        "external_calls": 0,
        "passed": passed,
        "cases": cases,
    }
    output = Path("output/evals/phase4a-golive.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
