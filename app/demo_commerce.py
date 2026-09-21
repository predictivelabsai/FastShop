"""Credential-free simulations in dedicated tables, never real commerce records.

All entry points require merchant membership. Customers, stock, tax and payment
outcomes below are explicitly synthetic. There are no provider/network imports.
"""

import copy
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app import commerce, content
from app.models import DemoCommand, DemoWorkspace, new_id
from app.services import CommerceError
from app.subscriptions import advance_months


def initial_state():
    return {"catalog": {
        "sample-tea-original": {"name": "Sample tea · Original", "product": "tea", "unit_minor": 2995, "stock": 30, "subscription": True},
        "sample-tea-mint": {"name": "Sample tea · Mint", "product": "tea", "unit_minor": 2995, "stock": 30, "subscription": True},
        "sample-cup": {"name": "Sample ceramic cup", "product": "cup", "unit_minor": 1800, "stock": 20, "subscription": False}},
        "cart": [], "customer": {"email": "shopper@example.test", "verified": False, "subscribed": False, "offer": "", "redeemed": False},
        "orders": [], "subscriptions": [], "mail": [], "quote": None}


def workspace(db, site_id, user_id, *, create=False):
    site = content.owned_site(db, site_id, user_id)
    row = db.scalar(select(DemoWorkspace).where(DemoWorkspace.site_id == site.id, DemoWorkspace.tenant_id == site.tenant_id))
    if not row and create:
        # Serialize creation without changing the merchant's content version.
        db.execute(update(type(site)).where(type(site).id == site.id, type(site).tenant_id == site.tenant_id)
            .values(updated_at=datetime.now(UTC)))
        row = db.scalar(select(DemoWorkspace).where(DemoWorkspace.site_id == site.id, DemoWorkspace.tenant_id == site.tenant_id))
        if not row:
            row = DemoWorkspace(site_id=site.id, tenant_id=site.tenant_id, state_json=initial_state())
            db.add(row)
            db.flush()
    if not row:
        raise CommerceError("Open the demo workspace first.")
    return site, row


def mail(state, kind, subject, body, *, action="", reference=""):
    item = {"id": new_id(), "kind": kind, "subject": subject, "body": body,
            "action": action, "reference": reference, "consumed": False, "created_at": datetime.now(UTC).isoformat()}
    state["mail"].append(item)
    return item


def due_after(value, months, anchor):
    return advance_months(datetime.fromisoformat(value), months, anchor).isoformat()


def simulated_quote(db, site, state, address, use_offer, consent):
    destination = commerce.address(address)
    config = commerce.settings_for(db, site)
    fee = config.shipping_minor if config and config.shipping_minor is not None else 1000
    threshold = config.free_shipping_threshold_minor if config else 7500
    lines = []
    for item in sorted(state["cart"], key=lambda x: (x["sku"], x["subscription"])):
        product = state["catalog"][item["sku"]]
        if item["quantity"] > product["stock"]:
            raise CommerceError("Not enough simulated stock. Reduce the quantity.")
        if item["subscription"] and not consent:
            raise CommerceError("Confirm the simulated recurring delivery before continuing.")
        unit = commerce.discounted(product["unit_minor"], 10) if item["subscription"] else product["unit_minor"]
        lines.append(item | {"name": product["name"], "unit_minor": unit, "amount_minor": unit * item["quantity"]})
    if not lines:
        raise CommerceError("Add a sample product first.")
    base = sum(x["amount_minor"] for x in lines)
    customer = state["customer"]
    if use_offer and (not customer["offer"] or customer["redeemed"] or state["orders"]):
        raise CommerceError("Confirm the demo newsletter first; the welcome offer is for one first order.")
    discount = base - commerce.discounted(base, 10) if use_offer else 0
    subtotal = base - discount
    shipping = 0 if subtotal >= threshold else fee
    # Deliberately illustrative fixtures, NOT current statutory rates or advice.
    basis_points = {"CA": 750, "NY": 800, "TX": 625}.get(destination["state"], 500)
    tax = ((subtotal + shipping) * basis_points + 5000) // 10000
    return {"lines": lines, "subtotal_minor": base, "discount_minor": discount, "shipping_minor": shipping,
        "tax_minor": tax, "total_minor": subtotal + shipping + tax, "destination": destination,
        "tax_label": "Illustrative demo tax — not a real tax quote", "used_offer": use_offer,
        "consent": consent, "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat()}


def settle(state, quote, *, renewal=""):
    # Aggregate mixed one-time/recurring lines before touching any stock.
    quantities = {}
    for line in quote["lines"]:
        quantities[line["sku"]] = quantities.get(line["sku"], 0) + line["quantity"]
    if any(qty > state["catalog"][sku]["stock"] for sku, qty in quantities.items()):
        raise CommerceError("Not enough simulated stock. No order was created.")
    for sku in sorted(quantities):
        state["catalog"][sku]["stock"] -= quantities[sku]
    now = datetime.now(UTC)
    order = {"id": new_id(), "number": "DEMO-" + str(len(state["orders"]) + 1).zfill(4), "status": "simulated_paid",
             "quote": copy.deepcopy(quote), "events": [], "created_at": now.isoformat(), "renewal": renewal}
    state["orders"].append(order)
    mail(state, "receipt", order["number"] + " · simulated receipt", "No payment was taken. Total: USD " + format(quote["total_minor"] / 100, ".2f"), reference=order["id"])
    if quote["used_offer"]:
        state["customer"]["redeemed"] = True
    if not renewal:
        for line in quote["lines"]:
            if line["subscription"]:
                state["subscriptions"].append({"id": new_id(), "sku": line["sku"], "quantity": line["quantity"],
                    "unit_minor": line["unit_minor"], "state": "active", "months": 1, "anchor": now.day,
                    "due": advance_months(now, 1, now.day).isoformat(), "destination": quote["destination"],
                    "payment": "Simulated card", "initial_order": order["id"]})
    return order


def apply(db, site_id, user_id, command_id, version, action, payload):
    if not re.fullmatch(r"[A-Za-z0-9-]{16,64}", command_id):
        raise CommerceError("Invalid demo command.")
    fingerprint = hashlib.sha256(json.dumps({"action": action, "payload": payload}, sort_keys=True).encode()).hexdigest()
    with db.begin_nested():
        site, row = workspace(db, site_id, user_id)
        previous = db.scalar(select(DemoCommand).where(DemoCommand.workspace_id == row.id,
            DemoCommand.site_id == site.id, DemoCommand.tenant_id == site.tenant_id, DemoCommand.command_id == command_id))
        if previous:
            if previous.fingerprint != fingerprint:
                raise CommerceError("This command was already used for a different demo action.")
            return previous.result_json
        changed = db.execute(update(DemoWorkspace).where(DemoWorkspace.id == row.id,
            DemoWorkspace.tenant_id == site.tenant_id, DemoWorkspace.site_id == site.id,
            DemoWorkspace.version == version).values(version=DemoWorkspace.version).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise CommerceError("Demo changed in another window. Reload before continuing.")
        db.refresh(row)
        state = copy.deepcopy(row.state_json)
        result = execute(db, site, state, action, payload)
        if len(json.dumps(state)) > 1_500_000:
            raise CommerceError("Demo workspace is full. Use a new private site for another demonstration.")
        row.state_json = state
        row.version += 1
        db.add(DemoCommand(tenant_id=site.tenant_id, site_id=site.id, workspace_id=row.id,
            command_id=command_id, fingerprint=fingerprint, result_json=result))
        db.flush()
        return result


def execute(db, site, state, action, payload):
    if action == "catalog":
        item = state["catalog"].get(payload.get("sku"))
        name = str(payload.get("name", "")).strip()
        try:
            price, stock = int(payload.get("unit_minor", -1)), int(payload.get("stock", -1))
        except (ValueError, TypeError) as exc:
            raise CommerceError("Sample price and stock must be whole numbers.") from exc
        if not item or not name or len(name) > 180 or not 0 <= price <= 1_000_000 or not 0 <= stock <= 10000:
            raise CommerceError("Enter a sample name, price in cents and available stock.")
        item.update(name=name, unit_minor=price, stock=stock)
        state["quote"] = None
        return {"message": "Sample catalog updated. Real products and stock are unchanged."}
    if action == "cart":
        sku = payload.get("sku")
        product = state["catalog"].get(sku)
        try:
            quantity = int(payload.get("quantity", 0))
        except (ValueError, TypeError) as exc:
            raise CommerceError("Enter a whole quantity.") from exc
        recurring = payload.get("subscription") is True
        if not product or not 0 <= quantity <= 25 or (recurring and not product["subscription"]):
            raise CommerceError("Invalid sample product or quantity.")
        state["cart"] = [item for item in state["cart"] if (item["sku"], item["subscription"]) != (sku, recurring)]
        if quantity:
            state["cart"].append({"sku": sku, "quantity": quantity, "subscription": recurring})
        state["quote"] = None
        return {"message": "Demo bag updated."}
    if action in {"login", "newsletter"}:
        if action == "newsletter" and payload.get("consent") is not True:
            raise CommerceError("Explicit newsletter consent is required, even in this demo.")
        mail(state, action, "Confirm demo " + action, "Local simulation for shopper@example.test. No email is sent.", action=action)
        return {"message": "Open the local inbox to confirm."}
    if action == "confirm":
        item = next((m for m in state["mail"] if m["id"] == payload.get("message_id")), None)
        if not item or item["consumed"] or item["action"] not in {"login", "newsletter"}:
            raise CommerceError("This demo confirmation is unavailable or already used.")
        if datetime.fromisoformat(item["created_at"]) < datetime.now(UTC) - timedelta(minutes=30):
            raise CommerceError("The demo confirmation expired. Request another.")
        item["consumed"] = True
        if item["action"] == "login":
            state["customer"]["verified"] = True
        else:
            state["customer"]["subscribed"] = True
            state["customer"]["offer"] = "DEMO-WELCOME"
            mail(state, "offer", "Your demo welcome code", "DEMO-WELCOME: 10% off one first order of sample merchandise.")
        return {"message": "Demo confirmation complete."}
    if action == "logout":
        state["customer"]["verified"] = False
        return {"message": "Demo shopper signed out."}
    if action == "unsubscribe":
        state["customer"]["subscribed"] = False
        for item in state["mail"]:
            if item["action"] == "newsletter":
                item["consumed"] = True
        return {"message": "Demo marketing consent withdrawn. Transactional receipts remain available."}
    if action == "quote":
        state["quote"] = simulated_quote(db, site, state, payload.get("address", {}), payload.get("offer") is True, payload.get("consent") is True)
        return {"message": "Review the simulated total. No real tax provider was contacted."}
    if action == "checkout":
        quote = state["quote"]
        if not quote or datetime.fromisoformat(quote["expires_at"]) <= datetime.now(UTC):
            raise CommerceError("Review a fresh demo quote before checkout.")
        if payload.get("outcome") == "declined":
            return {"message": "Simulated card declined. No stock or order changed."}
        if payload.get("outcome") != "approved":
            raise CommerceError("Choose a simulated payment outcome.")
        order = settle(state, quote)
        state["cart"], state["quote"] = [], None
        return {"message": order["number"] + " created — simulated, no payment.", "order_id": order["id"]}
    if action == "tracking":
        order = next((o for o in state["orders"] if o["id"] == payload.get("order_id")), None)
        status = payload.get("status")
        if not order or status not in {"shipped", "delivered"}:
            raise CommerceError("Choose a demo order and shipment status.")
        previous = order["events"][-1]["status"] if order["events"] else "unfulfilled"
        if previous == "delivered" or (status == "delivered" and previous != "shipped") or previous == status:
            raise CommerceError("Shipment updates must progress from shipped to delivered.")
        order["events"].append({"status": status, "at": datetime.now(UTC).isoformat(), "tracking": "DEMO-TRACK-" + order["number"]})
        mail(state, "tracking", order["number"] + " · simulated " + status, "Demo tracking only; no carrier was contacted.", reference=order["id"])
        return {"message": "Simulated tracking updated."}
    if action.startswith("subscription_"):
        if not state["customer"]["verified"]:
            raise CommerceError("Confirm the demo account sign-in first.")
        contract = next((c for c in state["subscriptions"] if c["id"] == payload.get("contract_id")), None)
        if not contract or contract["state"] == "cancelled":
            raise CommerceError("This simulated subscription is unavailable.")
        command = action.removeprefix("subscription_")
        if command in {"pause", "resume", "cancel"}:
            contract["state"] = {"pause": "paused", "resume": "active", "cancel": "cancelled"}[command]
            if command == "resume" and datetime.fromisoformat(contract["due"]) <= datetime.now(UTC):
                contract["due"] = advance_months(datetime.now(UTC), contract["months"], contract["anchor"]).isoformat()
        elif command == "skip":
            contract["due"] = due_after(contract["due"], contract["months"], contract["anchor"])
        elif command == "frequency":
            months = str(payload.get("months", ""))
            if months not in {"1", "2", "3"}:
                raise CommerceError("Choose one, two or three months.")
            contract["months"] = int(months)
        elif command == "flavour":
            sku = payload.get("sku")
            item = state["catalog"].get(sku)
            current = state["catalog"][contract["sku"]]
            if not item or item["product"] != current["product"] or item["unit_minor"] != current["unit_minor"]:
                raise CommerceError("Choose another flavour of the same sample product and price.")
            contract["sku"] = sku
        elif command == "address":
            contract["destination"] = commerce.address(payload.get("address", {}))
        elif command == "payment":
            contract["payment"] = "Updated simulated card — no card data stored"
        elif command == "renew":
            if contract["state"] != "active":
                raise CommerceError("Resume this subscription before simulating its next delivery.")
            if payload.get("outcome") == "declined":
                contract["state"] = "paused"
                mail(state, "renewal_failed", "Simulated renewal declined", "The sample subscription is paused. No payment was taken.")
                return {"message": "Simulated renewal declined; subscription paused."}
            if payload.get("outcome") != "approved":
                raise CommerceError("Choose a simulated renewal outcome.")
            if commerce.discounted(state["catalog"][contract["sku"]]["unit_minor"], 10) != contract["unit_minor"]:
                raise CommerceError("The sample price changed. This demo will not silently change an agreed recurring price.")
            temporary = state | {"cart": [{"sku": contract["sku"], "quantity": contract["quantity"], "subscription": True}]}
            quote = simulated_quote(db, site, temporary, contract["destination"], False, True)
            settle(state, quote, renewal=contract["id"])
            contract["due"] = due_after(contract["due"], contract["months"], contract["anchor"])
        else:
            raise CommerceError("Unsupported demo subscription action.")
        return {"message": "Simulated subscription updated; no real billing changed."}
    raise CommerceError("Unsupported demo command.")
