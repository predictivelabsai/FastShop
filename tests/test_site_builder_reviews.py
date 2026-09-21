from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import commerce, content, site_samples
from app import site_builder_reviews as reviews
from app import site_builder_services as builder
from app.integrations.site_builder_llm import guided, next_question
from app.models import Base, Membership, Product, User, VariantChannelListing
from app.services import CommerceError


@pytest.fixture
def merchant():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner, editor = User(email="owner@example.test", name="Owner"), User(email="editor@example.test", name="Editor")
        db.add_all([owner, editor])
        db.flush()
        site = content.create_site(db, owner.id, "Review test", "review-test")
        db.add(Membership(tenant_id=site.tenant_id, user_id=editor.id, role="editor"))
        site_samples.seed_samples(db, site, owner.id)
        db.commit()
        yield db, owner, editor, site


def propose(db, owner, site, proposals):
    page = content.site_pages(db, site)[0]
    turn, _ = builder.begin_turn(db, site.id, owner.id, uuid4().hex, "Please propose this change", page.id, site.version)
    db.commit()
    builder.finish_turn(db, site.id, owner.id, turn.id, {"answer": "Review first", "operations": [], "proposals": proposals}, "fixture")
    db.commit()
    return turn


def test_proposed_shipping_requires_approval_and_is_idempotent(merchant):
    db, owner, _, site = merchant
    config = commerce.settings_for(db, site)
    turn = propose(db, owner, site, [{"kind": "merchant", "values": {"shipping_minor": 1800}}])
    assert config.shipping_minor == 1000
    assert turn.response_json["review_status"] == "pending"
    assert reviews.decide(db, site.id, owner.id, turn.id, approve=True) == "approved"
    db.commit()
    version = site.version
    assert config.shipping_minor == 1800 and config.mode == "disabled"
    assert site.settings_json["sample_fields"]["shipping_minor"]["reviewed"]
    assert reviews.decide(db, site.id, owner.id, turn.id, approve=True) == "approved"
    assert site.version == version


def test_rejection_and_editor_cannot_approve(merchant):
    db, owner, editor, site = merchant
    turn = propose(db, owner, site, [{"kind": "merchant", "values": {"shipping_minor": 1800}}])
    with pytest.raises(CommerceError, match="access denied"):
        reviews.decide(db, site.id, editor.id, turn.id, approve=True)
    assert reviews.decide(db, site.id, owner.id, turn.id, approve=False) == "rejected"
    assert reviews.decide(db, site.id, owner.id, turn.id, approve=True) == "rejected"
    assert commerce.settings_for(db, site).shipping_minor == 1000


def test_changed_settings_invalidate_pending_proposal(merchant):
    db, owner, _, site = merchant
    turn = propose(db, owner, site, [{"kind": "merchant", "values": {"shipping_minor": 1800}}])
    config = commerce.settings_for(db, site)
    config.shipping_minor = 2200
    db.commit()
    with pytest.raises(CommerceError, match="changed"):
        reviews.decide(db, site.id, owner.id, turn.id, approve=True)
    assert config.shipping_minor == 2200


def test_product_then_price_proposals_use_owned_catalog(merchant):
    db, owner, _, site = merchant
    turn = propose(db, owner, site, [{"kind": "product", "name": "Merchant tea", "slug": "merchant-tea",
        "variants": [{"name": "Original", "price_minor": 2495}]}])
    assert not db.scalar(select(Product.id))
    reviews.decide(db, site.id, owner.id, turn.id, approve=True)
    db.commit()
    product = db.scalar(select(Product).where(Product.tenant_id == site.tenant_id))
    assert product.name == "Merchant tea"
    page = next(p for p in content.site_pages(db, site) if p.product_id == product.id)
    assert page.published_json is None
    catalog = reviews.review_context(db, site)["catalog"]
    turn = propose(db, owner, site, [{"kind": "price", "variant_id": catalog[0]["variant_id"], "price_minor": 2795}])
    assert db.scalar(select(VariantChannelListing)).price_minor == 2495
    reviews.decide(db, site.id, owner.id, turn.id, approve=True)
    assert db.scalar(select(VariantChannelListing)).price_minor == 2795


def test_atomic_bundle_rejects_duplicate_product_without_partial_shipping(merchant):
    db, owner, _, site = merchant
    product = {"kind": "product", "name": "Tea", "slug": "tea", "variants": [{"name": "Original", "price_minor": 2000}]}
    turn = propose(db, owner, site, [product, {"kind": "merchant", "values": {"shipping_minor": 1800}}, product])
    with pytest.raises(CommerceError, match="already exists"):
        reviews.decide(db, site.id, owner.id, turn.id, approve=True)
    db.commit()
    assert not db.scalar(select(Product.id))
    assert commerce.settings_for(db, site).shipping_minor == 1000


@pytest.mark.parametrize("proposal", [
    {"kind": "merchant", "values": {"mode": "live"}}, {"kind": "merchant", "values": {"shipping_minor": 1.5}},
    {"kind": "merchant", "values": {"tax_registration_reviewed": True}}, {"kind": "payment"},
    {"kind": "product", "name": "Tea", "slug": "tea", "variants": [{"name": "Original", "price_minor": True}]},
])
def test_unsafe_review_proposals_rejected(proposal):
    with pytest.raises(CommerceError):
        reviews.validate_proposals([proposal])


def test_onboarding_answers_are_saved_and_skip_known_questions(merchant):
    db, owner, _, site = merchant
    page = content.site_pages(db, site)[0]
    for prompt in ("Business: Tea shop", "Audience: Busy professionals", "Warm", "Pages: Home and About", "Tone: Friendly"):
        turn, _ = builder.begin_turn(db, site.id, owner.id, uuid4().hex, prompt, page.id, site.version)
        db.commit()
        response = guided(prompt, turn.context_json)
        builder.finish_turn(db, site.id, owner.id, turn.id, response, "guided")
        db.commit()
    assert site.settings_json["builder_brief"]["business"] == "Tea shop"
    assert "brief is saved" in next_question(site.settings_json)
