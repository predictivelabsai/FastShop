"""Settings -> Domains: connect a domain, copy its records, check DNS, choose the primary."""

from urllib.parse import urlencode

from fasthtml.common import (
    H2,
    H3,
    Button,
    Code,
    Div,
    Form,
    Input,
    Label,
    Li,
    Link,
    Ol,
    P,
    Span,
    Strong,
    Ul,
)
from starlette.responses import PlainTextResponse, RedirectResponse

from app import content, coolify_domains, site_integrations
from app import site_domains as domains
from app.admin_ui import badge
from app.config import settings
from app.db import SessionLocal
from app.integrations import zone_dns
from app.services import CommerceError

COPY_SCRIPT = (
    "navigator.clipboard.writeText(this.dataset.value)"
    ".then(()=>{this.textContent='Copied'})"
    ".catch(()=>{this.textContent='Select and copy'})"
)


def register_site_domain_routes(rt, actor, csrf, check_csrf, shell):
    def page_url(site_id, message=""):
        query = "?" + urlencode({"notice": message}) if message else ""
        return f"/admin/sites/{site_id}/settings/domains{query}"

    def back(site_id, message):
        return RedirectResponse(page_url(site_id, message), status_code=303)

    def action(session, site_id, domain_id, verb, label, *, primary=False):
        return Form(
            csrf(session),
            Button(label, cls="e-button primary" if primary else "e-button"),
            method="post",
            action=f"/admin/sites/{site_id}/settings/domains/{domain_id}/{verb}",
            cls="g-domain-action",
        )

    def records_table(domain, ownership_from_apex):
        rows = domains.records(domain, ownership_from_apex=ownership_from_apex)
        if not settings.ingress_ipv4 and not settings.domain_cname_target:
            return P("Custom domains aren't switched on for this server yet. "
                     "FastShop support can connect your domain for you.", cls="g-domain-note")
        # A list rather than a table, so the admin's responsive-table script leaves it alone.
        return Ul(
            *[
                Li(
                    Div(Span("Type", cls="g-domain-label"), Strong(record.type)),
                    Div(Span("Name", cls="g-domain-label"), Code(record.name)),
                    Div(Span("Value", cls="g-domain-label"), Code(record.value),
                        Span(record.purpose, cls="g-domain-purpose")),
                    Button("Copy value", type="button", data_value=record.value,
                           onclick=COPY_SCRIPT, cls="e-button",
                           aria_label=f"Copy the {record.type} value"),
                    cls="g-domain-record",
                )
                for record in rows
            ],
            cls="g-domain-records",
        )

    def zone_wanted(db, site, zone):
        """The records Zone should hold for this store's domains under ``zone``."""
        items = [d for d in domains.list_domains(db, site) if domains.apex_of(d.hostname) == zone]
        hostnames = {d.hostname for d in items}
        wanted = []
        for domain in items:
            if domain.status == "connected":
                continue
            from_apex = domain.hostname != zone and zone in hostnames
            for record in domains.records(domain, ownership_from_apex=from_apex):
                name = zone if record.name == "@" else f"{record.name}.{zone}"
                wanted.append((record.type, name, record.value))
        return wanted

    def zone_client(db, site):
        try:
            saved = site_integrations.payload_for(site, "zone", db)
        except CommerceError:
            return None
        return zone_dns.ZoneClient(saved["username"], saved["api_key"]) if saved else None

    def zone_card(session, db, site, items, provider):
        pending_zones = sorted({domains.apex_of(d.hostname) for d in items if d.status != "connected"})
        client = zone_client(db, site)
        row = site_integrations.credential_row(db, site, "zone")
        hints = (row.masked_hints_json or {}) if row else {}
        if not client and provider != "Zone.ee":
            return None
        base = f"/admin/sites/{site.id}/settings/zone-dns"
        if not client:
            return Div(
                H2("Connect automatically with Zone"),
                P("Your domain's DNS is at Zone. Give FastShop a Zone API key and we'll add the "
                  "records for you. Your email keeps working: mail records are never changed."),
                Ol(Li("Sign in to My Zone and click your name at the top right."),
                   Li("Open Account settings → Security → API keys."),
                   Li("Write a note such as \"FastShop\", press Generate new and copy the key. "
                      "Zone shows it only once.")),
                Form(csrf(session),
                     Label("ZoneID username", Input(name="username", autocomplete="off",
                                                    spellcheck="false", required=True)),
                     Label("API key", Input(name="api_key", type="password",
                                            autocomplete="new-password", required=True)),
                     Button("Connect Zone", cls="e-button primary"),
                     method="post", action=base + "/connect", cls="e-form"),
                P("The key is encrypted and only used to change your store's DNS records.",
                  cls="g-domain-note"),
                cls="a-card g-domain g-zone", id="zone")
        body = [P(f"Connected to Zone as {hints.get('username', 'your account')}.")]
        changes_total = 0
        for zone in pending_zones:
            try:
                changes = zone_dns.plan(client, zone, zone_wanted(db, site, zone))
            except CommerceError as exc:
                body.append(P(f"{zone}: {exc}", cls="g-domain-error", role="status"))
                continue
            changes_total += len(changes)
            if changes:
                body += [H3(f"Changes for {zone}"),
                         Ul(*[Li(change.describe()) for change in changes], cls="g-zone-changes")]
            else:
                body.append(P(f"{zone} already has the right records at Zone. DNS can take a few "
                              "minutes to update; press Check now on the domain below."))
        if changes_total:
            body += [P("Only these records change. Mail (MX), SPF and every other record stay "
                       "as they are, and you can undo this.", cls="g-domain-note"),
                     Form(csrf(session), Button("Apply these changes at Zone", cls="e-button primary"),
                          method="post", action=base + "/apply", cls="g-domain-action")]
        elif not pending_zones:
            body.append(P("All your domains are connected.", cls="g-domain-note"))
        actions = []
        if hints.get("undo"):
            actions.append(Form(csrf(session), Button("Undo the last Zone change", cls="e-button"),
                                method="post", action=base + "/undo", cls="g-domain-action"))
        actions.append(Form(csrf(session), Button("Disconnect Zone", cls="e-button"),
                            method="post", action=base + "/disconnect", cls="g-domain-action"))
        return Div(H2("Zone DNS"), *body, Div(*actions, cls="g-domain-actions"),
                   cls="a-card g-domain g-zone", id="zone")

    def domain_card(session, site, domain, provider, ownership_from_apex=False):
        primary = (site.hostname or "") == domain.hostname
        connected = domain.status == "connected"
        if primary:
            state = badge("Primary", "success")
        elif connected:
            state = badge("Connected · redirects to " + site.hostname if site.hostname
                          else "Connected", "info")
        else:
            state = badge("Waiting for DNS", "warning")
        actions = [action(session, site.id, domain.id, "check", "Check now")]
        if connected and not primary:
            actions.append(action(session, site.id, domain.id, "primary", "Make primary",
                                  primary=True))
        if not primary:
            actions.append(action(session, site.id, domain.id, "remove", "Remove"))
        body = []
        if not connected:
            where = (f"Your DNS is managed at {provider}. Sign in there, open the DNS settings "
                     f"for {domains.apex_of(domain.hostname)} and add these records."
                     if provider else
                     "Sign in where you bought the domain, open its DNS settings and add these "
                     "records.")
            body += [P(where), records_table(domain, ownership_from_apex),
                     P("Changes usually show up within minutes, but can take up to 48 hours. "
                       "Remove any other A or CNAME record for the same name.",
                       cls="g-domain-note")]
            if domain.last_error:
                body.append(P(domain.last_error, cls="g-domain-error", role="status"))
        elif primary and site.status != "published":
            body.append(P("Your store opens on this domain when you publish it.",
                          cls="g-domain-note"))
        return Div(
            Div(H3(domain.hostname), state, cls="g-domain-head"),
            *body,
            Div(*actions, cls="g-domain-actions"),
            cls="a-card g-domain",
            id="domain-" + domain.id,
        )

    @rt("/admin/sites/{site_id}/settings/domains", methods=["GET"])
    def get(session, site_id: str):
        with SessionLocal() as db:
            try:
                site = content.owned_site(db, site_id, actor(session))
                items = domains.list_domains(db, site)
                db.commit()
            except CommerceError as exc:
                return PlainTextResponse(str(exc), status_code=400)
            pending = [d for d in items if d.status != "connected"]
            hostnames = {d.hostname for d in items}
            provider = domains.dns_provider(pending[0].hostname) if pending else ""
            https_note = ("HTTPS certificates are issued automatically a few minutes after a "
                          "domain connects."
                          if settings.https_automatic else
                          "After a domain connects, FastShop support finishes its HTTPS "
                          "certificate. We'll confirm when it's live.")
            add_form = Form(
                csrf(session),
                Label("Domain", Input(name="hostname", placeholder="yourstore.com",
                                      autocomplete="off", spellcheck="false", required=True,
                                      maxlength=253)),
                Label(Input(type="checkbox", name="include_www", checked=True),
                      " Also connect www, and send it to the same store"),
                Button("Connect domain", cls="e-button primary"),
                method="post",
                action=f"/admin/sites/{site.id}/settings/domains",
                cls="e-form g-domain-add",
            )
            steps = Ul(
                Li("Enter the domain you own."),
                Li("Copy two records into your domain provider's DNS settings."),
                Li("We check them for you. Press Check now any time."),
                Li("Once connected, make it your primary domain. Your other addresses "
                   "redirect to it."),
                cls="g-domain-steps",
            )
            return shell(
                "Domains",
                Div(
                    Div(H2("Connect a domain"), steps, add_form, P(https_note, cls="g-domain-note"),
                        cls="a-card g-domain-intro"),
                    zone_card(session, db, site, items, provider),
                    *[domain_card(session, site, d, provider if d.status != "connected" else "",
                                  d.hostname != domains.apex_of(d.hostname)
                                  and domains.apex_of(d.hostname) in hostnames)
                      for d in items],
                    None if items else P("No domains connected yet. Your store is available at "
                                         f"/sites/{site.slug}/ while you set one up.",
                                         cls="g-domain-note"),
                    cls="o-operations g-settings-operations g-domains",
                ),
                Link(rel="stylesheet", href="/static/admin/growth.css"),
                Link(rel="stylesheet", href="/static/admin/domains.css"),
            )

    @rt("/admin/sites/{site_id}/settings/domains", methods=["POST"])
    async def post(request, session, site_id: str):
        form = await request.form()
        with SessionLocal() as db:
            try:
                check_csrf(session, form)
                added = domains.add_domain(db, site_id, actor(session), str(form.get("hostname", "")),
                                           include_www=form.get("include_www") == "on")
                for domain in added:
                    domains.refresh(db, domain)
                db.commit()
            except CommerceError as exc:
                db.rollback()
                return back(site_id, str(exc))
            coolify_domains.sync(db)
        if not added:
            return back(site_id, "That domain is already on this list.")
        names = " and ".join(d.hostname for d in added)
        return back(site_id, f"Added {names}. Add the records below at your DNS provider.")

    @rt("/admin/sites/{site_id}/settings/domains/{domain_id}/{verb}", methods=["POST"])
    async def post(request, session, site_id: str, domain_id: str, verb: str):
        form = await request.form()
        with SessionLocal() as db:
            try:
                check_csrf(session, form)
                user_id = actor(session)
                if verb == "check":
                    domain = domains.check_domain(db, site_id, user_id, domain_id)
                    message = (f"{domain.hostname} is connected." if domain.status == "connected"
                               else domain.last_error or "Not connected yet.")
                elif verb == "primary":
                    domain = domains.set_primary(db, site_id, user_id, domain_id)
                    message = f"{domain.hostname} is now your primary domain."
                elif verb == "remove":
                    message = domains.remove_domain(db, site_id, user_id, domain_id) + " removed."
                else:
                    return PlainTextResponse("Not found.", status_code=404)
                db.commit()
                coolify_domains.sync(db, removed={message.removesuffix(" removed.")}
                                     if verb == "remove" else None)
            except CommerceError as exc:
                db.rollback()
                return back(site_id, str(exc))
        return back(site_id, message)

    @rt("/admin/sites/{site_id}/settings/zone-dns/{verb}", methods=["POST"])
    async def post(request, session, site_id: str, verb: str):
        form = await request.form()
        with SessionLocal() as db:
            try:
                check_csrf(session, form)
                site = content.owned_site(db, site_id, actor(session), publish=True)
                if verb == "connect":
                    username = str(form.get("username", "")).strip()
                    api_key = str(form.get("api_key", "")).strip()
                    if not username or not api_key:
                        raise CommerceError("Enter your ZoneID username and API key.")
                    zone_dns.ZoneClient(username, api_key).zones()  # proves the key works
                    try:
                        site_integrations.save(db, site, "zone", {"username": username,
                                               "api_key": api_key}, "live", {"username": username})
                    except CommerceError as exc:
                        if "encryption" in str(exc).lower():
                            raise CommerceError("Secure storage for connections isn't set up on "
                                                "this server yet. Ask FastShop support.") from exc
                        raise
                    message = "Zone connected. Check the changes below, then apply them."
                elif verb == "apply":
                    client = zone_client(db, site)
                    if not client:
                        raise CommerceError("Connect Zone first.")
                    applied, count = {}, 0
                    for zone in sorted({domains.apex_of(d.hostname)
                                        for d in domains.list_domains(db, site)
                                        if d.status != "connected"}):
                        changes = zone_dns.plan(client, zone, zone_wanted(db, site, zone))
                        if changes:
                            applied[zone] = zone_dns.apply(client, zone, changes)
                            count += len(changes)
                    row = site_integrations.credential_row(db, site, "zone")
                    if applied:
                        row.masked_hints_json = {**(row.masked_hints_json or {}), "undo": applied}
                    for domain in domains.list_domains(db, site):
                        if domain.status != "connected":
                            domains.refresh(db, domain)
                    message = (f"Zone updated {count} record{'s' if count != 1 else ''}. DNS usually "
                               "updates within a few minutes; press Check now on each domain."
                               if count else "Zone already had the right records.")
                elif verb == "undo":
                    client = zone_client(db, site)
                    row = site_integrations.credential_row(db, site, "zone")
                    applied = ((row.masked_hints_json or {}).get("undo") if row else None) or {}
                    if not client or not applied:
                        raise CommerceError("There's no Zone change to undo.")
                    restored = sum(zone_dns.undo(client, zone, items) for zone, items in applied.items())
                    row.masked_hints_json = {k: v for k, v in (row.masked_hints_json or {}).items()
                                             if k != "undo"}
                    message = f"Restored {restored} record{'s' if restored != 1 else ''} at Zone."
                elif verb == "disconnect":
                    site_integrations.disconnect(db, site, "zone")
                    message = "Zone disconnected. Your DNS records were not changed."
                else:
                    return PlainTextResponse("Not found.", status_code=404)
                db.commit()
                if verb == "apply":
                    coolify_domains.sync(db)
            except CommerceError as exc:
                db.rollback()
                return back(site_id, str(exc))
        return back(site_id, message)

    @rt("/_domains/tls-allowed", methods=["GET"])
    def get(domain: str = ""):
        """Edge-proxy hook (Caddy on_demand_tls ask): 200 only for hostnames FastShop serves."""
        with SessionLocal() as db:
            allowed = domains.tls_allowed(db, domain)
        return PlainTextResponse("ok" if allowed else "unknown domain",
                                 status_code=200 if allowed else 404)
