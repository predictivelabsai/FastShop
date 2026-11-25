"""Connect a Zone.ee domain in one step, and serve connected domains over HTTPS on Coolify."""

import base64
import json
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import select

from app import coolify_domains, live_credentials, site_integrations
from app import site_domains as domains
from app.integrations import zone_dns
from app.models import SiteDomain
from app.services import CommerceError
from tests.test_site_domains import INGRESS, TARGET, domains_client, ingress  # noqa: F401
from tests.test_site_golive import workspace


class FakeZone:
    """Zone's DNS API holding real records: list, create, update and delete by id."""

    def __init__(self, records, *, user="merchant", key="zone-key"):
        self.records = [dict(r) for r in records]
        self.next_id = 100
        self.auth = "Basic " + base64.b64encode(f"{user}:{key}".encode()).decode()

    def handler(self, request):
        if request.headers.get("Authorization") != self.auth:
            return httpx.Response(401)
        parts = request.url.path.removeprefix("/v2/").split("/")
        if parts == ["dns"]:
            return httpx.Response(200, json=[{"identificator": "northstar.example"}])
        zone, kind = parts[1], parts[2] if len(parts) > 2 else None
        if zone != "northstar.example":
            return httpx.Response(404)
        if request.method == "GET":
            return httpx.Response(200, json=[r for r in self.records if r["type"] == kind])
        if request.method == "POST":
            body = json.loads(request.content)
            self.next_id += 1
            row = {"id": str(self.next_id), "type": kind, **body}
            self.records.append(row)
            return httpx.Response(201, json=[row])
        row = next(r for r in self.records if r["id"] == parts[3] and r["type"] == kind)
        if request.method == "PUT":
            row.update(json.loads(request.content))
            return httpx.Response(200, json=[row])
        self.records.remove(row)
        return httpx.Response(204)

    def values(self, kind, name):
        return sorted(r["destination"] for r in self.records if r["type"] == kind and r["name"] == name)


NORTHSTAR = [  # A store moving off a WordPress host at Zone, with mail at Zone. Mail must survive.
    {"id": "1", "type": "a", "name": "northstar.example", "destination": "203.0.113.20"},
    {"id": "2", "type": "a", "name": "www.northstar.example", "destination": "203.0.113.20"},
    {"id": "3", "type": "aaaa", "name": "northstar.example", "destination": "2a02:2350::1"},
    {"id": "4", "type": "mx", "name": "northstar.example", "destination": "mx.zonemx.eu", "priority": 10},
    {"id": "5", "type": "txt", "name": "northstar.example", "destination": "v=spf1 include:zone.eu -all"},
]


def client_for(zone):
    class FakeZoneClient(zone_dns.ZoneClient):
        def __init__(self, username, api_key, *, transport=None):
            super().__init__(username, api_key, transport=httpx.MockTransport(zone.handler))
    return FakeZoneClient


def test_plan_points_the_domain_here_and_leaves_mail_alone():
    zone = FakeZone(NORTHSTAR)
    client = zone_dns.ZoneClient("merchant", "zone-key", transport=httpx.MockTransport(zone.handler))
    wanted = [("A", "northstar.example", INGRESS), ("TXT", "_fastshop.northstar.example", "fastshop-verify=abc"),
              ("CNAME", "www.northstar.example", TARGET)]
    changes = zone_dns.plan(client, "northstar.example", wanted)
    described = [c.describe() for c in changes]
    assert f"Change A northstar.example from 203.0.113.20 to {INGRESS}" in described
    assert "Remove AAAA northstar.example (2a02:2350::1)" in described
    assert "Add TXT _fastshop.northstar.example → fastshop-verify=abc" in described
    assert "Remove A www.northstar.example (203.0.113.20)" in described
    assert f"Add CNAME www.northstar.example → {TARGET}" in described
    assert not any("MX" in d or "spf" in d for d in described)

    applied = zone_dns.apply(client, "northstar.example", changes)
    assert zone.values("a", "northstar.example") == [INGRESS] and zone.values("aaaa", "northstar.example") == []
    assert zone.values("cname", "www.northstar.example") == [TARGET] and zone.values("a", "www.northstar.example") == []
    assert zone.values("mx", "northstar.example") == ["mx.zonemx.eu"]
    assert zone.values("txt", "northstar.example") == ["v=spf1 include:zone.eu -all"]
    assert zone_dns.plan(client, "northstar.example", wanted) == []  # applying twice changes nothing

    zone_dns.undo(client, "northstar.example", applied)
    for kind, name in (("a", "northstar.example"), ("a", "www.northstar.example"), ("aaaa", "northstar.example"),
                       ("cname", "www.northstar.example"), ("txt", "_fastshop.northstar.example")):
        assert zone.values(kind, name) == sorted(r["destination"] for r in NORTHSTAR
                                                 if r["type"] == kind and r["name"] == name)


def test_wrong_key_and_unknown_zone_read_plainly():
    zone = FakeZone(NORTHSTAR)
    bad = zone_dns.ZoneClient("merchant", "wrong", transport=httpx.MockTransport(zone.handler))
    with pytest.raises(CommerceError, match="didn't accept"):
        bad.zones()
    good = zone_dns.ZoneClient("merchant", "zone-key", transport=httpx.MockTransport(zone.handler))
    assert not good.has_zone("example.org")


def test_domains_page_connects_zone_applies_and_undoes(monkeypatch):
    monkeypatch.setattr(live_credentials, "settings",
                        replace(live_credentials.settings, store_key_encryption_key="k" * 40))
    zone = FakeZone(NORTHSTAR)
    monkeypatch.setattr(zone_dns, "ZoneClient", client_for(zone))
    monkeypatch.setattr(domains, "dns_provider", lambda hostname, transport=None: "Zone.ee")
    with workspace() as (db, engine, owner, _, site):
        site.hostname = "northstar.fastsme.com"
        db.commit()
        client = domains_client(engine, owner.id, monkeypatch)
        client.get(f"/admin/sites/{site.id}/settings/domains")  # issues the CSRF token
        client.post(f"/admin/sites/{site.id}/settings/domains",
                    data={"hostname": "northstar.example", "include_www": "on", "csrf_token": "expected-csrf"})
        page = client.get(f"/admin/sites/{site.id}/settings/domains").text
        assert "Connect automatically with Zone" in page and "API keys" in page

        bad = client.post(f"/admin/sites/{site.id}/settings/zone-dns/connect", follow_redirects=False,
                          data={"username": "merchant", "api_key": "wrong", "csrf_token": "expected-csrf"})
        assert "accept" in bad.headers["location"]
        ok = client.post(f"/admin/sites/{site.id}/settings/zone-dns/connect", follow_redirects=False,
                         data={"username": "merchant", "api_key": "zone-key", "csrf_token": "expected-csrf"})
        assert "Zone+connected" in ok.headers["location"]
        page = client.get(f"/admin/sites/{site.id}/settings/domains").text
        assert f"Change A northstar.example from 203.0.113.20 to {INGRESS}" in page
        assert "Mail (MX), SPF and every other record stay" in page

        applied = client.post(f"/admin/sites/{site.id}/settings/zone-dns/apply", follow_redirects=False,
                              data={"csrf_token": "expected-csrf"})
        assert "Zone+updated" in applied.headers["location"]
        assert zone.values("a", "northstar.example") == [INGRESS]
        assert zone.values("mx", "northstar.example") == ["mx.zonemx.eu"]
        token = db.scalar(select(SiteDomain.verification_token).where(SiteDomain.hostname == "northstar.example"))
        assert zone.values("txt", "_fastshop.northstar.example") == [token]

        undone = client.post(f"/admin/sites/{site.id}/settings/zone-dns/undo", follow_redirects=False,
                             data={"csrf_token": "expected-csrf"})
        assert "Restored" in undone.headers["location"]
        assert zone.values("a", "northstar.example") == ["203.0.113.20"]
        assert site_integrations.credential_row(db, site, "zone") is not None


def test_coolify_keeps_platform_domains_and_adds_only_connected_store_domains(monkeypatch):
    monkeypatch.setattr(coolify_domains, "settings", replace(
        coolify_domains.settings, coolify_url="https://coolify.example.test", coolify_token="t",
        coolify_app_uuid="app1", coolify_keep_domains=("https://northstar.fastsme.com",)))
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, request.content.decode() or None))
        if request.method == "GET" and request.url.path.endswith("/applications/app1"):
            return httpx.Response(200, json={"fqdn": "https://northstar.fastsme.com,https://old-store.test"})
        return httpx.Response(200, json={})

    with workspace() as (db, engine, owner, _, site):
        for host, status in (("northstar.example", "connected"), ("www.northstar.example", "pending"),
                             ("old-store.test", "pending")):
            db.add(SiteDomain(tenant_id=site.tenant_id, site_id=site.id, hostname=host,
                              status=status, verification_token="t"))
        db.commit()
        result = coolify_domains.sync(db, transport=httpx.MockTransport(handler))
        assert result == "updated"
        patch = next(c for c in calls if c[0] == "PATCH")
        assert json.loads(patch[2]) == {"domains": "https://northstar.fastsme.com,https://northstar.example"}
        assert any(c[1].endswith("/deploy") for c in calls)

        calls.clear()
        handler_fqdn = "https://northstar.fastsme.com,https://northstar.example"
        result = coolify_domains.sync(db, transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"fqdn": handler_fqdn})))
        assert result == "unchanged"

    monkeypatch.setattr(coolify_domains, "settings", replace(coolify_domains.settings, coolify_token=""))
    assert coolify_domains.sync(None) == "off"
