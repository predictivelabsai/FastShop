import copy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import commerce, content, site_samples
from app.models import Base, User
from app.services import CommerceError


@pytest.fixture
def merchant():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="owner@example.test", name="Owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Demo", "demo-site")
        db.commit()
        yield db, owner, site


def test_samples_are_explicit_private_editable_and_idempotent(merchant):
    db, owner, site = merchant
    published = copy.deepcopy(site.published_settings_json)
    site_samples.seed_samples(db, site, owner.id)
    config = commerce.settings_for(db, site)
    assert config.mode == "disabled" and site.status == "draft"
    assert config.shipping_minor == 1000
    assert site.settings_json["sample_fields"]["shipping_minor"]["source"] == "synthetic"
    assert site.published_settings_json == published
    values = site_samples.values_for(site, config) | {"company": "Actual merchant", "shipping_minor": "1400"}
    site_samples.save_samples(db, site, owner.id, values, {"company", "shipping_minor"}, config.version)
    assert site.settings_json["company"] == "Actual merchant" and config.shipping_minor == 1400
    assert site.settings_json["sample_fields"]["shipping_minor"]["reviewed"]
    site_samples.seed_samples(db, site, owner.id)
    assert site.settings_json["company"] == "Actual merchant" and config.shipping_minor == 1400


def test_samples_do_not_overwrite_existing_values_or_enable_commerce(merchant):
    db, owner, site = merchant
    site.settings_json = site.settings_json | {"company": "My company"}
    config = commerce.settings_for(db, site, create=True)
    config.shipping_minor = 900
    db.flush()
    site_samples.seed_samples(db, site, owner.id)
    assert config.shipping_minor == 900 and site.settings_json["company"] == "My company"
    assert not config.tax_registration_reviewed and config.mode == "disabled"
    assert not site.settings_json["sample_fields"]["company"]["reviewed"]


def test_sample_setup_denied_on_published_or_enabled_stores(merchant):
    db, owner, site = merchant
    site.status = "published"
    with pytest.raises(CommerceError):
        site_samples.seed_samples(db, site, owner.id)
    site.status = "draft"
    config = commerce.settings_for(db, site, create=True)
    config.mode = "sandbox"
    db.flush()
    with pytest.raises(CommerceError):
        site_samples.seed_samples(db, site, owner.id)


def test_review_requires_current_config_and_valid_country(merchant):
    db, owner, site = merchant
    site_samples.seed_samples(db, site, owner.id)
    config = commerce.settings_for(db, site)
    values = site_samples.values_for(site, config)
    with pytest.raises(CommerceError, match="changed"):
        site_samples.save_samples(db, site, owner.id, values, set(), config.version - 1)
    with pytest.raises(CommerceError, match="EU"):
        site_samples.save_samples(db, site, owner.id, values | {"origin_country": "US"}, set(), config.version)
    db.rollback()


def test_reviews_belong_to_exact_values():
    previous = {"company": "First company", "sample_fields": {
        "company": {"source": "merchant_entered", "reviewed": True},
        "shipping_minor": {"source": "synthetic", "reviewed": False}}}
    result = site_samples.invalidate_reviews(previous, previous, {"company": "New company"})
    assert not result["sample_fields"]["company"]["reviewed"]
    assert previous["sample_fields"]["company"]["reviewed"]
    assert site_samples.pending_reviews(result) == ["Company name", "Shipping below threshold (USD cents)"]
    assert site_samples.invalidate_reviews(previous, previous, {"company": "First company"}) == previous
