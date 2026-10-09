"""Offline golden-brief evals for full draft-site generation.

Runs both the no-key guided preset path and a mocked provider through the JSON
plan path. No network access is used.

Run:  python -m evals.site_generation
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import content
from app.compliance import scan_document
from app.models import Base, Membership, SiteMedia, Tenant, User, new_id
from app.site_blocks import normalize_document, resolve_text
from app.site_generation import (
    MerchantBrief,
    create_generated_site,
    generate_plan,
    guided_plan,
    validate_plan,
)
from app.site_images import MAX_IMAGES_PER_RESOLUTION, resolve_site_imagery
from app.site_theme import CHOICES, PRESETS


@dataclass(frozen=True)
class Case:
    name: str
    brief: MerchantBrief


CASES = [
    Case("wellness_studio", MerchantBrief("Stillwell Studio", "wellness business", "adults building gentle movement habits", "warm and natural")),
    Case("artisan_food", MerchantBrief("Juniper Pantry", "food and beverage shop", "home cooks who value small-batch ingredients", "editorial and thoughtful")),
    Case("saas", MerchantBrief("Relayboard", "SaaS", "small operations teams coordinating client work", "minimal and precise")),
    Case("local_services", MerchantBrief("Northline Repairs", "local service", "homeowners who need clear and dependable repairs", "minimal and precise")),
    Case("fashion_shop", MerchantBrief("Aster Thread", "fashion shop", "people building a versatile everyday wardrobe", "bold and energetic")),
    Case("coffee_roaster", MerchantBrief("Harbor Roast", "coffee shop", "curious home brewers and neighborhood regulars", "warm and natural")),
    Case("creative_studio", MerchantBrief("Fieldnote Design", "creative studio", "founders preparing thoughtful product launches", "editorial and thoughtful")),
    Case("consultancy", MerchantBrief("Plainspoken Finance", "consulting service", "independent businesses planning sustainable growth", "minimal and precise")),
    Case("home_goods", MerchantBrief("Soft Corner", "home goods store", "renters making compact spaces feel personal", "warm and natural")),
    Case("fitness_coach", MerchantBrief("Form & Pace", "local service", "recreational runners seeking consistent coaching", "bold and energetic")),
]


def _score(plan: dict) -> dict:
    validated = validate_plan(plan)
    required_paths = {"/", "/pages/about-us", "/pages/contact", "/blogs/learn"}
    paths = {page["path"] for page in validated["pages"]}
    block_valid = True
    compliance_ok = True
    for page in validated["pages"]:
        document = normalize_document({
            "version": 1, "title": page["title"], "description": page["description"],
            "blocks": page["blocks"],
        })
        block_valid = block_valid and bool(document["blocks"]) and all(
            block.get("id") and block.get("type") for block in document["blocks"]
        )
        findings = scan_document(document)
        compliance_ok = compliance_ok and not findings["banned"] and not findings["diseases"]
    menus_ok = all(
        item.get("path") in paths
        for items in validated["menus"].values()
        for item in items
    )
    theme_ok = validated["theme"] in PRESETS.values() and all(
        validated["theme"][key] in values for key, values in CHOICES.items()
    )
    checks = {
        "required_pages": required_paths <= paths,
        "blocks_valid": block_valid,
        "menus_resolve": menus_ok,
        "compliance_passes": compliance_ok,
        "theme_valid": theme_ok,
    }
    return {"checks": checks, "score": sum(checks.values()), "possible": len(checks)}


def run() -> dict:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    rows = []
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="site-generation-evals@example.test", name="Site generation evals")
        db.add(owner)
        db.flush()
        # Platform-tenant precedent (app/seed.py): the eval harness provisions
        # many sites for one owner, so the account holds a pro-tier membership
        # and app/plans.py free-account quotas never limit eval coverage.
        platform = Tenant(name="Generation eval platform", slug=f"eval-{new_id()}", plan="pro")
        db.add(platform)
        db.flush()
        db.add(Membership(tenant_id=platform.id, user_id=owner.id, role="admin"))
        db.flush()
        for case in CASES:
            for mode in ("guided", "mocked-llm"):
                if mode == "guided":
                    plan, source = generate_plan(case.brief, force_guided=True)
                else:
                    fixture = guided_plan(case.brief)
                    plan, source = generate_plan(
                        case.brief, provider=lambda _prompt, value=fixture: copy.deepcopy(value)
                    )
                score = _score(plan)
                site = create_generated_site(db, owner.id, case.brief, plan, source)
                applied_pages = content.site_pages(db, site)
                applied_ok = len(applied_pages) == len(plan["pages"]) and site.status == "draft"
                visual_urls = []
                for page in applied_pages:
                    document = normalize_document(page.draft_json)
                    for block in document["blocks"]:
                        if block["type"] in {"hero", "split", "product"}:
                            visual_urls.append(resolve_text(
                                block.get("image", ""), case.brief.default_locale
                            ))
                imagery_ok = len(visual_urls) == len(plan["imagery"]) and all(
                    url.startswith(f"/site-media/{site.id}/")
                    or (url.startswith("/static/") and "placeholder" in url.lower())
                    for url in visual_urls
                )
                for url in visual_urls:
                    content.validate_media_ownership(db, site, url)
                media_before = db.scalar(select(func.count()).select_from(SiteMedia).where(
                    SiteMedia.tenant_id == site.tenant_id,
                    SiteMedia.site_id == site.id,
                ))
                repeated = resolve_site_imagery(db, site, owner.id)
                media_after = db.scalar(select(func.count()).select_from(SiteMedia).where(
                    SiteMedia.tenant_id == site.tenant_id,
                    SiteMedia.site_id == site.id,
                ))
                bounded = (
                    len(plan["imagery"]) <= MAX_IMAGES_PER_RESOLUTION
                    and repeated.total <= MAX_IMAGES_PER_RESOLUTION
                )
                idempotent = repeated.provider_calls == 0 and media_after == media_before
                checks = score["checks"] | {
                    "draft_applied": applied_ok,
                    "imagery_resolved": imagery_ok,
                    "resolution_bounded": bounded,
                    "resolution_idempotent": idempotent,
                }
                rows.append({
                    "case": case.name, "mode": mode, "provider": source,
                    "brief": asdict(case.brief), "checks": checks,
                    "score": sum(checks.values()), "possible": len(checks),
                    "passed": all(checks.values()), "plan": plan,
                })
        db.rollback()
    passed = sum(row["passed"] for row in rows)
    return {
        "suite": "site_generation", "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "network": "disabled", "briefs": len(CASES), "runs": len(rows),
        "passed": passed, "failed": len(rows) - passed, "cases": rows,
    }


def write_results(summary: dict, out_dir: str = "output/evals") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "site_generation_results.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    lines = [
        "# Site generation evals",
        "",
        f"Generated: {summary['generated_at']} · network: `{summary['network']}`",
        f"**{summary['passed']}/{summary['runs']} runs passed across {summary['briefs']} golden briefs.**",
        "",
        "| Brief | Mode | Structure | Menus | Safety | Theme | Apply | Images | Bounded | Idempotent | Score |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    def marker(value):
        return "pass" if value else "FAIL"

    for row in summary["cases"]:
        check = row["checks"]
        structure = check["required_pages"] and check["blocks_valid"]
        lines.append(
            f"| {row['case']} | {row['mode']} | {marker(structure)} | "
            f"{marker(check['menus_resolve'])} | {marker(check['compliance_passes'])} | "
            f"{marker(check['theme_valid'])} | {marker(check['draft_applied'])} | "
            f"{marker(check['imagery_resolved'])} | {marker(check['resolution_bounded'])} | "
            f"{marker(check['resolution_idempotent'])} | "
            f"{row['score']}/{row['possible']} |"
        )
    lines.extend([
        "", "## Coverage", "",
        "The golden set includes wellness, food, SaaS, local services, fashion, coffee, creative services, consulting, home goods and fitness. Guided and mocked-provider runs use the same validation, apply and image-resolution boundaries; every visual block resolves to owned media or an approved static placeholder. Re-resolution is bounded and does not call the provider or duplicate media. No path accesses the network.",
    ])
    (out / "site_generation_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> None:
    summary = run()
    out = write_results(summary)
    print(json.dumps({
        "passed": summary["passed"], "runs": summary["runs"], "results": str(out)
    }, indent=2))
    if summary["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
