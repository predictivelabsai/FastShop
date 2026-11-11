import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import compliance, content
from app.models import Base, User
from app.site_placeholders import placeholder_inventory
from scripts.demo_site import seed_demo_site


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
    findings = compliance.scan_text("Researchers have studied vitamin supplements; more research is needed.")
    assert findings["banned"] == [] and findings["diseases"] == []


def test_word_boundaries_avoid_false_positives():
    # "secure"/"procure" contain "cure" but must not match.
    assert compliance.scan_text("Your secure checkout is here.")["banned"] == []


def test_nested_localised_richtext_cannot_bypass_publication_checks():
    document = {"blocks": [{"type": "container", "children": [
        {"type": "richtext", "body": {"en": "<p>clinically <b>proven</b></p>"}},
        {"type": "container", "children": [
            {"type": "heading", "heading": "A mir&#97;cle"},
        ]},
    ]}]}
    findings = compliance.scan_document(document)
    assert "clinically proven" in findings["banned"]
    assert "miracle" in findings["banned"]
    assert "detox" in compliance.scan_document({"blocks": [
        {"type": "heading", "heading": "A <detox> promise"},
    ]})["banned"]
    assert "clinically proven" in compliance.assert_document_compliant(document)


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


def test_claim_copy_advisory_never_requires_asterisk_or_blocks_wording():
    assert compliance.assert_claim_compliant("Supports cellular health") is None
    assert "revolutionary" in compliance.assert_claim_compliant("Revolutionary results.*")
    assert compliance.assert_claim_compliant("Supports cellular health.*") is None


def test_save_page_publishes_noncompliant_copy_with_warning(db):
    owner = User(email="owner@example.test", name="Owner")
    db.add(owner)
    db.flush()
    site = content.create_site(db, owner.id, "Compliance test", "compliance-test")
    page = content.create_page(db, site, "Promo", "/pages/promo", "content",
                               {"title": "Promo", "sections": [{"type": "text", "body": "A miracle detox."}]})
    db.flush()
    # Draft save and publication both succeed; findings remain available to warn.
    content.save_page(db, site, page.id, owner.id, page.draft_json, page.version, action="draft")
    saved = content.save_page(db, site, page.id, owner.id, page.draft_json, page.version, action="publish")
    assert saved.published_json
    assert "miracle" in compliance.warning_message(compliance.scan_document(saved.published_json))


def test_seed_content_is_publishable(db):
    site = seed_demo_site(db, "admin@fastshop.example")
    db.commit()
    for page in content.site_pages(db, site):
        findings = compliance.scan_document(page.draft_json)
        assert findings["banned"] == [], f"{page.path}: {findings['banned']}"
        assert findings["diseases"] == [], f"{page.path}: {findings['diseases']}"


def test_placeholder_inventory_surfaces_seed_placeholders(db):
    site = seed_demo_site(db, "admin@fastshop.example")
    db.commit()
    items = placeholder_inventory(db, site)
    areas = {item["area"] for item in items}
    # Seed ships a placeholder address, unapproved claims, empty socials and an unpriced bottle.
    assert "Legal" in areas
    assert "Compliance" in areas
    assert "Social" in areas
    assert any(item["area"] == "Pricing" for item in items)
