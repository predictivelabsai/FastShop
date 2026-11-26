from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import content
from app.models import Base, SiteMedia, User
from app.store_agent_audits import page_draft_report


def test_page_draft_report_is_scoped_static_and_identifies_fixable_issues():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="audit@example.test", name="Audit")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Audit store", "audit-store")
        page = content.site_pages(db, site)[0]
        media = SiteMedia(tenant_id=site.tenant_id, site_id=site.id, title="Large hero", alt="",
                          content_type="image/webp", storage_key="large.webp", size=600_001)
        db.add(media)
        db.flush()
        page.draft_json = {
            "version": 1,
            "title": "A title that is deliberately much longer than sixty characters for this draft page",
            "description": "",
            "blocks": [{"id": "hero", "type": "hero", "heading": "Draft",
                        "image": f"/site-media/{site.id}/{media.id}", "alt": ""}],
        }
        report = page_draft_report(db, site, page)

    assert report["page_id"] == page.id
    assert report["published_content_changed"] is False
    assert report["draft_preview_url"] == f"/admin/sites/{site.id}/preview/{page.id}"
    assert {issue["code"] for issue in report["issues"]} == {
        "long_title", "missing_description", "missing_alt", "large_image"
    }
