"""Customer indexes and records inside the shared merchant shell."""

import json
from urllib.parse import urlencode

from fasthtml.common import (
    A,
    Button,
    Details,
    Div,
    Form,
    Input,
    Label,
    Link,
    Nav,
    P,
    Small,
    Span,
    Summary,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Tr,
)

from app.admin_ui import badge, card, empty_state, page_header, timeline, two_column
from app.catalog_admin_ui import area, check, choice, field
from app.customer_admin import ADDRESS_LIMITS
from app.services import money


def assets():
    return Link(rel="stylesheet", href="/static/admin/customers.css")


def tabs(site, mail=False):
    base = f"/admin/sites/{site.id}/customers"
    return Nav(A("All customers", href=base, cls="is-active" if not mail else ""),
               A("Email queue", href=base + "/mail", cls="is-active" if mail else ""),
               cls="a-tabs", aria_label="Customer views")


def customer_index(site, rows, total, filters, page, csrf, segments):
    base = f"/admin/sites/{site.id}/customers"
    query = urlencode(filters)
    filter_form = Form(field("Search customers", "q", filters.get("q", ""), "search", placeholder="Name, email or phone"),
        choice("Email consent", "consent", [("", "Any consent"), ("subscribed", "Subscribed"), ("not_subscribed", "Not subscribed")], filters.get("consent", "")),
        field("Tag", "tag", filters.get("tag", "")), field("Location", "location", filters.get("location", "")),
        Details(Summary("More filters"), Div(*[field(label, key, filters.get(key, ""), "number", min="0")
            for key, label in (("orders_min", "Minimum orders"), ("orders_max", "Maximum orders"))],
            *[field(label, key + "_amount", f"{int(filters[key]) / 100:.2f}" if filters.get(key) else "", "number", min="0", step="0.01")
              for key, label in (("spent_min", "Minimum spent"), ("spent_max", "Maximum spent"))], cls="u-filter-extra")),
        choice("Sort", "sort", [("created", "Newest customer"), ("name", "Name A to Z"), ("spent", "Amount spent"), ("orders", "Most orders")], filters.get("sort", "created")),
        Button("Apply filters", cls="a-button"), A("Clear", href=base, cls="a-button"), method="get", cls="u-filters")
    body = []
    for row in rows:
        c = row.customer
        body.append(Tr(Td(Input(type="checkbox", name="customer_ids", value=c.id, data_row_select="", aria_label="Select " + (c.name or c.email))),
            Td(A(c.name or c.email, href=base + "/" + c.id, cls="u-record-link")), Td(c.email),
            Td(badge("Subscribed" if row.email_consent else "Not subscribed", "success" if row.email_consent else "neutral")),
            Td(row.location or "No address"), Td(str(row.orders)), Td(money(row.spent, row.currency))))
    table = Form(csrf, Div(Label("Add tags to selected customers", Input(name="tags", placeholder="VIP, wholesale")),
            Button("Add tags", cls="a-button"), cls="u-bulk"),
        Div(Table(Thead(Tr(Th(Input(type="checkbox", data_select_all="", aria_label="Select all customers on this page"), scope="col"),
            *[Th(name, scope="col") for name in ("Customer name", "Email", "Email consent", "Location", "Orders", "Amount spent")])), Tbody(*body)), cls="a-table-wrap"),
        method="post", action=base + "/bulk", data_index_table="") if rows else empty_state("No customers found", "Create a customer or adjust your filters to find an existing record.", A("Add customer", href=base + "/new", cls="a-button"))
    pages = max(1, (total + 24) // 25)
    pagination = Div(A("Previous", href=base + "?" + urlencode({**filters, "page": page - 1})) if page > 1 else Span("Previous"),
        Span(f"Page {page} · {total} customer" + ("" if total == 1 else "s")),
        A("Next", href=base + "?" + urlencode({**filters, "page": page + 1})) if page < pages else Span("Next"), cls="u-pagination")
    saved = Div(*[A(segment.name, href=base + "?segment=" + segment.id, cls="u-chip") for segment in segments], cls="u-chips") if segments else None
    save = Details(Summary("Save current filters as a segment"), Form(csrf,
        field("Segment name", "name", required="required", maxlength="100"),
        Input(type="hidden", name="filters_json", value=json.dumps(filters)),
        Button("Save segment", cls="a-button"), method="post", action=base + "/segments", cls="u-inline"), cls="u-segment")
    chip_labels = {"q": "Search", "consent": "Email consent", "orders_min": "Minimum orders",
                   "orders_max": "Maximum orders", "spent_min": "Minimum spent", "spent_max": "Maximum spent"}
    chips = Div(*[Span(chip_labels.get(key, key.title()) + ": " +
        (f"{int(value) / 100:.2f}" if key in {"spent_min", "spent_max"} else
         value.replace("_", " ").title() if key == "consent" else value), cls="u-chip")
        for key, value in filters.items() if value and key != "sort"], cls="u-chips")
    return Div(assets(), page_header("Customers", primary_action=A("Add customer", href=base + "/new", cls="a-button a-button-primary"),
        secondary_actions=(A("Export", href=base + "/export?" + query, cls="a-button"), A("Import", href=base + "/import", cls="a-button"))),
        Div(tabs(site), saved, filter_form, chips, table, pagination, cls="a-card u-index"), save, cls="u-customers")


def contact_form(site, customer, csrf, email_consent=False):
    base = f"/admin/sites/{site.id}/customers"
    c = customer
    def get(key, fallback=""):
        return getattr(c, key, fallback) if c else fallback

    def consent_summary(kind):
        at = get(kind + "_consent_at", None)
        source = get(kind + "_consent_source") or "Not recorded"
        return Small(kind.title() + " consent: " + source +
                     (" · " + at.strftime("%d %b %Y, %H:%M UTC") if at else ""))
    return Form(csrf,
        field("Name", "name", get("name"), maxlength="160"),
        field("Email address", "email", get("email"), "email", required="required", maxlength="320"),
        field("Phone", "phone", get("phone"), "tel", maxlength="60"),
        check("Email marketing consent", "email_marketing_consent", email_consent),
        Small("Consent changes are recorded with the time and staff source."),
        consent_summary("email") if c else None,
        check("SMS marketing consent", "sms_marketing_consent", get("sms_marketing_consent", False)),
        consent_summary("sms") if c else None,
        field("Tags", "tags", ", ".join(get("tags_json", []) or []), placeholder="Separate tags with commas"),
        area("Customer note", "note", get("note"), maxlength="5000"),
        choice("Account state", "account_state", [("active", "Active"), ("disabled", "Disabled"), ("invited", "Invited")], get("account_state", "active")),
        Small("Invited is an administrative status. Customers sign in using the store's email link."),
        Button("Save customer", type="submit", cls="a-button a-button-primary"),
        method="post", action=base + ("/" + c.id if c else "/new"), data_dirty_guard="", cls="u-form")


def address_form(site, customer, csrf, address=None):
    base = f"/admin/sites/{site.id}/customers/{customer.id}/addresses"
    def get(key, fallback=""):
        return getattr(address, key, fallback) if address else fallback

    fields = Div(*[field(label, key, get(key), **({"required": "required"} if key in {"line1", "city", "country"} else {}), maxlength=str(ADDRESS_LIMITS[key]))
        for key, label in (("name", "Address name"), ("phone", "Address phone"), ("line1", "Address line 1"),
            ("line2", "Address line 2"), ("city", "City"), ("state", "County / state"),
            ("postal_code", "Postcode"), ("country", "Country"))], cls="u-address-grid")
    return Form(csrf, fields, check("Default address", "is_default", get("is_default", False)),
        Div(Button("Save address", type="submit", cls="a-button"),
            Button("Delete address", type="submit", name="action", value="delete", formnovalidate=True, cls="a-button") if address else None, cls="u-inline"),
        method="post", action=base + ("/" + address.id if address else ""), cls="u-form")


def customer_detail(site, customer, row, orders, addresses, events, csrf):
    base = f"/admin/sites/{site.id}/customers"
    metrics = Div(*[Div(str(value), Span(label)) for label, value in (
        ("Amount spent", money(row.spent, row.currency)), ("Orders", row.orders),
        ("Average order value", money(row.spent // row.orders if row.orders else 0, row.currency)),
        ("Customer since", customer.created_at.strftime("%d %b %Y")))], cls="u-metrics")
    order_card = card(Div(Table(Thead(Tr(Th("Order"), Th("Date"), Th("Payment"), Th("Total"))),
        Tbody(*[Tr(Td(A(order.number, href=f"/admin/sites/{site.id}/orders/{link.id}", cls="u-record-link")),
            Td(order.created_at.strftime("%d %b %Y")), Td(badge(order.payment_status.replace("_", " ").title())),
            Td(money(order.total_minor, order.currency))) for link, order in orders])), cls="a-table-wrap")
        if orders else P("No orders yet. Confirmed purchases appear here."), title="Orders")
    addresses_card = card(*[Details(Summary((a.name or a.line1) + (" · Default" if a.is_default else "")),
        P(", ".join(v for v in (a.line1, a.line2, a.city, a.postal_code, a.country) if v)),
        address_form(site, customer, csrf, a)) for a in addresses],
        Details(Summary("Add address"), address_form(site, customer, csrf)), title="Address book")
    event_card = card(Form(csrf, area("Internal comment", "body", required="required", maxlength="5000"),
            Button("Add comment", cls="a-button"), method="post", action=base + "/" + customer.id + "/comments"),
        timeline([Div(Small(at.strftime("%d %b %Y, %H:%M")), P(Span(title, cls="u-event-title")), P(body)) for at, title, body in events]), title="Timeline")
    return Div(assets(), page_header(customer.name or customer.email, back_href=base,
        badges=(badge(customer.account_state.title(), "critical" if not customer.is_active else "neutral"),)),
        metrics, Small("Amount spent is net of completed refunds in " + row.currency + "."),
        two_column(Div(order_card, addresses_card, event_card, cls="u-stack"),
            card(contact_form(site, customer, csrf, row.email_consent), title="Customer details")), cls="u-customers")


def new_customer(site, csrf):
    return Div(assets(), page_header("Add customer", back_href=f"/admin/sites/{site.id}/customers"),
        card(contact_form(site, None, csrf), title="Contact information"), cls="u-customers u-new")


def import_page(site, csrf, results=None, source="", applied=False):
    base = f"/admin/sites/{site.id}/customers"
    preview = None
    if results is not None:
        valid = sum(not r["error"] for r in results)
        preview = card(P(f"{valid} valid row(s), {len(results) - valid} row error(s)."),
            Div(Table(Thead(Tr(Th("Row"), Th("Email"), Th("Action"), Th("Error"))),
                Tbody(*[Tr(Td(str(r["row"])), Td(r["email"]), Td(r["action"]), Td(r["error"] or ("Imported" if applied else "Ready"))) for r in results])), cls="a-table-wrap"),
            Form(csrf, Input(type="hidden", name="csv_text", value=source), Input(type="hidden", name="apply", value="yes"),
                Button("Import valid customers", cls="a-button a-button-primary"), method="post", action=base + "/import") if valid and not applied else None,
            title="Import result" if applied else "Dry-run preview")
    return Div(assets(), page_header("Import customers", back_href=base),
        card(P("Upload a Shopify-compatible UTF-8 CSV. Existing customers are updated by email. Preview rows before importing; rejected rows remain unchanged."),
            Form(csrf, Label(Span("Choose CSV file", cls="a-button"), Input(type="file", name="file", accept=".csv,text/csv", required=True, cls="u-file"), cls="u-dropzone"),
                P("CSV files up to 2 MB and 5,000 customers. Email is required."),
                Button("Preview import", cls="a-button a-button-primary"), method="post", action=base + "/import", enctype="multipart/form-data"), title="Customer CSV"),
        preview, cls="u-customers")


def mail_page(site, messages):
    return Div(assets(), page_header("Email queue"), card(tabs(site, True),
        Div(Table(Thead(Tr(Th("Message"), Th("Status"), Th("Attempts"), Th("Created"))),
            Tbody(*[Tr(Td(message.kind.replace("_", " ").title()), Td(badge(message.status.title())),
                Td(str(message.attempts)), Td(message.created_at.strftime("%d %b %Y, %H:%M"))) for message in messages])), cls="a-table-wrap")
        if messages else P("No queued messages yet. Transactional and account emails appear here.")), cls="u-customers")
