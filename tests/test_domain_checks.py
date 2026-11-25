"""Waiting domains are re-checked in the background; admins hear when one connects."""

from sqlalchemy.orm import Session

from app import scheduler
from app import site_domains as domains
from app.models import SiteDomain
from tests.test_site_domains import INGRESS, dns, ingress  # noqa: F401
from tests.test_site_golive import workspace


def test_background_check_connects_and_emails_admins(monkeypatch):
    with workspace() as (db, engine, owner, _, site):
        domain = SiteDomain(tenant_id=site.tenant_id, site_id=site.id, hostname="northstar.example",
                            verification_token="fastshop-verify=abc")
        db.add(domain)
        db.commit()
        answers = {("_fastshop.northstar.example", "TXT"): ["fastshop-verify=abc"], ("northstar.example", "A"): [INGRESS]}
        real_refresh = domains.refresh
        monkeypatch.setattr(domains, "refresh",
                            lambda db, d, transport=None: real_refresh(db, d, transport=dns(answers)))
        import app.db
        monkeypatch.setattr(app.db, "SessionLocal", lambda: Session(engine, expire_on_commit=False))
        sent = []
        import app.integrations.commerce_email as mail
        monkeypatch.setattr(mail, "send_email", lambda to, subject, body, message_id: sent.append(
            (to, subject, body)) or ("sent", "m1"))

        result = scheduler.run_domain_checks()
        recipients = {to for to, *_ in sent}
        assert result == {"connected": 1, "emails": len(recipients)} and owner.email in recipients
        db.expire_all()
        assert db.get(SiteDomain, domain.id).status == "connected"
        assert all(subject == "northstar.example is connected" for _, subject, _ in sent)
        assert "Make primary" in sent[0][2]

        assert scheduler.run_domain_checks() == {"connected": 0, "emails": 0}  # no repeat emails


def test_domain_checks_are_off_outside_production(monkeypatch):
    monkeypatch.delenv("FASTSHOP_ENABLE_DOMAIN_CHECKS", raising=False)
    assert scheduler.domain_worker_enabled() is False
    monkeypatch.setenv("FASTSHOP_ENABLE_DOMAIN_CHECKS", "1")
    assert scheduler.domain_worker_enabled() is True
