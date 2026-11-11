"""Customer-facing subscription portal composition."""

import secrets
from datetime import UTC, datetime, timedelta

from fasthtml.common import (
    H2,
    H3,
    A,
    Button,
    Del,
    Details,
    Div,
    Form,
    Img,
    Input,
    Label,
    Option,
    P,
    Select,
    Small,
    Span,
    Summary,
)
from sqlalchemy import select

from app import commerce
from app.models import (
    MarketingConsent,
    Order,
    Product,
    ProductVariant,
    SiteOrder,
    SubscriptionContract,
    VariantChannelListing,
)
from app.services import money


def labels(site):
    from app.subscription_portal_copy import DEFAULT_PORTAL_COPY
    config = site.published_settings_json or {}
    edited = config.get("portal_copy") or {}
    return {**DEFAULT_PORTAL_COPY, **edited}


def render(db, site, customer, request, session, csrf, tab="next"):
    copy = labels(site)
    root = request.scope.get("site_base", "/sites/" + site.slug)
    base = root + "/account/subscriptions"
    def frequency_text(months):
        unit = copy["month"] if months == 1 else copy["months"]
        return copy["every"].format(count=months, unit=unit)
    contracts = db.scalars(select(SubscriptionContract).where(
        SubscriptionContract.site_id == site.id, SubscriptionContract.tenant_id == site.tenant_id,
        SubscriptionContract.customer_id == customer.id).order_by(
            SubscriptionContract.created_at.desc())).all()
    active = next((row for row in contracts if row.state == "active"), None)
    tabs = (("next", copy["next_order"]), ("subscriptions", copy["subscriptions"]),
            ("orders", copy["order_history"]), ("account", copy["account"]))
    nav = Div(*[A(label, href=base + "?tab=" + key, aria_current="page" if tab == key else None,
                    cls="portal-tab" + (" is-active" if tab == key else ""))
               for key, label in tabs], role="tablist", aria_label="Customer account", cls="portal-tabs")

    def action_form(contract, action, label, *fields, cls="portal-action-form"):
        return Form(csrf(session), Input(type="hidden", name="version", value=contract.version),
                    Input(type="hidden", name="request_key", value=secrets.token_hex(16)),
                    *fields, Button(label, cls="portal-button"), method="post",
                    action=base + "/" + contract.id + "/" + action, cls=cls)

    content = []
    if tab == "next":
        if active:
            config = commerce.settings_for(db, site)
            item_nodes, raw_subtotal, raw_discount = [], 0, 0
            for line in active.lines_json:
                variant_row = db.execute(select(ProductVariant, Product, VariantChannelListing).join(
                    Product, Product.id == ProductVariant.product_id).join(VariantChannelListing,
                    VariantChannelListing.variant_id == ProductVariant.id).where(
                        ProductVariant.id == line["variant_id"], ProductVariant.tenant_id == site.tenant_id,
                        Product.tenant_id == site.tenant_id, Product.is_published.is_(True),
                        Product.status == "active", ProductVariant.is_active.is_(True),
                        VariantChannelListing.channel_id == site.channel_id,
                        VariantChannelListing.currency == "USD")).first()
                product = variant_row[1] if variant_row else None
                listing = variant_row[2] if variant_row else None
                compare = (listing.price_minor * line["quantity"]
                    if listing and commerce.discounted(listing.price_minor, 10) == line["unit_minor"] else None)
                raw_subtotal += (listing.price_minor if compare else int(line["unit_minor"])) * int(line["quantity"])
                if compare:
                    raw_discount += compare - int(line["unit_minor"]) * int(line["quantity"])
                options = db.execute(select(ProductVariant, Product).join(Product,
                    Product.id == ProductVariant.product_id).join(VariantChannelListing,
                    VariantChannelListing.variant_id == ProductVariant.id).where(
                        Product.tenant_id == site.tenant_id,
                        Product.is_published.is_(True), Product.status == "active",
                        ProductVariant.tenant_id == site.tenant_id, ProductVariant.is_active.is_(True),
                        Product.id.in_(config.subscription_product_ids_json or []),
                        VariantChannelListing.channel_id == site.channel_id,
                        VariantChannelListing.currency == "USD")).all()
                variants = db.scalars(select(ProductVariant).join(Product,
                    Product.id == ProductVariant.product_id).join(VariantChannelListing,
                    VariantChannelListing.variant_id == ProductVariant.id).where(
                        Product.id == line["product_id"], Product.tenant_id == site.tenant_id,
                        Product.is_published.is_(True), Product.status == "active",
                        ProductVariant.tenant_id == site.tenant_id, ProductVariant.is_active.is_(True),
                        VariantChannelListing.channel_id == site.channel_id,
                        VariantChannelListing.currency == "USD")).all()
                current_variant_ids = {item["variant_id"] for item in active.lines_json}
                swap_options = [(option, product) for option, product in options
                    if option.id != line["variant_id"] and option.id not in current_variant_ids]
                item_nodes.append(Div(
                    Img(src=product.image_url, alt="", loading="lazy") if product and product.image_url else None,
                    Div(H3(line["name"]), P(frequency_text(active.interval_months), cls="portal-frequency"),
                        P(Del(money(compare, "USD")) if compare else None,
                          Span(money(line["unit_minor"] * line["quantity"], "USD"))), cls="portal-item-price"),
                    Div(Details(Summary(copy["edit"]),
                        action_form(active, "quantity", copy["save_changes"],
                            Input(type="hidden", name="variant_id", value=line["variant_id"]),
                            Label(copy["quantity"], Input(name="quantity", type="number", value=line["quantity"],
                                min=1, max=25, required=True))),
                        action_form(active, "variant", copy["save_changes"],
                            Input(type="hidden", name="from_variant_id", value=line["variant_id"]),
                            Label(copy["variant"], Select(*[Option(option.name, value=option.id,
                                selected=option.id == line["variant_id"]) for option in variants],
                                name="variant_id", required=True))),
                        action_form(active, "frequency", copy["save_changes"],
                            Label(copy["frequency"], Select(*[Option(frequency_text(months), value=months,
                                selected=months == active.interval_months)
                                for months in range(1, 13)], name="months"))), cls="portal-edit-details"),
                        action_form(active, "swap", copy["swap"],
                            Input(type="hidden", name="from_variant_id", value=line["variant_id"]),
                            Label(copy["swap"], Select(*[Option(product.name + " · " + option.name, value=option.id)
                                for option, product in swap_options],
                                name="variant_id", required=True)) if swap_options else None)
                            if swap_options else None,
                        action_form(active, "remove_product", copy["remove_product"],
                            Input(type="hidden", name="variant_id", value=line["variant_id"])),
                        cls="portal-item-actions"), cls="portal-item"))

            product_rows, add_options = [], []
            contract_product_ids = {line["product_id"] for line in active.lines_json}
            if config:
                eligible = db.execute(select(ProductVariant, Product, VariantChannelListing).join(
                    Product, Product.id == ProductVariant.product_id).join(VariantChannelListing,
                    VariantChannelListing.variant_id == ProductVariant.id).where(
                        Product.tenant_id == site.tenant_id, Product.is_published.is_(True),
                        Product.status == "active", ProductVariant.tenant_id == site.tenant_id,
                        ProductVariant.is_active.is_(True),
                        Product.id.in_(config.subscription_product_ids_json or []),
                        VariantChannelListing.channel_id == site.channel_id,
                        VariantChannelListing.currency == "USD")).all()
                for variant, product, listing in eligible:
                    if product.id in contract_product_ids:
                        continue
                    add_options.append(Option(product.name + " · " + variant.name, value=variant.id))
                    product_rows.append(Div(Img(src=product.image_url, alt="", loading="lazy")
                        if product.image_url else None, H3(product.name),
                        P(variant.name + " · " + money(commerce.discounted(listing.price_minor, 10), "USD")),
                        action_form(active, "add_product", copy["add_to_order"],
                            Input(type="hidden", name="variant_id", value=variant.id),
                            Input(type="hidden", name="quantity", value="1")), cls="portal-product"))
            now_date = datetime.now(UTC).date()
            content.extend([
                Div(Div(Small(copy["processing"]), H2(active.next_due_at.strftime("%d %B %Y")),
                        P(frequency_text(active.interval_months))),
                    Div(action_form(active, "reschedule", copy["reschedule"], Label(copy["date"],
                        Input(type="date", name="date", min=(now_date + timedelta(days=1)).isoformat(),
                              max=(now_date + timedelta(days=90)).isoformat(), required=True)),
                        cls="portal-inline-form"), action_form(active, "skip", copy["skip"]),
                        cls="portal-header-actions"), cls="portal-card portal-next-date"),
                Div(H2(copy["items"]), *item_nodes, cls="portal-card"),
                Div(H2(copy["add_product"]),
                    action_form(active, "add_product", copy["add_to_order"],
                        Label(copy["add_product"], Select(*add_options, name="variant_id", required=True)),
                        Input(type="hidden", name="quantity", value="1")), cls="portal-card") if add_options else None,
                Div(H2(copy["order_summary"]),
                    P(Span(copy["subtotal"]), Span(money(raw_subtotal, "USD")), cls="portal-summary-row"),
                    P(Span(copy["discounts"]), Span("−" + money(raw_discount, "USD") if raw_discount else money(0, "USD")), cls="portal-summary-row"),
                    P(Span(copy["shipping"]), Span("Calculated when prepared"), cls="portal-summary-row"),
                    P(copy["tax_note"], cls="portal-note"),
                    P(Span(copy["total"]), Span("Calculated when prepared"), cls="portal-summary-row portal-total"),
                    cls="portal-card portal-summary"),
                Div(H2(copy["shipping_address"]),
                    Form(csrf(session), Input(type="hidden", name="version", value=active.version),
                        Input(type="hidden", name="request_key", value=secrets.token_hex(16)),
                        *[Label(label, Input(name=key, value=(active.recipient_name if key == "recipient_name"
                            else active.destination_json.get(key, "")), required=key != "line2"))
                          for key, label in (("recipient_name", copy["recipient"]), ("line1", copy["address_line"]),
                              ("line2", copy["apartment"]), ("city", copy["city"]),
                              ("state", copy["state"]), ("postal_code", copy["postal_code"]))],
                        Input(type="hidden", name="country", value="US"),
                        Button(copy["change_address"], cls="portal-button"), method="post",
                        action=base + "/" + active.id + "/address", cls="portal-address-form"),
                    cls="portal-card"),
                Div(H2(copy["payment_method"]), P(copy["card_on_file"]),
                    action_form(active, "payment", copy["change_payment"]), cls="portal-card"),
            ])
            if product_rows:
                content.append(Div(H2(copy["recommended"]), *product_rows[:4], cls="portal-card portal-recommendations"))
        else:
            content.append(Div(H2(copy["next_order"]), P(copy["no_subscriptions"]),
                A(copy["subscriptions_heading"], href=base + "?tab=subscriptions"), cls="portal-card"))
    elif tab == "subscriptions":
        content.append(Div(H2(copy["subscriptions_heading"]), *[
            Div(H3(" · ".join(line["name"] for line in row.lines_json)),
                P(copy["status"] + ": " + row.state),
                P(copy["next_delivery"] + ": " + row.next_due_at.strftime("%d %B %Y"))
                    if row.state == "active" else None,
                P(money(sum(line["unit_minor"] * line["quantity"] for line in row.lines_json), "USD")),
                action_form(row, "pause", copy["pause"]) if row.state == "active" else None,
                action_form(row, "resume", copy["resume"]) if row.state == "paused" else None,
                action_form(row, "cancel", copy["cancel"],
                    Label(copy["confirm_cancel"], Input(type="checkbox", name="confirm_cancel", required=True)))
                    if row.state in {"active", "paused"} else None,
                cls="portal-contract") for row in contracts] or [P(copy["no_subscriptions"])], cls="portal-card"))
    elif tab == "orders":
        rows = db.execute(select(SiteOrder, Order).join(Order, Order.id == SiteOrder.order_id).where(
            SiteOrder.site_id == site.id, SiteOrder.tenant_id == site.tenant_id,
            SiteOrder.customer_id == customer.id, Order.tenant_id == site.tenant_id)
            .order_by(Order.created_at.desc())).all()
        content.append(Div(H2(copy["order_history"]), *[
            Div(H3(A(order.number, href=root + "/account/orders/" + link.id)),
                P(order.created_at.strftime("%d %B %Y") + " · " + order.status),
                P(money(order.total_minor, order.currency)), cls="portal-contract")
            for link, order in rows] or [P(copy["no_orders"])], cls="portal-card"))
    else:
        consent = db.scalar(select(MarketingConsent).where(
            MarketingConsent.site_id == site.id, MarketingConsent.tenant_id == site.tenant_id,
            MarketingConsent.customer_id == customer.id))
        content.extend([Div(H2(copy["account"]), P(copy["email"] + ": " + customer.email),
            P(copy["marketing"] + ": " + (copy["marketing_on"] if consent and consent.status == "subscribed" else copy["marketing_off"])),
            Form(csrf(session), Button(copy["unsubscribe"], cls="portal-button"), method="post",
                 action=root + "/account/unsubscribe") if consent and consent.status == "subscribed" else None,
            Form(csrf(session), Button(copy["sign_out"], cls="portal-button"), method="post",
                 action=root + "/account/logout"), cls="portal-card")])
    first_name = (customer.name or "").strip().split(" ")[0] or customer.email
    from app.site_theme import theme_style
    return Div(H2(copy["welcome"].format(name=first_name), cls="portal-welcome"), nav,
               Div(*content, cls="portal-content"), cls="subscription-portal", data_tab=tab,
               style=theme_style(site.published_settings_json or {}) or None)
