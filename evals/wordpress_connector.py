"""Offline WordPress REST migration and WXR portability evaluation."""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import connectors, content
from app.integrations.wordpress import _fixture_transport
from app.models import Base, ExternalMapping, SiteMedia, SitePage, User
from app.site_blog import metadata

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "wordpress_site.json"
OUTPUT = ROOT / "output" / "evals"
NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "wp": "http://wordpress.org/export/1.2/",
}


def _counts(db, site):
    return {
        "pages": db.scalar(select(func.count()).select_from(SitePage).where(
            SitePage.tenant_id == site.tenant_id, SitePage.site_id == site.id
        )),
        "media": db.scalar(select(func.count()).select_from(SiteMedia).where(
            SiteMedia.tenant_id == site.tenant_id, SiteMedia.site_id == site.id
        )),
        "mappings": db.scalar(select(func.count()).select_from(ExternalMapping).where(
            ExternalMapping.tenant_id == site.tenant_id,
            ExternalMapping.site_id == site.id,
            ExternalMapping.system == "wordpress",
        )),
    }


def run() -> dict:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            owner = User(email="wordpress-eval@example.test", name="WordPress eval")
            db.add(owner)
            db.flush()
            site = content.create_site(
                db, owner.id, "WordPress fixture migration", "wordpress-eval"
            )
            prefix = f"FASTSHOP_WORDPRESS_{site.id.upper()}_"
            values = {
                "ENABLED": "true",
                "REST_BASE_URL": "https://fixture.wordpress.test/wp-json/wp/v2",
            }
            previous = {prefix + key: os.environ.get(prefix + key) for key in values}
            try:
                for key, value in values.items():
                    os.environ[prefix + key] = value
                before = _counts(db, site)
                first = connectors.create_dry_run(
                    db, site, owner.id, "wordpress", transport=_fixture_transport(FIXTURE)
                )
                after_preview = _counts(db, site)
                _, first_result = connectors.apply_reviewed_plan(
                    db, site, owner.id, first.id, first.version
                )
                after_first = _counts(db, site)
                article = db.scalar(select(SitePage).where(
                    SitePage.tenant_id == site.tenant_id,
                    SitePage.site_id == site.id,
                    SitePage.path == "/blogs/learn/field-guide-to-spring-water",
                ))
                inline_media = db.scalar(select(SiteMedia).where(
                    SiteMedia.tenant_id == site.tenant_id,
                    SiteMedia.site_id == site.id,
                    SiteMedia.public_url == "https://images.wordpress.example/spring-detail.jpg",
                ))
                second = connectors.create_dry_run(
                    db, site, owner.id, "wordpress", transport=_fixture_transport(FIXTURE)
                )
                _, second_result = connectors.apply_reviewed_plan(
                    db, site, owner.id, second.id, second.version
                )
                after_second = _counts(db, site)
                wxr = connectors.export_artifact(
                    db, site, "wordpress", include_drafts=True
                )
                root = ET.fromstring(wxr.content)
                items = root.findall("./channel/item")
                wxr_structure = sorted(
                    (
                        item.findtext("title"),
                        item.findtext("wp:post_type", namespaces=NS),
                        item.findtext("wp:status", namespaces=NS),
                    )
                    for item in items
                )
            finally:
                for name, value in previous.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
    finally:
        engine.dispose()

    imported_blocks = article.published_json["blocks"]
    serialized_blocks = json.dumps(imported_blocks)
    checks = {
        "dry_run_has_no_cms_writes": after_preview == before,
        "dry_run_reports_all_resources": set(first.report_json["counts"]) == {
            "categories", "tags", "authors", "media", "posts", "pages",
        },
        "trust_and_script_policy_reported": (
            any("untrusted text" in value for value in first.report_json["warnings"])
            and any("not imported" in value for value in first.report_json["warnings"])
        ),
        "first_apply_created_content": (
            first_result["posts"]["created"] == 2
            and first_result["pages"]["created"] == 1
            and after_first["pages"] == before["pages"] + 3
        ),
        "article_metadata_mapped": metadata(article.published_json) == {
            "state": "published",
            "category_slug": "field-notes",
            "tags": ["water", "reading"],
            "author_name": "Mara Field",
            "author_bio": "",
        },
        "html_sanitized_to_blocks": (
            "<script" not in serialized_blocks
            and "onclick" not in serialized_blocks
            and any(block["type"] == "split" for block in imported_blocks)
        ),
        "remote_inline_media_registered_without_blob": (
            inline_media is not None
            and inline_media.data is None
            and inline_media.size == 0
        ),
        "reimport_plans_updates": all(
            second.report_json["counts"][name]["create"] == 0
            for name in ("categories", "tags", "authors", "media", "posts", "pages")
        ),
        "reimport_has_no_duplicates": after_second == after_first,
        "wxr_is_well_formed_and_structural": (
            ("A field guide to spring water", "post", "publish") in wxr_structure
            and ("Unfinished reading notes", "post", "draft") in wxr_structure
            and ("Water library", "page", "publish") in wxr_structure
            and b"<![CDATA[" in wxr.content
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
        "before": before,
        "after_first": after_first,
        "after_second": after_second,
        "wxr_items": wxr_structure,
    }


def main() -> int:
    result = run()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "wordpress_connector_results.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# WordPress connector eval",
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
        "The fixture exercises REST dry-run → exact-plan apply → re-import, conservative HTML-to-block conversion, remote media registration, blog metadata, and well-formed WXR structural portability.",
        "",
    ])
    (OUTPUT / "wordpress_connector_results.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print(json.dumps({
        "passed": result["passed"], "total": result["total"], "offline": True,
    }))
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
