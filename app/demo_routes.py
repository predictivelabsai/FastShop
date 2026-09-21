"""Merchant-only UI for the isolated simulated customer journey."""

from uuid import uuid4

from fasthtml.common import (
    H2,
    H3,
    A,
    Button,
    Details,
    Div,
    Form,
    Input,
    Label,
    Link,
    Option,
    P,
    Select,
    Small,
    Summary,
)
from sqlalchemy import select
from starlette.responses import RedirectResponse

from app import commerce, content
from app import demo_commerce as demo
from app.db import SessionLocal
from app.models import DemoWorkspace
from app.services import CommerceError, money


def register_demo_routes(rt, actor, csrf, check_csrf, shell, error):
    @rt("/admin/sites/{site_id}/demo", methods=["GET"])
    def get(session, site_id: str, view: str = "shop", notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                base = f"/admin/sites/{site.id}/demo"
                row = db.scalar(select(DemoWorkspace).where(DemoWorkspace.site_id == site.id, DemoWorkspace.tenant_id == site.tenant_id))
                banner = Div(H2("Private commerce simulator"),
                    P("SYNTHETIC DATA · No payment, real email, carrier or fulfillment calls. Tax figures are illustrative fixtures, not actual US rates. This demo is accessible only to this site's merchant team."),
                    P("Sample products and simulated orders live separately from your real catalog and orders. Merchant shipping settings are read for demonstrations; running this simulator does not change them."), cls="e-note")
                if not row:
                    return shell("Try commerce", banner, Form(csrf(session), Button("Start private demo", cls="e-button"),
                        action=base + "/start", method="post"))
                state = row.state_json
                customer = state["customer"]

                def form(action, *children, next_view=None, **fields):
                    return Form(csrf(session), Input(type="hidden", name="version", value=row.version),
                        Input(type="hidden", name="command_id", value=uuid4().hex), Input(type="hidden", name="action", value=action),
                        Input(type="hidden", name="view", value=next_view or view),
                        *[Input(type="hidden", name=key, value=value) for key, value in fields.items()],
                        *children, action=base + "/action", method="post", cls="e-form")

                def address(values=None):
                    values = values or {"line1": "123 Example Street — DEMO", "city": "New York", "state": "NY", "postal_code": "10001", "country": "US"}
                    return Div(*[Label(label, Input(name="address_" + key, value=values.get(key, ""), required=True, maxlength=200)) for key, label in (
                        ("line1", "Demo delivery street"), ("city", "City"), ("postal_code", "ZIP"))],
                        Label("US state", Select(*[Option(name, value=code, selected=values.get("state") == code) for code, name in commerce.US_STATES.items()], name="address_state", aria_label="US state")),
                        Input(type="hidden", name="address_country", value="US"))

                body = []
                if view == "shop":
                    body = [H2("Sample shop"), P("Try both one-time and recurring products. Quantities replace the matching bag line."),
                        Div(*[Div(H3(item["name"]), P(money(item["unit_minor"], "USD") + " · " + str(item["stock"]) + " simulated units"),
                            form("cart", Label("Quantity", Input(type="number", name="quantity", value=1, min=0, max=25)),
                                Label("Purchase option", Select(Option("One-time", value="off"), *([Option("Monthly · save 10%", value="on")] if item["subscription"] else []), name="subscription")),
                                Button("Set bag quantity", cls="e-button"), sku=sku),
                            Details(Summary("Edit sample product / stock"), form("catalog",
                                Label("Sample name", Input(name="name", value=item["name"], maxlength=180, required=True)),
                                Label("Sample price (USD cents)", Input(name="unit_minor", type="number", value=item["unit_minor"], min=0, max=1000000, required=True)),
                                Label("Simulated available stock", Input(name="stock", type="number", value=item["stock"], min=0, max=10000, required=True)),
                                Button("Save sample product"), sku=sku)), cls="e-card") for sku, item in state["catalog"].items()], cls="e-grid"),
                        H2("Demo bag"), *[P(state["catalog"][x["sku"]]["name"] + " × " + str(x["quantity"]) + (" · monthly" if x["subscription"] else " · one-time")) for x in state["cart"]],
                        A("Review checkout →", href=base + "?view=checkout"),
                        H2("Email capture"), P("Synthetic subscriber: " + customer["email"]),
                        form("newsletter", Label(Input(type="checkbox", name="consent", required=True), " I agree to simulated marketing emails"),
                            Button("Request demo welcome offer", cls="e-button"), next_view="inbox"),
                        form("unsubscribe", Button("Withdraw demo marketing consent"))]
                elif view == "checkout":
                    quote = state["quote"]
                    body = [H2("Demo delivery and quote"), form("quote", address(quote["destination"] if quote else None),
                        Label(Input(type="checkbox", name="offer", checked=bool(quote and quote["used_offer"])), " Use DEMO-WELCOME (confirm in local inbox first)"),
                        Label(Input(type="checkbox", name="consent", checked=bool(quote and quote["consent"])), " I authorize simulated monthly deliveries for recurring items"),
                        Button("Calculate simulated total", cls="e-button"))]
                    quote = state["quote"]
                    if quote:
                        body += [Div(H2("Review simulated order"),
                            *[P(line["name"] + " × " + str(line["quantity"]) + (" · monthly" if line["subscription"] else "")) for line in quote["lines"]],
                            *[P(label + ": " + money(quote[key], "USD")) for key, label in (
                                ("subtotal_minor", "Merchandise after subscription saving"), ("discount_minor", "First-order saving"),
                                ("shipping_minor", "Shipping"), ("tax_minor", "Illustrative demo tax"), ("total_minor", "Simulated total"))],
                            Small(quote["tax_label"] + " · " + quote["destination"]["state"] + " " + quote["destination"]["postal_code"]),
                            form("checkout", Button("Simulate approved payment", cls="e-button"), outcome="approved", next_view="account"),
                            form("checkout", Button("Simulate declined payment"), outcome="declined"), cls="e-card")]
                elif view == "inbox":
                    body = [H2("Local demo inbox"), P("Only this site's merchant team can see these synthetic messages. Nothing is delivered externally."),
                        *[Div(H3(item["subject"]), P(item["body"]), Small(item["created_at"]),
                            form("confirm", Button("Confirm demo " + item["action"], cls="e-button"), message_id=item["id"])
                            if item["action"] and not item["consumed"] else None, cls="e-card") for item in reversed(state["mail"])]]
                elif view == "fulfillment":
                    body = [H2("Simulated fulfillment"), P("These controls create local tracking events only; no labels or shipments are purchased."),
                        *[Div(H3(order["number"]), P(" → ".join(event["status"] for event in order["events"]) or "Not shipped"),
                            form("tracking", Button("Mark simulated shipped"), order_id=order["id"], status="shipped") if not order["events"] else None,
                            form("tracking", Button("Mark simulated delivered"), order_id=order["id"], status="delivered")
                            if order["events"] and order["events"][-1]["status"] == "shipped" else None, cls="e-card") for order in reversed(state["orders"])]]
                elif view == "account":
                    body = [H2("Demo account"), P(customer["email"]),
                        P("Marketing: " + ("confirmed" if customer["subscribed"] else "not subscribed")),
                        P("This is a simulated shopper identity inside your authenticated merchant workspace, not a real customer login.")]
                    if not customer["verified"]:
                        body += [form("login", Button("Send local demo sign-in", cls="e-button"), next_view="inbox")]
                    else:
                        body += [form("logout", Button("Sign demo shopper out")), H2("Order history"),
                            *[Div(H3(order["number"]), P("SIMULATED · " + money(order["quote"]["total_minor"], "USD")),
                                *[P(event["status"] + " · " + event["tracking"]) for event in order["events"]], cls="e-card") for order in reversed(state["orders"])],
                            H2("Demo subscriptions")]
                        for contract in state["subscriptions"]:
                            controls = []
                            if contract["state"] != "cancelled":
                                controls = [*[form("subscription_" + action, Button(label), contract_id=contract["id"])
                                    for action, label in (("skip", "Skip next demo delivery"), ("pause", "Pause demo subscription"),
                                        ("resume", "Resume demo subscription"), ("cancel", "Cancel demo subscription"), ("payment", "Update simulated payment method"))],
                                    form("subscription_frequency", Label("Frequency", Select(*[Option(str(m) + " month(s)", value=m, selected=contract["months"] == m) for m in (1, 2, 3)], name="months", aria_label="Frequency")),
                                        Button("Save demo frequency"), contract_id=contract["id"]),
                                    form("subscription_flavour", Label("Flavour", Select(*[Option(item["name"], value=sku, selected=contract["sku"] == sku) for sku, item in state["catalog"].items() if item["subscription"]], name="sku", aria_label="Flavour")),
                                        Button("Save demo flavour"), contract_id=contract["id"]),
                                    form("subscription_address", address(contract["destination"]), Button("Save demo delivery address"), contract_id=contract["id"]),
                                    form("subscription_renew", Button("Simulate next renewal", cls="e-button"), contract_id=contract["id"], outcome="approved"),
                                    form("subscription_renew", Button("Simulate failed renewal"), contract_id=contract["id"], outcome="declined")]
                            body.append(Div(H3(state["catalog"][contract["sku"]]["name"]),
                                P(contract["state"] + " · next simulated date " + contract["due"][:10]),
                                P(money(contract["unit_minor"] * contract["quantity"], "USD") + " recurring merchandise; shipping and illustrative tax calculated per delivery"),
                                Small(contract["payment"]), *controls, cls="e-card"))
                else:
                    raise CommerceError("Unknown demo view.")
                return shell("Commerce demo — " + site.name, Link(rel="stylesheet", href="/static/demo-commerce.css"), banner,
                    Div(A("← Builder", href=f"/admin/sites/{site.id}/build"),
                        *[A(label, href=base + "?view=" + key, aria_current="page" if view == key else None) for key, label in (
                            ("shop", "Shop & bag"), ("checkout", "Checkout"), ("account", "My account"), ("inbox", "Local inbox"), ("fulfillment", "Tracking"))], cls="e-actions"),
                    P(notice[:200], role="status") if notice else None, Div(*body, cls="e-demo"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/demo/start", methods=["POST"])
    async def post(session, request, site_id: str):
        try:
            check_csrf(session, await request.form())
            with SessionLocal() as db:
                demo.workspace(db, site_id, actor(session), create=True)
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/demo", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/demo/action", methods=["POST"])
    async def post(session, request, site_id: str):
        from urllib.parse import urlencode
        form = await request.form()
        try:
            check_csrf(session, form)
            payload = {k: str(form[k]) for k in ("sku", "quantity", "outcome", "message_id", "order_id", "status", "contract_id", "months", "name", "unit_minor", "stock") if k in form}
            payload.update({k: form.get(k) == "on" for k in ("subscription", "consent", "offer")})
            if "address_line1" in form:
                payload["address"] = {key: str(form.get("address_" + key, "")) for key in ("line1", "line2", "city", "state", "postal_code", "country")}
            with SessionLocal() as db:
                result = demo.apply(db, site_id, actor(session), str(form.get("command_id", "")), int(form.get("version", 0)), str(form.get("action", "")), payload)
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/demo?" + urlencode({"view": str(form.get("view", "shop")), "notice": result["message"]}), status_code=303)
        except (CommerceError, ValueError) as exc:
            return error(exc)
