import pytest
from fasthtml.common import to_xml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import content, site_ui
from app.models import Base, Product, Review, User
from app.services import CommerceError
from app.site_reviews import all_reviews, approved_reviews, moderate_review, review_summary
from app.site_seed import seed_h24you

ADMIN = "admin@fastshop.example"


@pytest.fixture
def site_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        admin = User(email=ADMIN, name="Admin")
        db.add(admin)
        db.flush()
        site = seed_h24you(db, ADMIN)
        db.commit()
        yield db, site, admin


def _add_review(db, site, approved, product=None):
    product = product or db.scalar(select(Product).where(Product.tenant_id == site.tenant_id))
    review = Review(tenant_id=site.tenant_id, product_id=product.id, author_name="Sam P.",
                    rating=5, title="Part of my morning", body="A calm daily ritual.", is_approved=approved)
    db.add(review)
    db.flush()
    return review


def test_only_approved_reviews_display(site_db):
    db, site, _ = site_db
    _add_review(db, site, approved=False)
    assert approved_reviews(db, site) == []
    _add_review(db, site, approved=True)
    shown = approved_reviews(db, site)
    assert len(shown) == 1
    count, average = review_summary(db, site)
    assert count == 1 and average == 5.0
    assert len(all_reviews(db, site)) == 2


def test_moderation_requires_ownership_and_toggles(site_db):
    db, site, admin = site_db
    review = _add_review(db, site, approved=False)
    outsider = User(email="outsider@example.test", name="Out")
    db.add(outsider)
    db.flush()
    with pytest.raises(CommerceError):
        moderate_review(db, site, outsider.id, review.id, "approve")
    moderate_review(db, site, admin.id, review.id, "approve")
    assert review.is_approved is True
    moderate_review(db, site, admin.id, review.id, "hide")
    assert review.is_approved is False
    moderate_review(db, site, admin.id, review.id, "delete")
    assert all_reviews(db, site) == []


def _render(db, site, path, preview=True):
    page = next(p for p in content.site_pages(db, site) if p.path == path)
    parts = site_ui.storefront(db, site, page, "/sites/h24you", "tok", "https://h24you.com", preview=preview)
    return "".join(to_xml(part) for part in parts if part is not None)


def test_footer_uses_payment_and_social_icons(site_db):
    db, site, _ = site_db
    html = _render(db, site, "/")
    assert "h-pay-methods" in html and "h-social-icon" in html
    assert "Visa · Mastercard · PayPal · Apple Pay · Google Pay — planned" not in html


def test_reviews_section_shows_approved_reviews_over_sample(site_db):
    db, site, _ = site_db
    assert "SAMPLE SECTION" in _render(db, site, "/")  # no reviews yet
    _add_review(db, site, approved=True)
    html = _render(db, site, "/")
    assert "Part of my morning" in html and "SAMPLE SECTION" not in html
