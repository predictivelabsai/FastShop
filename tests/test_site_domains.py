"""Self-serve custom domains: records, DNS checks, primary domain and redirects."""

from dataclasses import replace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from app import site_domains as domains
from app.models import Site, SiteDomain
from app.services import CommerceError
from app.site_context import SiteHostMiddleware
from tests.test_site_golive import workspace

INGRESS = "191.0.2.10"
TARGET = "stores.example.test"


@pytest.fixture(autouse=True)
def ingress(monkeypatch):
    monkeypatch.setattr(domains, "settings", replace(
        domains.settings, ingress_ipv4=INGRESS, domain_cname_target=TARGET,
        dns_over_https_url="https://dns.example.test/dns-query"))


def dns(answers):
    """A DNS-over-HTTPS fake: answers maps (name, type) to a list of record values."""
    codes = {"A": 1, "NS": 2, "CNAME": 5, "TXT": 16}

    def handler(request):
        name, kind = request.url.params["name"], request.url.params["type"]
        values = answers.get((name, kind), [])
        data = [f'"{value}"' if kind == "TXT" else value for value in values]
        return httpx.Response(200, json={"Answer": [
            {"name": name, "type": codes[kind], "data": value} for value in data]})

    return httpx.MockTransport(handler)


def connect(db, domain):
    """Make DNS show this domain's TXT token and point it at the platform."""
    host = domain.hostname
    answers = {(f"_fastshop.{host}", "TXT"): [domain.verification_token]}
    if domains.is_apex(host):
        answers[(host, "A")] = [INGRESS]
    else:
        answers[(host, "CNAME")] = [TARGET + "."]
    return domains.refresh(db, domain, transport=dns(answers))


def test_records_name_values_as_dns_providers_show_them():
    apex = SiteDomain(hostname="northstar.example", verification_token="fastshop-verify=abc")
    www = SiteDomain(hostname="www.northstar.example", verification_token="fastshop-verify=def")
    assert [(r.type, r.name, r.value) for r in domains.records(apex)] == [
        ("A", "@", INGRESS), ("TXT", "_fastshop", "fastshop-verify=abc")]
    assert [(r.type, r.name, r.value) for r in domains.records(www)] == [
        ("CNAME", "www", TARGET), ("TXT", "_fastshop.www", "fastshop-verify=def")]
    assert [(r.type, r.name) for r in domains.records(www, ownership_from_apex=True)] == [
        ("CNAME", "www")]
    assert domains.apex_of("shop.example.co.uk") == "example.co.uk"
    assert domains.is_apex("example.co.uk")


def test_adding_an_apex_domain_also_adds_www_and_rejects_another_stores_domain():
    with workspace() as (db, _, owner, _, site):
        added = domains.add_domain(db, site.id, owner.id, "Northstar.example.")
        assert [d.hostname for d in added] == ["northstar.example", "www.northstar.example"]
        assert all(d.status == "pending" and d.verification_token for d in added)
        assert domains.add_domain(db, site.id, owner.id, "northstar.example") == []
        other = Site(tenant_id=site.tenant_id, channel_id=site.channel_id, slug="other",
                     name="Other", hostname="taken.example.test")
        db.add(other)
        db.flush()
        with pytest.raises(CommerceError):
            domains.add_domain(db, site.id, owner.id, "taken.example.test")
        with pytest.raises(CommerceError):
            domains.add_domain(db, other.id, owner.id, "www.northstar.example")


def test_dns_check_connects_only_when_ownership_and_routing_are_both_right():
    with workspace() as (db, _, owner, _, site):
        domain = domains.add_domain(db, site.id, owner.id, "northstar.example", include_www=False)[0]
        wrong = dns({("northstar.example", "A"): ["203.0.113.20"]})
        domains.refresh(db, domain, transport=wrong)
        assert domain.status == "pending"
        assert "points to 203.0.113.20" in domain.last_error
        assert "TXT record" in domain.last_error
        connect(db, domain)
        assert domain.status == "connected" and domain.connected_at and not domain.last_error

        def broken(request):
            raise httpx.ConnectError("offline")
        domains.refresh(db, domain, transport=httpx.MockTransport(broken))
        assert domain.status == "connected"
        assert "couldn't check DNS" in domain.last_error


def test_primary_domain_requires_a_connection_and_keeps_the_old_host_as_a_redirect():
    with workspace() as (db, _, owner, _, site):
        site.hostname = "northstar.fastsme.example"
        db.flush()
        apex, www = domains.add_domain(db, site.id, owner.id, "northstar.example")
        with pytest.raises(CommerceError):
            domains.set_primary(db, site.id, owner.id, apex.id)
        connect(db, apex)
        connect(db, www)
        domains.set_primary(db, site.id, owner.id, apex.id)
        assert site.hostname == "northstar.example"
        hosts = {d.hostname: d.status for d in domains.list_domains(db, site)}
        assert hosts == {"northstar.example": "connected", "www.northstar.example": "connected",
                         "northstar.fastsme.example": "connected"}
        assert domains.redirect_target(db, "www.northstar.example") == (site.id, "northstar.example")
        assert domains.redirect_target(db, "northstar.fastsme.example") == (site.id, "northstar.example")
        assert domains.redirect_target(db, "northstar.example") is None
        with pytest.raises(CommerceError):
            domains.remove_domain(db, site.id, owner.id, apex.id)
        assert domains.remove_domain(db, site.id, owner.id, www.id) == "www.northstar.example"


def test_tls_hook_allows_only_hostnames_the_platform_serves():
    with workspace() as (db, _, owner, _, site):
        site.hostname = "shop.example.test"
        domains.add_domain(db, site.id, owner.id, "pending.example.test")
        assert domains.tls_allowed(db, "shop.example.test")
        assert domains.tls_allowed(db, "pending.example.test.")
        assert not domains.tls_allowed(db, "stranger.example.test")
        assert not domains.tls_allowed(db, "")


def test_dns_provider_is_named_from_nameservers():
    zone = dns({("northstar.example", "NS"): ["ns.zone.eu.", "ns2.zone.ee."]})
    assert domains.dns_provider("www.northstar.example", transport=zone) == "Zone.ee"
    assert domains.dns_provider("northstar.example", transport=dns({})) == ""


def test_secondary_domains_redirect_to_primary_but_webhooks_still_arrive(monkeypatch):
    with workspace() as (db, engine, owner, _, site):
        site.status = "published"
        apex, www = domains.add_domain(db, site.id, owner.id, "northstar.example")
        connect(db, apex)
        connect(db, www)
        domains.set_primary(db, site.id, owner.id, apex.id)
        db.commit()

        import app.db as database_module
        import app.site_context as context_module
        monkeypatch.setattr(context_module, "SessionLocal", lambda: Session(engine))
        monkeypatch.setattr(database_module, "SessionLocal", lambda: Session(engine))

        async def endpoint(scope, receive, send):
            await PlainTextResponse(scope["path"])(scope, receive, send)

        client = TestClient(SiteHostMiddleware(endpoint), base_url="https://www.northstar.example")
        response = client.get("/shop?colour=blue", follow_redirects=False)
        assert response.status_code == 301
        assert response.headers["location"] == "https://northstar.example/shop?colour=blue"
        hook = f"/api/v1/commerce/{site.id}/stripe-webhook"
        assert client.post(hook).text == hook
        stranger = TestClient(SiteHostMiddleware(endpoint), base_url="https://unknown.example.test")
        assert stranger.get("/shop", follow_redirects=False).status_code != 301
        assert db.scalar(select(Site.hostname).where(Site.id == site.id)) == "northstar.example"


def test_waiting_message_names_the_record_value_the_merchant_was_given():
    with workspace() as (db, _, owner, _, site):
        apex, www = domains.add_domain(db, site.id, owner.id, "northstar.example")
        elsewhere = dns({("northstar.example", "A"): ["203.0.113.20"],
                         ("www.northstar.example", "CNAME"): ["northstar.example."]})
        domains.refresh(db, apex, transport=elsewhere)
        domains.refresh(db, www, transport=elsewhere)
        assert f"not {INGRESS}" in apex.last_error
        assert f"points to northstar.example, not {TARGET}" in www.last_error


def test_www_needs_only_its_cname_once_the_apex_proves_ownership():
    with workspace() as (db, _, owner, _, site):
        apex, www = domains.add_domain(db, site.id, owner.id, "northstar.example")
        cname_only = dns({("www.northstar.example", "CNAME"): [TARGET + "."]})
        domains.refresh(db, www, transport=cname_only)
        assert www.status == "pending" and "Connect northstar.example first" in www.last_error
        both = dns({("_fastshop.northstar.example", "TXT"): [apex.verification_token],
                    ("northstar.example", "A"): [INGRESS], ("www.northstar.example", "CNAME"): [TARGET + "."]})
        domains.check_domain(db, site.id, owner.id, apex.id, transport=both)
        assert apex.status == "connected" and www.status == "connected"


def domains_client(engine, user_id, monkeypatch):
    """The Domains routes on a bare app, with DNS answered by a fake that finds nothing."""
    from fasthtml.common import Div, Input, fast_app, to_xml
    from starlette.middleware.sessions import SessionMiddleware

    import app.site_domain_routes as routes

    monkeypatch.setattr(routes, "SessionLocal", lambda: Session(engine, expire_on_commit=False))
    monkeypatch.setattr(domains, "resolve", lambda name, kind, transport=None: [])
    monkeypatch.setattr(routes, "settings", domains.settings)
    application, rt = fast_app(default_hdrs=False)
    application.add_middleware(SessionMiddleware, secret_key="domains-test-secret")

    def csrf(session):
        session["csrf_token"] = "expected-csrf"
        return Input(type="hidden", name="csrf_token", value="expected-csrf")

    def check_csrf(session, values):
        if session.get("csrf_token") != values.get("csrf_token"):
            raise CommerceError("Your session expired. Reload and try again.")

    routes.register_site_domain_routes(rt, lambda session: user_id, csrf, check_csrf,
                                       lambda title, *children: str(to_xml(Div(*children))))
    return TestClient(application)


def test_domains_page_adds_domains_and_stays_tenant_scoped(monkeypatch):
    from app import content
    from app.models import User

    with workspace() as (db, engine, owner, _, site):
        site.hostname = "current.example.test"
        outsider = User(email="domains-outsider@example.test", name="Outsider")
        db.add(outsider)
        db.flush()
        foreign = content.create_site(db, outsider.id, "Foreign", "foreign-domains")
        db.commit()
        client = domains_client(engine, owner.id, monkeypatch)

        page = client.get(f"/admin/sites/{site.id}/settings/domains")
        assert page.status_code == 200
        assert "Connect a domain" in page.text and "current.example.test" in page.text
        assert client.get(f"/admin/sites/{foreign.id}/settings/domains").status_code == 400

        refused = client.post(f"/admin/sites/{site.id}/settings/domains",
                              data={"hostname": "northstar.example", "csrf_token": "wrong"},
                              follow_redirects=False)
        assert refused.status_code == 303 and "session+expired" in refused.headers["location"]
        added = client.post(f"/admin/sites/{site.id}/settings/domains",
                            data={"hostname": "northstar.example", "include_www": "on",
                                  "csrf_token": "expected-csrf"}, follow_redirects=False)
        assert added.status_code == 303 and "Added+northstar.example" in added.headers["location"]
        db.expire_all()
        hosts = set(db.scalars(select(SiteDomain.hostname).where(SiteDomain.site_id == site.id)))
        assert hosts == {"current.example.test", "northstar.example", "www.northstar.example"}
        page = client.get(f"/admin/sites/{site.id}/settings/domains")
        assert "Waiting for DNS" in page.text and "fastshop-verify=" in page.text

        assert client.get("/_domains/tls-allowed", params={"domain": "northstar.example"}).status_code == 200
        assert client.get("/_domains/tls-allowed",
                          params={"domain": "stranger.example.test"}).status_code == 404
