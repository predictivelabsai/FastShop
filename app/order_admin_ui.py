"""Order workspaces built from the merchant shell's shared components."""
from urllib.parse import urlencode

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
    Nav,
    Option,
    P,
    Script,
    Select,
    Small,
    Span,
    Strong,
    Summary,
    Table,
    Tbody,
    Td,
    Textarea,
    Th,
    Thead,
    Tr,
)

from app.admin_ui import badge, card, empty_state, page_header, two_column
from app.models import new_id
from app.services import money


def assets():
    return (Link(rel="stylesheet", href="/static/admin/orders.css"),
            Script(src="/static/admin/orders.js", defer=True))


def base(site, link_id=""):
    return f"/admin/sites/{site.id}/orders" + (f"/{link_id}" if link_id else "")


def state(value):
    tone = "success" if value in {"paid", "fulfilled", "delivered", "closed", "received"} else (
        "critical" if value in {"cancelled", "failed", "declined"} else "warning")
    return badge(str(value).replace("_", " ").capitalize(), tone)


def field(label, name, value="", kind="text", **attrs):
    return Label(label, Input(name=name, value=value, type=kind, **attrs))


def select_field(label, name, options, value=""):
    return Label(label, Select(*[Option(title, value=key, selected=str(value) == str(key))
                                for key, title in options], name=name, aria_label=label))


def quantity_fields(order, operation="fulfil"):
    rows = []
    for line in order.lines:
        used = (line.fulfilled_quantity + line.unfulfilled_refunded_quantity) if operation == "fulfil" else (
            line.quantity - max(0, line.fulfilled_quantity - line.refunded_quantity) if operation == "return"
            else line.refunded_quantity)
        available = max(0, line.quantity - used)
        rows.append(Div(
            Div(Strong(line.product_name), Small(line.variant_name), Small(line.sku)),
            field(f"Quantity: {line.product_name}", "qty_" + line.id, 0,
                  "number", min=0, max=available, step=1, disabled=available == 0),
            Small(f"{available} available"), cls="p3-quantity-row"))
    return rows


def orders_index(site, result, filters, csrf, notice=""):
    url = base(site)
    rows = []
    for link, order, customer in result["rows"]:
        rows.append(Tr(
            Td(Input(type="checkbox", name="ids", value=link.id, data_order_select="",
                     form="order-bulk", aria_label="Select " + order.number)),
            Td(A(order.number, href=base(site, link.id), cls="p3-resource")),
            Td(order.created_at.strftime("%d %b %Y")),
            Td(customer.name or order.email), Td(money(order.total_minor, order.currency)),
            Td(state(order.payment_status)),
            Td(state("partially_fulfilled" if order.status == "unfulfilled" and any(
                line.fulfilled_quantity for line in order.lines) else order.status)),
            Td(str(sum(line.quantity for line in order.lines))),
            Td(str((order.shipping_address_json or {}).get("method", "Shipping"))),
            Td(*[badge(tag) for tag in link.tags_json or []]),
        ))
    chips = [A(f"{'Search' if key == 'q' else key.replace('_', ' ').capitalize()}: "
               f"{str(value).replace('_', ' ') if key in {'payment', 'fulfilment'} else value} ×",
               href=url + "?" + urlencode({k: v for k, v in filters.items()
                                             if k != key and k != "page" and v}), cls="p3-chip")
             for key, value in filters.items()
             if value and key in {"q", "payment", "fulfilment", "start", "end", "tag", "channel"}]
    def page_link(number, title):
        return A(title, href=url + "?" + urlencode(filters | {"page": number}), cls="p3-button")
    return Div(
        *assets(), page_header("Orders", secondary_actions=[
            A("Export", href=url + "/export?" + urlencode(filters), cls="p3-button"),
            A("Draft orders", href=f"/admin/sites/{site.id}/drafts", cls="p3-button"),
            A("Abandoned checkouts", href=f"/admin/sites/{site.id}/abandoned-checkouts", cls="p3-button")]),
        Div(Nav(*[A(title, href=url + "?" + urlencode(filters | {"view": value, "page": 1}),
                      cls="p3-tab" + (" active" if filters.get("view", "all") == value else ""))
                     for value, title in [("all", "All"), ("unfulfilled", "Unfulfilled"),
                         ("unpaid", "Unpaid"), ("open", "Open"), ("archived", "Archived")]],
                aria_label="Order views", cls="p3-tabs"),
            Form(Input(type="hidden", name="view", value=filters.get("view", "all")),
                 field("Search orders", "q", filters.get("q", ""), "search",
                       placeholder="Order, email, name or SKU"),
                 Details(Summary("Add filters"), Div(
                     select_field("Payment status", "payment", [("", "Any payment"),
                         ("paid", "Paid"), ("pending", "Pending"), ("partially_refunded", "Partially refunded"),
                         ("refunded", "Refunded"), ("failed", "Failed")], filters.get("payment", "")),
                     select_field("Fulfilment status", "fulfilment", [("", "Any fulfilment"),
                         ("unfulfilled", "Unfulfilled"), ("partially_fulfilled", "Partially fulfilled"),
                         ("fulfilled", "Fulfilled"), ("delivered", "Delivered"), ("cancelled", "Cancelled")],
                                  filters.get("fulfilment", "")),
                     field("From", "start", filters.get("start", ""), "date"),
                     field("To", "end", filters.get("end", ""), "date"),
                     field("Tag", "tag", filters.get("tag", "")),
                     field("Channel", "channel", filters.get("channel", "")), cls="p3-filter-fields")),
                 select_field("Sort", "sort", [("date_desc", "Newest first"), ("date_asc", "Oldest first"),
                     ("total_desc", "Highest total"), ("total_asc", "Lowest total"), ("number", "Order number")],
                              filters.get("sort", "date_desc")),
                 Button("Apply", type="submit"), A("Clear", href=url),
                 method="get", cls="p3-filter"), Div(*chips, cls="p3-chips"),
            Form(csrf, Input(type="hidden", name="request_key", value=new_id()),
                 Span("Selected orders", data_order_count=""),
                 select_field("Bulk action", "action", [("archive", "Archive"), ("unarchive", "Unarchive"),
                     ("tag", "Add tags"), ("fulfil", "Mark fulfilled"), ("export", "Export CSV")]),
                 field("Tags", "value", placeholder="VIP, Wholesale"),
                 Details(Summary("Tracking for bulk fulfilment"),
                         field("Carrier", "carrier"), field("Tracking number", "tracking_number"),
                         field("Tracking URL", "tracking_url", kind="url")),
                 Button("Apply to selected", type="submit"), action=url + "/bulk", method="post",
                 id="order-bulk", cls="p3-bulk"),
            Div(Table(Thead(Tr(Th(Input(type="checkbox", data_order_select_all="",
                aria_label="Select all orders")), *[Th(c, scope="col") for c in (
                    "Order", "Date", "Customer", "Total", "Payment", "Fulfilment", "Items", "Delivery", "Tags")])),
                Tbody(*rows)), cls="p3-table-wrap") if rows else empty_state(
                    "No matching orders", "Confirmed orders appear here. Try changing your search or filters."),
            Div(Small(f"{result['total']} {'order' if result['total'] == 1 else 'orders'} · "
                          f"Page {result['page']} of {max(1, result['pages'])}"),
                page_link(result["page"] - 1, "Previous") if result["page"] > 1 else None,
                page_link(result["page"] + 1, "Next") if result["page"] < result["pages"] else None,
                cls="p3-pagination"), cls="a-card p3-index"), cls="p3-orders")


def _address(address):
    return P(*[Span(str(address.get(key))) for key in (
        "name", "line1", "line2", "city", "state", "postal_code", "country") if address.get(key)],
        cls="p3-address") if address else P("No address supplied", cls="p3-muted")


def order_detail(site, data, csrf_factory, notice=""):
    order, link, customer = data["order"], data["link"], data["customer"]
    url = base(site, link.id)
    def form(*children, endpoint, **attrs):
        return Form(csrf_factory(), *children, action=url + endpoint, method="post", **attrs)
    groups = []
    for title, fulfilled in (("Unfulfilled items", False), ("Fulfilled items", True)):
        lines = []
        for line in order.lines:
            quantity = line.fulfilled_quantity if fulfilled else max(0, line.quantity - line.fulfilled_quantity - line.unfulfilled_refunded_quantity)
            if quantity:
                lines.append(Div(Div(Strong(line.product_name), Small(line.variant_name), Small(line.sku),
                        Small(f"{line.refunded_quantity} refunded · {line.restocked_quantity} restocked")
                            if line.refunded_quantity else None),
                    Span(f"{money(line.unit_price_minor, order.currency)} × {quantity}"),
                    Strong(money(line.unit_price_minor * quantity, order.currency)), cls="p3-line"))
        if lines:
            groups.append(card(H2(title), *lines))
    fulfil = Details(Summary("Fulfil items"), form(
        Input(type="hidden", name="request_key", value=new_id()), *quantity_fields(order),
        Div(field("Carrier", "carrier", required=True, maxlength=100),
            field("Tracking number", "tracking_number", required=True, maxlength=100),
            field("Tracking URL", "tracking_url", kind="url", placeholder="https://", maxlength=500),
            cls="p3-fields"),
        Label(Input(type="checkbox", name="notify", value="on", checked=True), " Notify customer by email"),
        Button("Fulfil selected items", type="submit", cls="p3-primary"),
        endpoint="/fulfil-items", cls="p3-form"), cls="a-card p3-disclosure") if (
            order.status != "cancelled" and any(line.fulfilled_quantity + line.unfulfilled_refunded_quantity < line.quantity for line in order.lines)) else None
    refund = Details(Summary("Refund items"), Form(
        *quantity_fields(order, "refund"), field("Shipping refund", "shipping", "0.00", "number",
            min=0, step="0.01", max=f"{order.shipping_minor / 100:.2f}"),
        Label(Input(type="checkbox", name="restock", value="on"), " Restock returned items"),
        field("Reason for refund", "reason", required=True, minlength=10, maxlength=500),
        Button("Review refund", cls="p3-primary"), action=url + "/refund-items", method="get",
        cls="p3-form"), cls="a-card p3-disclosure") if data["available"] else None
    return_flow = Details(Summary("Create return"), form(
        *quantity_fields(order, "return"), field("Reason for return", "reason", required=True, maxlength=500),
        Label(Input(type="checkbox", name="restock", checked=True, value="on"), " Restock on receipt"),
        Button("Create return", cls="p3-primary"), endpoint="/returns", cls="p3-form"),
        cls="a-card p3-disclosure")
    returns = []
    for row in data["returns"]:
        buttons = []
        transitions = {"requested": [("approved", "Approve return"), ("declined", "Decline return")],
                       "approved": [("received", "Mark received")], "received": [("closed", "Close return")]}
        for target, label in transitions.get(row.status, []):
            buttons.append(form(Input(type="hidden", name="target", value=target),
                Button(label), endpoint=f"/returns/{row.id}/status"))
        if row.status == "received" and not row.refund_command_id:
            buttons.append(A("Refund return", href=url + "/refund-items?" + urlencode({"return_id": row.id}),
                             cls="p3-button"))
        item_count = sum(line.quantity for line in row.lines)
        returns.append(Div(Div(state(row.status), Small(row.created_at.strftime("%d %b %Y"))),
            P(row.reason), Small(f"{item_count} {'item' if item_count == 1 else 'items'} · " +
                  (("Items restocked" if row.status in {"received", "closed"} else "Restock on receipt")
                   if row.restock else "No restock")),
            Div(*buttons, cls="p3-actions"), cls="p3-return"))
    events = []
    for event in data["timeline"]:
        command = event.get("command")
        retry = form(Input(type="hidden", name="amount_minor", value=command.amount_minor),
            Input(type="hidden", name="reason", value=command.reason),
            Input(type="hidden", name="request_key", value=command.request_key),
            Input(type="hidden", name="retry", value="on"), Button("Reconcile exact refund"),
            endpoint="/refund") if command and command.state in {
                "creating", "pending", "reconciliation_required"} else None
        events.append(Div(Div(Strong(event["title"]), Small(event["at"].strftime("%d %b %Y, %H:%M"))),
            P(event.get("detail", "")), A("View tracking", href=event["tracking_url"], target="_blank",
                rel="noopener noreferrer") if event.get("tracking_url") else None, retry, cls="p3-event"))
    payment = card(H2("Payment summary"), *[Div(Span(label), Strong(money(value, order.currency)),
        cls="p3-total" + (" p3-grand" if label == "Total" else "")) for label, value in [
            ("Subtotal", order.subtotal_minor), ("Discounts", -order.discount_minor),
            ("Shipping", order.shipping_minor), ("Tax", order.tax_minor), ("Total", order.total_minor),
            ("Paid by customer", data["captured"]), ("Refunded", data["refunded"])]],
        P("Available to refund: " + money(data["available"], order.currency), cls="p3-muted"),
        P("Manual payment recorded. Automatic refunds are unavailable for this payment.", cls="p3-muted")
            if data.get("payment_provider") == "manual" else None)
    notes = card(H2("Notes"), form(Label("Order note", Textarea(link.note or "", name="note", maxlength=5000)),
        Button("Save note"), endpoint="/note", cls="p3-form"))
    tags = card(H2("Tags"), Div(*[badge(tag) for tag in link.tags_json or []], cls="p3-chips"),
        form(field("Tags, separated by commas", "tags", ", ".join(link.tags_json or []), maxlength=1000),
             Button("Save tags"), endpoint="/tags", cls="p3-form"))
    customer_card = card(H2("Customer"), A(customer.name or customer.email,
        href=f"/admin/sites/{site.id}/customers/{customer.id}", cls="p3-resource"),
        P(order.email), P(customer.phone) if getattr(customer, "phone", "") else None,
        H3("Shipping address"), _address(order.shipping_address_json or {}),
        H3("Billing address"), _address(getattr(order, "billing_address_json", {}) or {}))
    actions = card(H2("Order actions"), form(Input(type="hidden", name="archived",
        value="0" if link.is_archived else "1"), Button("Unarchive order" if link.is_archived else "Archive order"),
        endpoint="/archive"), Details(Summary("Cancel order"),
            P("Cancelling fulfilment does not refund a payment."),
            form(Input(type="hidden", name="expected_status", value=order.status),
                 Input(type="hidden", name="target", value="cancelled"),
                 field("Cancellation reason", "note", required=True, maxlength=500),
                 Button("Cancel order", cls="p3-danger"), endpoint="/fulfillment", cls="p3-form"))
        if order.status in {"unfulfilled", "partially_fulfilled", "fulfilled"} else None,
        form(Button("Re-sync payment status"), endpoint="/resync"))
    return Div(*assets(), page_header(order.number, back_href=base(site),
        badges=[state(order.payment_status), state(data["fulfilment_label"]),
                badge("Archived") if link.is_archived else None], secondary_actions=[
            A("Edit order", href=url + "/edit", cls="p3-button"),
            A("Refund gift card", href=url + "/gift-card-refund", cls="p3-button")
                if data.get("gift_only_payment") and order.payment_status != "refunded" else None,
            A("Packing slip", href=url + "/packing-slip", target="_blank", cls="p3-button"),
            A("←", href=base(site, data["prev_id"]), aria_label="Previous order", cls="p3-button")
                if data.get("prev_id") else None,
            A("→", href=base(site, data["next_id"]), aria_label="Next order", cls="p3-button")
                if data.get("next_id") else None]),
        P(order.created_at.strftime("%d %B %Y at %H:%M"), cls="p3-muted"),
        two_column(Div(*groups, fulfil, payment, refund, return_flow,
            card(H2("Returns"), *returns) if returns else None,
            card(H2("Timeline"), form(Label("Add an internal note", Textarea(name="body", required=True,
                maxlength=5000, rows=3, placeholder="Leave a note for your team")),
                Input(type="hidden", name="internal", value="on"), Button("Post comment"),
                endpoint="/comments", cls="p3-form"), Div(*events, cls="p3-timeline"))),
            Div(notes, customer_card, tags, actions)), cls="p3-orders")


def refund_review(site, data, preview, values, csrf):
    order = data["order"]
    url = base(site, data["link"].id)
    return Div(*assets(), page_header("Review refund", back_href=url),
        card(H2("Refund " + money(preview["amount_minor"], order.currency)),
            P("Confirm the selected quantities, shipping and reason before issuing this refund."),
            *[P(f"{line.product_name} × {values['quantities'].get(line.id, 0)}")
              for line in order.lines if values["quantities"].get(line.id)],
            P("Shipping: " + money(preview["shipping_minor"], order.currency)),
            P("Items will be restocked." if values["restock"] else "Items will not be restocked."),
            P(values["reason"]),
            Form(csrf, *[Input(type="hidden", name="qty_" + key, value=qty)
                          for key, qty in values["quantities"].items()],
                Input(type="hidden", name="shipping", value=f"{values['shipping_minor'] / 100:.2f}"),
                Input(type="hidden", name="restock", value="on" if values["restock"] else ""),
                Input(type="hidden", name="reason", value=values["reason"]),
                Input(type="hidden", name="return_id", value=values.get("return_id") or ""),
                Input(type="hidden", name="expected_amount_minor", value=preview["amount_minor"]),
                Input(type="hidden", name="request_key", value=new_id()),
                Label(Input(type="checkbox", name="confirmed", value="on", required=True),
                      " I confirm this exact refund"),
                Div(A("Back to order", href=url, cls="p3-button"),
                    Button("Issue refund", type="submit", cls="p3-primary"), cls="p3-actions"),
                action=url + "/refund-items", method="post", cls="p3-form")), cls="p3-orders")


def packing_slip(site, data):
    order = data["order"]
    return Div(*assets(), Div(H2(site.name), H3("Packing slip"), Strong(order.number),
        P(order.created_at.strftime("%d %B %Y")), _address(order.shipping_address_json or {}),
        Table(Thead(Tr(Th("Item"), Th("SKU"), Th("Quantity"))),
              Tbody(*[Tr(Td(line.product_name + " · " + line.variant_name), Td(line.sku), Td(line.quantity))
                      for line in order.lines])), cls="p3-packing"),
        Button("Print packing slip", type="button", data_print_order="", cls="p3-no-print"), cls="p3-orders")
