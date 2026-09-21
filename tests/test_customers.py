from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import customer_services as customers
from app.content import create_site
from app.integrations.commerce_email import dispatch_mail
from app.models import Base, CommerceMail, CustomerChallenge, CustomerOffer, MarketingConsent, User
from app.services import CommerceError


@pytest.fixture
def customer_shop():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="owner@example.test", name="Owner")
        db.add(owner)
        db.flush()
        site = create_site(db, owner.id, "Customer test", "customer-test")
        other = create_site(db, owner.id, "Other site", "other-customer-test")
        db.commit()
        yield db, sessions, site, other


def issue(db, site, purpose="login", email="shopper@example.test"):
    mail = customers.request_email_challenge(db, site, email, purpose, "127.0.0.1", consent=purpose == "newsletter")
    db.commit()
    challenge = db.get(CustomerChallenge, mail.challenge_id)
    return mail, challenge, customers.challenge_token(challenge)


def test_login_link_is_site_scoped_single_use_and_does_not_subscribe(customer_shop):
    db, _, site, other = customer_shop
    _, challenge, token = issue(db, site)
    assert token != challenge.token_hash
    with pytest.raises(CommerceError, match="invalid"):
        customers.consume_challenge(db, other, challenge.id, token)
    customer, purpose = customers.consume_challenge(db, site, challenge.id, token)
    db.commit()
    assert purpose == "login" and customer.verified_at
    assert not db.scalar(select(MarketingConsent))
    with pytest.raises(CommerceError, match="already used"):
        customers.consume_challenge(db, site, challenge.id, token)
    assert customers.signed_in_customer(db, other, {"customer:" + other.id: customer.id}) is None


def test_tampered_expired_and_missing_consent_rejected(customer_shop):
    db, _, site, _ = customer_shop
    with pytest.raises(CommerceError, match="consent"):
        customers.request_email_challenge(db, site, "shopper@example.test", "newsletter", "127.0.0.1")
    _, challenge, token = issue(db, site)
    with pytest.raises(CommerceError):
        customers.consume_challenge(db, site, challenge.id, "0" * 64)
    challenge.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    with pytest.raises(CommerceError):
        customers.consume_challenge(db, site, challenge.id, token)


def test_double_opt_in_issues_one_offer_and_unsubscribe_cancels_old_links(customer_shop):
    db, _, site, _ = customer_shop
    _, challenge, token = issue(db, site, "newsletter")
    assert not db.scalar(select(MarketingConsent))
    assert not db.scalar(select(CustomerOffer))
    customer, purpose = customers.consume_challenge(db, site, challenge.id, token)
    db.commit()
    assert purpose == "newsletter"
    consent = db.scalar(select(MarketingConsent))
    assert consent.status == "subscribed"
    assert consent.consent_json["text"] == customers.consent_text(site)
    offer = db.scalar(select(CustomerOffer))
    assert offer.percent == 10 and offer.customer_id == customer.id
    challenge.created_at = datetime.now(UTC) - timedelta(minutes=2)
    db.commit()
    _, pending, pending_token = issue(db, site, "newsletter")
    customers.unsubscribe(db, site, customer)
    db.commit()
    assert consent.status == "unsubscribed"
    with pytest.raises(CommerceError):
        customers.consume_challenge(db, site, pending.id, pending_token)
    assert db.query(CustomerOffer).count() == 1


def test_mail_queue_retries_without_persisting_bearer_tokens(customer_shop):
    db, sessions, site, _ = customer_shop
    mail, challenge, token = issue(db, site)
    assert token not in str(mail.__dict__)
    captured = []
    def failing(recipient, subject, body, **kwargs):
        captured.append(body)
        return "failed", ""
    assert not dispatch_mail(mail.id, sessions=sessions, sender=failing)
    db.expire_all()
    mail = db.get(CommerceMail, mail.id)
    assert mail.status == "failed" and mail.attempts == 1
    assert "#token=" + token in captured[0]
    assert not dispatch_mail(mail.id, sessions=sessions, sender=failing)
    mail.available_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert dispatch_mail(mail.id, sessions=sessions, sender=lambda *args, **kwargs: ("sent", "test-message"))
    db.expire_all()
    assert db.get(CommerceMail, mail.id).attempts == 2
    assert not dispatch_mail(mail.id, sessions=sessions, sender=failing)


def test_duplicate_requests_are_rate_limited_without_duplicate_customers(customer_shop):
    db, _, site, _ = customer_shop
    issue(db, site)
    assert customers.request_email_challenge(db, site, "shopper@example.test", "login", "127.0.0.1") is None
    assert db.query(CustomerChallenge).count() == 1


@pytest.mark.parametrize("email", ["one@example.com,two@example.com", "invalid", "a\n@example.com"])
def test_email_recipient_cannot_inject_multiple_recipients(email):
    with pytest.raises(CommerceError):
        customers.normalized_email(email)
