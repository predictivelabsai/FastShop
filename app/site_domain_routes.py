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
    P,
    Span,
    Strong,
    Ul,
)
from starlette.responses import PlainTextResponse, RedirectResponse

from app import content
from app import site_domains as domains
from app.admin_ui import badge
from app.config import settings
from app.db import SessionLocal
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
            https_note = ("HTTPS certificates are issued automatically once a domain is connected."
                          if settings.automatic_tls else
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
