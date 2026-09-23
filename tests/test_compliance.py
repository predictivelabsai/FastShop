import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import compliance, content
from app.models import Base, User
from app.services import CommerceError
from app.site_placeholders import placeholder_inventory
from app.site_seed import seed_h24you


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


def test_scan_flags_banned_and_disease_terms():
    findings = compliance.scan_text("Clinically proven to cure diabetes and detox your body")
    assert "clinically proven" in findings["banned"]
    assert "cure" in findings["banned"]
    assert "detox" in findings["banned"]
    assert "diabetes" in findings["diseases"]


def test_scan_ignores_compliant_research_language():
    findings = compliance.scan_text("Researchers have studied molecular hydrogen; more research is needed.")
    assert findings["banned"] == [] and findings["diseases"] == []


def test_word_boundaries_avoid_false_positives():
    # "secure"/"procure" contain "cure" but must not match.
    assert compliance.scan_text("Your secure checkout is here.")["banned"] == []


def test_research_sections_allow_disease_names_but_not_banned_words():
    document = {
        "sections": [
            {"type": "references", "items": [{"heading": "A study on diabetes", "body": "See the source."}]},
            {"type": "text", "body": "This is clinically proven."},
        ]
    }
    findings = compliance.scan_document(document)
    assert "diabetes" not in findings["diseases"]  # allowed inside a citation section
    assert "clinically proven" in findings["banned"]


def test_assert_claim_requires_asterisk_and_compliant_wording():
    with pytest.raises(CommerceError):
        compliance.assert_claim_compliant("Supports cellular health")  # no asterisk
    with pytest.raises(CommerceError):
        compliance.assert_claim_compliant("Proven to work.*")  # banned word
    compliance.assert_claim_compliant("Supports cellular health.*")  # ok


def test_save_page_blocks_noncompliant_publish_but_allows_draft(db):
    owner = User(email="owner@example.test", name="Owner")
    db.add(owner)
    db.flush()
    site = content.create_site(db, owner.id, "Compliance test", "compliance-test")
    page = content.create_page(db, site, "Promo", "/pages/promo", "content",
                               {"title": "Promo", "sections": [{"type": "text", "body": "A miracle detox."}]})
    db.flush()
    # Draft save is allowed; publish is blocked.
    content.save_page(db, site, page.id, owner.id, page.draft_json, page.version, action="draft")
    with pytest.raises(CommerceError):
        content.save_page(db, site, page.id, owner.id, page.draft_json, page.version, action="publish")


def test_seed_content_is_publishable(db):
    site = seed_h24you(db, "admin@fastshop.example")
    db.commit()
    for page in content.site_pages(db, site):
        findings = compliance.scan_document(page.draft_json)
        assert findings["banned"] == [], f"{page.path}: {findings['banned']}"
        assert findings["diseases"] == [], f"{page.path}: {findings['diseases']}"


def test_placeholder_inventory_surfaces_seed_placeholders(db):
    site = seed_h24you(db, "admin@fastshop.example")
    db.commit()
    items = placeholder_inventory(db, site)
    areas = {item["area"] for item in items}
    # Seed ships a placeholder address, unapproved claims, empty socials and an unpriced bottle.
    assert "Legal" in areas
    assert "Compliance" in areas
    assert "Social" in areas
    assert any(item["area"] == "Pricing" for item in items)
