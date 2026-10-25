"""Compact draft order screens using the common merchant cards."""
from urllib.parse import urlencode

from fasthtml.common import (
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
from app.services import money


def field(label, name, value="", **attrs):
    return Label(label, Input(name=name, value=value, **attrs))


def hidden_version(draft):
    return Input(type="hidden", name="version", value=draft.version)


def index(site, rows, total, page, filters, people, totals, csrf):
    base = f"/admin/sites/{site.id}/drafts"
    return (
        page_header("Draft orders", primary_action=Form(csrf, Button("Create draft order", cls="a-primary"), method="post", action=base)),
        Nav(A("Orders", href=f"/admin/sites/{site.id}/orders"), A("Drafts", href=base, aria_current="page"),
            A("Abandoned checkouts", href=f"/admin/sites/{site.id}/abandoned-checkouts"), cls="a-tabs"),
        card(Form(field("Search drafts", "q", filters.get("q", ""), type="search"),
            Label("Status", Select(Option("All", value=""), *[Option(label, value=value,
                selected=filters.get("status")==value) for value, label in
                (("open", "Open"), ("invoice_sent", "Invoice sent"), ("completed", "Completed"))], name="status")),
            Button("Filter"), method="get", cls="d-fields"),
            Div(Table(Thead(Tr(*[Th(x) for x in ("Draft", "Updated", "Customer", "Status", "Total")])),
                Tbody(*[Tr(Td(A("#"+row.id[:8].upper(), href=base+"/"+row.id)),
                    Td(row.updated_at.strftime("%d %b %Y")), Td(people.get(row.customer_id, "No customer")),
                    Td(badge(row.status.replace("_", " ").title())), Td(money(totals[row.id], row.currency))) for row in rows])),
                cls="a-table-wrap") if rows else empty_state("Create your first draft", "Build an order for a customer, send an invoice and record a manual payment."),
            P(f"Page {page} · {total} drafts"),
            A("Previous", href=base+"?"+urlencode(filters | {"page": page-1})) if page>1 else None,
            A("Next", href=base+"?"+urlencode(filters | {"page": page+1})) if page*25<total else None),
        Link(rel="stylesheet", href="/static/admin/drafts.css"),
    )


def editor(site, draft, lines, people, csrf, subtotal, total, notice=""):
    base = f"/admin/sites/{site.id}/drafts/{draft.id}"
    def form(*children, action=base):
        return Form(csrf, hidden_version(draft), *children, method="post", action=action, cls="d-fields")
    if draft.status == "completed":
        return (page_header("Draft #"+draft.id[:8].upper(), back_href=f"/admin/sites/{site.id}/drafts",
            badges=[badge("Completed", "success")]), card(P("Manual payment recorded. Stock is reserved for fulfilment."),
                A("View order", href=f"/admin/sites/{site.id}/orders/{draft.site_order_id}", cls="e-button")))
    def amount(value):
        return f"{value//100}.{value%100:02}"
    main = Div(
        card(H3("Items"),
            Div(*[Div(Div(P(line.title), Small(line.sku or "Custom item")),
                P(f"{line.quantity} × {money(line.unit_price_minor, draft.currency)}"),
                form(Button("Remove", aria_label="Remove "+line.title), action=base+"/lines/"+line.id+"/remove"), cls="d-line") for line in lines])
            if lines else P("Add a product or a custom item to get started."),
            form(Label("Find products", Input(type="search", data_draft_variant_search="", placeholder="Search by name or SKU", autocomplete="off")),
                Label("Product option", Select(Option("Choose a product", value=""), name="variant_id", aria_label="Product option", data_draft_variant_results="", required=True)),
                field("Quantity", "quantity", 1, type="number", min=1, max=999), Button("Add product"), action=base+"/lines"),
            Details(Summary("Add custom item"), form(field("Item title", "title", required=True, maxlength=220),
                field("SKU", "sku"), field("Price", "unit_price", "0.00", type="number", min=0, step="0.01"),
                field("Quantity", "quantity", 1, type="number", min=1, max=999), Button("Add custom item"), action=base+"/lines"))),
        card(H3("Order details"), form(
            Label("Customer", Select(Option("Choose customer", value=""), *[Option(person.name or person.email,
                value=person.id, selected=person.id==draft.customer_id) for person in people], name="customer_id", aria_label="Customer", data_draft_customers="")),
            Label("Find customers", Input(type="search", data_draft_customer_search="", placeholder="Search name or email")),
            Details(Summary("New customer"), field("New customer email", "customer_email", type="email"), field("New customer name", "customer_name")),
            *[field("Quantity: "+line.title, "quantity_"+line.id, line.quantity, type="number", min=1, max=999) for line in lines],
            Div(field("Discount", "discount", amount(draft.discount_minor), type="number", min=0, step="0.01"),
                field("Shipping", "shipping", amount(draft.shipping_minor), type="number", min=0, step="0.01"), cls="d-fields"),
            Details(Summary("Shipping address"), *[field(label, key, (draft.destination_json or {}).get(key, "")) for key, label in
                (("name", "Recipient name"), ("line1", "Address"), ("line2", "Apartment or suite"), ("city", "City"),
                 ("state", "State or region"), ("postal_code", "Postcode"), ("country", "Country code"))]),
            Label("Note", Textarea(draft.note, name="note", rows=3)), Button("Save draft", cls="a-primary"))),
    )
    def payment_row(label, value, *, total=False):
        return Div(Span(label), Strong(money(value, draft.currency)),
            cls="d-payment-row" + (" d-payment-total" if total else ""))
    aside = Div(card(H3("Payment"), payment_row("Subtotal", subtotal),
        payment_row("Discount", draft.discount_minor), payment_row("Shipping", draft.shipping_minor),
        payment_row("Tax", 0), payment_row("Total", total, total=True),
        P("Manual payment only. Online payment links are not supported for draft orders."),
        P("No automatic tax calculation. Review tax obligations and totals before recording payment."),
        form(Button("Send invoice email"), action=base+"/invoice"),
        form(Label(Input(type="checkbox", name="mark_as_paid", required=True), "Payment received outside FastShop", cls="d-check"),
            Button("Mark as paid and create order", cls="a-primary"), action=base+"/complete")),
        card(H3("Inventory"), P("Stock is checked and reserved when this draft is completed. Saving or emailing a draft does not reserve stock.")))
    return (Link(rel="stylesheet", href="/static/admin/drafts.css"), Script(src="/static/admin/drafts.js", defer=True),
        page_header("Draft #"+draft.id[:8].upper(), back_href=f"/admin/sites/{site.id}/drafts", badges=[badge(draft.status.replace("_", " ").title())]),
        P(notice, role="status") if notice else None,
        Div(two_column(main, aside), data_draft_editor=base))


def abandoned(site, rows):
    return (page_header("Abandoned checkouts", back_href=f"/admin/sites/{site.id}/orders"),
        card(P("Checkout records begin when a customer starts checkout. Recovery links are unavailable because this checkout does not provide a safe recovery mechanism."),
            Div(Table(Thead(Tr(*[Th(x) for x in ("Started", "Customer", "Cart", "Total", "Checkout state")])),
                Tbody(*[Tr(Td(attempt.created_at.strftime("%d %b %Y %H:%M")), Td(person.email),
                    Td(*[P(f'{line.get("name", "Item")} × {line.get("quantity", 0)}') for line in quote.snapshot_json.get("lines", [])]),
                    Td(money(quote.total_minor, quote.currency)), Td(badge(attempt.state.title())))
                    for attempt, quote, person in rows])), cls="a-table-wrap") if rows else
            empty_state("No abandoned checkouts", "Tracking starts when checkout records exist. No customers have a pending checkout for this store.")))
