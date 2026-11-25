"""Merchant-connected custom domains: the records to add, DNS checks and the primary domain.

``Site.hostname`` remains the primary domain, so host routing, canonical URLs and checkout
return URLs keep working unchanged. Every other connected domain redirects to it.
DNS is checked over DNS-over-HTTPS with httpx, so no resolver library is needed.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select

from app import content
from app.config import settings
from app.models import Site, SiteCommerceSettings, SiteDomain
from app.services import CommerceError
from app.site_golive import _audit, _state, normalize_hostname

TXT_LABEL = "_fastshop"
DNS_TIMEOUT_SECONDS = 5.0
# Common two-label public suffixes, so "shop.co.uk" counts as an apex domain. A full
# public suffix list is unnecessary for choosing between an A and a CNAME record.
TWO_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "me.uk", "ac.uk", "com.au", "net.au", "org.au", "co.nz",
    "co.za", "com.br", "co.jp", "com.mx", "co.in", "com.sg", "com.tr", "co.il",
})
# Nameserver fragments for the providers merchants most often use.
DNS_PROVIDERS = (
    ("zone.eu", "Zone.ee"), ("zone.ee", "Zone.ee"), ("zonedata.net", "Zone.ee"),
    ("cloudflare.com", "Cloudflare"), ("domaincontrol.com", "GoDaddy"),
    ("registrar-servers.com", "Namecheap"), ("ui-dns.", "IONOS"), ("awsdns", "Amazon Route 53"),
    ("googledomains.com", "Google Domains"), ("squarespacedns.com", "Squarespace"),
    ("dns-parking.com", "Hostinger"), ("hostinger", "Hostinger"), ("wixdns.net", "Wix"),
    ("nsone.net", "NS1"), ("azure-dns", "Azure DNS"), ("louhi.net", "Louhi"),
)
RECORD_TYPES = {"A": 1, "CNAME": 5, "NS": 2, "TXT": 16}


@dataclass(frozen=True)
class DnsRecord:
    type: str
    name: str
    value: str
    purpose: str


class DnsLookupError(Exception):
    pass


def apex_of(hostname: str) -> str:
    labels = hostname.split(".")
    size = 3 if ".".join(labels[-2:]) in TWO_LABEL_SUFFIXES else 2
    return ".".join(labels[-size:])


def is_apex(hostname: str) -> bool:
    return apex_of(hostname) == hostname


def records(domain: SiteDomain, *, ownership_from_apex: bool = False) -> list[DnsRecord]:
    """The records the merchant adds at their DNS provider, named as providers show them.

    A subdomain whose apex is connected to the same store needs no TXT record of its own.
    """
    host = domain.hostname
    apex = apex_of(host)
    name = "@" if host == apex else host.removesuffix("." + apex)
    txt_name = TXT_LABEL if host == apex else f"{TXT_LABEL}.{name}"
    result = []
    if host != apex and settings.domain_cname_target:
        result.append(DnsRecord("CNAME", name, settings.domain_cname_target,
                                "Sends visitors to your store."))
    elif settings.ingress_ipv4:
        result.append(DnsRecord("A", name, settings.ingress_ipv4, "Sends visitors to your store."))
    if not ownership_from_apex:
        result.append(DnsRecord("TXT", txt_name, domain.verification_token,
                                "Proves the domain is yours."))
    return result


def apex_sibling(db, domain: SiteDomain) -> SiteDomain | None:
    """The same store's row for this subdomain's apex domain, if it has one."""
    apex = apex_of(domain.hostname)
    if apex == domain.hostname:
        return None
    return db.scalar(select(SiteDomain).where(
        func.lower(SiteDomain.hostname) == apex, SiteDomain.site_id == domain.site_id))


def resolve(name: str, record_type: str, *, transport=None) -> list[str]:
    """Return the record values for a name, via DNS-over-HTTPS (JSON API)."""
    try:
        with httpx.Client(timeout=DNS_TIMEOUT_SECONDS, transport=transport) as client:
            response = client.get(settings.dns_over_https_url,
                                  params={"name": name, "type": record_type},
                                  headers={"accept": "application/dns-json"})
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DnsLookupError(str(exc)) from exc
    wanted = RECORD_TYPES[record_type]
    values = []
    for answer in payload.get("Answer") or []:
        if answer.get("type") != wanted:
            continue
        data = str(answer.get("data", "")).strip()
        if record_type == "TXT":
            data = "".join(part for part in data.split('"') if part.strip())
        values.append(data.rstrip(".").lower() if record_type != "TXT" else data)
    return values


def dns_provider(hostname: str, *, transport=None) -> str:
    """Name the DNS provider from the apex's nameservers, or "" if unknown or unreachable."""
    try:
        nameservers = resolve(apex_of(hostname), "NS", transport=transport)
    except DnsLookupError:
        return ""
    for server in nameservers:
        for fragment, provider in DNS_PROVIDERS:
            if fragment in server:
                return provider
    return ""


def _site(db, site_id: str, user_id: str) -> Site:
    return content.owned_site(db, site_id, user_id, publish=True)


def _domain(db, site: Site, domain_id: str) -> SiteDomain:
    domain = db.scalar(select(SiteDomain).where(
        SiteDomain.id == domain_id, SiteDomain.site_id == site.id,
        SiteDomain.tenant_id == site.tenant_id))
    if not domain:
        raise CommerceError("Domain not found.")
    return domain


def _commerce_mode(db, site: Site) -> str:
    return db.scalar(select(SiteCommerceSettings.mode).where(
        SiteCommerceSettings.site_id == site.id,
        SiteCommerceSettings.tenant_id == site.tenant_id)) or "disabled"


def ensure_current(db, site: Site) -> None:
    """Show a domain bound before this feature existed as a connected domain."""
    if site.hostname and not db.scalar(select(SiteDomain.id).where(
            func.lower(SiteDomain.hostname) == site.hostname.lower())):
        now = datetime.now(UTC)
        db.add(SiteDomain(tenant_id=site.tenant_id, site_id=site.id, hostname=site.hostname,
                          status="connected", verification_token=secrets.token_hex(16),
                          ownership_verified=True, points_here=True, connected_at=now,
                          last_checked_at=now))
        db.flush()


def list_domains(db, site: Site) -> list[SiteDomain]:
    ensure_current(db, site)
    domains = list(db.scalars(select(SiteDomain).where(
        SiteDomain.site_id == site.id, SiteDomain.tenant_id == site.tenant_id)))
    primary = (site.hostname or "").lower()
    return sorted(domains, key=lambda d: (d.hostname != primary, d.status != "connected",
                                          d.hostname.startswith("www."), d.hostname))


def add_domain(db, site_id: str, user_id: str, hostname: str, *,
               include_www: bool = True) -> list[SiteDomain]:
    site = _site(db, site_id, user_id)
    host = normalize_hostname(hostname)
    if not host:
        raise CommerceError("Enter the domain you want to connect, such as yourstore.com.")
    hosts = [host]
    if include_www and is_apex(host):
        hosts.append("www." + host)
    added = []
    for name in hosts:
        owner = db.scalar(select(SiteDomain).where(func.lower(SiteDomain.hostname) == name))
        bound = db.scalar(select(Site.id).where(func.lower(Site.hostname) == name))
        if owner and owner.site_id == site.id:
            continue
        if owner or (bound and bound != site.id):
            raise CommerceError(f"{name} is already connected to another store.")
        domain = SiteDomain(tenant_id=site.tenant_id, site_id=site.id, hostname=name,
                            verification_token="fastshop-verify=" + secrets.token_hex(16))
        db.add(domain)
        added.append(domain)
    db.flush()
    if added:
        _audit(db, site, user_id, action="domain-add", decision="approved",
               reason="Merchant added " + ", ".join(d.hostname for d in added) + ".",
               before=_state(site, _commerce_mode(db, site)))
    return added


def check_domain(db, site_id: str, user_id: str, domain_id: str, *, transport=None) -> SiteDomain:
    site = _site(db, site_id, user_id)
    domain = refresh(db, _domain(db, site, domain_id), transport=transport)
    if is_apex(domain.hostname):
        # Its subdomains (such as www) rely on the apex for ownership, so recheck them too.
        for sub in db.scalars(select(SiteDomain).where(
                SiteDomain.site_id == site.id, SiteDomain.status == "pending",
                SiteDomain.hostname.like("%." + domain.hostname))):
            refresh(db, sub, transport=transport)
    return domain


def refresh(db, domain: SiteDomain, *, transport=None) -> SiteDomain:
    """Look up the domain's DNS and update its status. A lookup failure changes nothing."""
    host = domain.hostname
    try:
        txt = resolve(f"{TXT_LABEL}.{host}", "TXT", transport=transport)
        addresses = resolve(host, "A", transport=transport)
        aliases = resolve(host, "CNAME", transport=transport) if not is_apex(host) else []
    except DnsLookupError:
        domain.last_error = "We couldn't check DNS just now. Try again in a minute."
        db.flush()
        return domain
    sibling = apex_sibling(db, domain)
    domain.ownership_verified = (domain.ownership_verified or domain.verification_token in txt
                                 or bool(sibling and sibling.ownership_verified))
    domain.points_here = bool(
        (settings.ingress_ipv4 and settings.ingress_ipv4 in addresses)
        or (settings.domain_cname_target and settings.domain_cname_target in aliases)
    )
    domain.last_checked_at = datetime.now(UTC)
    problems = []
    if not domain.points_here:
        # Name the value the merchant was asked to enter for this kind of domain.
        target = (settings.domain_cname_target
                  if not is_apex(host) and settings.domain_cname_target
                  else settings.ingress_ipv4 or settings.domain_cname_target)
        current = ", ".join(aliases or addresses) or "nothing yet"
        problems.append(f"{host} points to {current}, not {target}." if target
                        else "Custom domains are not set up on this server yet.")
    if not domain.ownership_verified:
        problems.append(f"Connect {sibling.hostname} first; it proves this domain is yours."
                        if sibling else "The TXT record isn't visible yet.")
    domain.last_error = " ".join(problems)[:300]
    if domain.ownership_verified and domain.points_here:
        if domain.status != "connected":
            domain.connected_at = datetime.now(UTC)
        domain.status = "connected"
    else:
        domain.status = "pending"
    db.flush()
    return domain


def set_primary(db, site_id: str, user_id: str, domain_id: str) -> SiteDomain:
    site = _site(db, site_id, user_id)
    domain = _domain(db, site, domain_id)
    if domain.status != "connected":
        raise CommerceError("Finish connecting this domain before making it primary.")
    if site.hostname == domain.hostname:
        return domain
    before = _state(site, _commerce_mode(db, site))
    ensure_current(db, site)
    site.hostname = domain.hostname
    site.version += 1
    db.flush()
    _audit(db, site, user_id, action="domain-bind", decision="approved",
           reason=f"Merchant made {domain.hostname} the primary domain.", before=before)
    return domain


def remove_domain(db, site_id: str, user_id: str, domain_id: str) -> str:
    site = _site(db, site_id, user_id)
    domain = _domain(db, site, domain_id)
    if site.hostname == domain.hostname:
        raise CommerceError("Make another domain primary before removing this one.")
    hostname = domain.hostname
    before = _state(site, _commerce_mode(db, site))
    db.delete(domain)
    db.flush()
    _audit(db, site, user_id, action="domain-remove", decision="approved",
           reason=f"Merchant removed {hostname}.", before=before)
    return hostname


def redirect_target(db, host: str) -> tuple[str, str] | None:
    """(site ID, primary hostname) for a connected secondary domain, or None."""
    domain = db.scalar(select(SiteDomain).where(
        func.lower(SiteDomain.hostname) == host, SiteDomain.status == "connected"))
    if not domain:
        return None
    site = db.get(Site, domain.site_id)
    if not site or not site.hostname or site.hostname == host or site.status == "deleted":
        return None
    return site.id, site.hostname


def tls_allowed(db, host: str) -> bool:
    """Whether the edge proxy may issue a certificate for this hostname."""
    host = host.lower().rstrip(".")
    if not host:
        return False
    if db.scalar(select(Site.id).where(func.lower(Site.hostname) == host)):
        return True
    return bool(db.scalar(select(SiteDomain.id).where(func.lower(SiteDomain.hostname) == host)))
