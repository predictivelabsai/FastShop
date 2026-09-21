"""FastShop-branded merchant US commerce settings and sandbox tax preview."""

from __future__ import annotations

import re

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
    Li,
    Option,
    P,
    Select,
    Small,
    Summary,
    Ul,
)
from sqlalchemy import select
from starlette.responses import RedirectResponse

from app import commerce, content, site_builder_services
from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway, credentials
from app.models import Product, ProductVariant, SiteCommerceSettings
from app.services import CommerceError, money


def register_commerce_routes(rt, actor, csrf, check_csrf, shell, error):
    def amount(value, *, optional=False):
        if optional and not str(value).strip():
            return None
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise CommerceError("Enter amounts in whole USD cents.") from exc
        if not 0 <= result <= 1_000_000:
            raise CommerceError("Amount must be between 0 and 1,000,000 cents.")
        return result

    def address_fields(prefix, values, *, origin=False):
        return Div(*[Label(label, Input(name=prefix + key, value=values.get(key, ""), maxlength=200))
            for key, label in [("line1", "Street address"), ("line2", "Address line 2"), ("city", "City"), ("postal_code", "Postal / ZIP code")]],
            Label("Country", Select(*[Option(code, value=code, selected=code == values.get("country", "EE"))
                for code in sorted(commerce.EU_COUNTRIES)], name=prefix + "country")) if origin else Input(type="hidden", name=prefix + "country", value="US"),
            Label("State / region", Input(name=prefix + "state", value=values.get("state", ""))) if origin else
            Label("US state", Select(*[Option(name, value=code) for code, name in commerce.US_STATES.items()], name=prefix + "state")))

    @rt("/admin/sites/{site_id}/commerce", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                config = commerce.settings_for(db, site, create=True)
                products = list(db.scalars(select(Product).where(Product.tenant_id == site.tenant_id).order_by(Product.name)))
                variants = list(db.scalars(select(ProductVariant).where(ProductVariant.tenant_id == site.tenant_id).order_by(ProductVariant.sku)))
                key, webhook = credentials(site)
                return shell("US commerce", A("← Site settings", href=f"/admin/sites/{site.id}"),
                    P(f"{site.name} · USD · EU fulfilment → United States"),
                    Div(H2("Sandbox setup — no live payments"),
                        P("Shipping rates are merchant settings, not carrier quotes. Sales tax is calculated by Stripe Tax from the full delivery address, product classifications and configured registrations."),
                        Ul(Li("Stripe test key: " + ("configured" if key.startswith(("sk_test_", "rk_test_")) else "required")),
                           Li("Webhook signing secret: " + ("configured" if webhook else "required")),
                           Li("Shipping fee: " + (money(config.shipping_minor, "USD") if config.shipping_minor is not None else "not configured")),
                           Li("Sandbox mode enables accounts, email capture, one-time checkout and eligible product subscriptions when configuration is complete. Provider acceptance and worker scheduling must be verified before launch. Live payments are disabled."), cls="e-readiness"), cls="e-note"),
                    Div(Div(Form(csrf(session), Input(type="hidden", name="version", value=config.version),
                        H2("Market and shipping"),
                        Label("Commerce mode", Select(Option("Disabled", value="disabled", selected=config.mode == "disabled"),
                            Option("Stripe sandbox", value="sandbox", selected=config.mode == "sandbox"), name="mode")),
                        H3("Actual EU fulfilment origin"), P("Confirm the warehouse address. The company address is not automatically used as the shipping origin."),
                        address_fields("origin_", config.origin_json, origin=True),
                        Label("Standard US shipping (USD cents; blank means unconfigured)", Input(name="shipping_minor", type="number", min="0", step="1", value=config.shipping_minor if config.shipping_minor is not None else "")),
                        Label("Free shipping threshold after discounts (USD cents)", Input(name="free_shipping_threshold_minor", type="number", min="0", step="1", value=config.free_shipping_threshold_minor)),
                        Details(Summary("Allowed US destinations"), Div(*[Label(Input(type="checkbox", name="states", value=code, checked=code in config.allowed_states_json), " " + name) for code, name in commerce.US_STATES.items()], cls="e-states")),
                        H2("Stripe Tax and subscriptions"), P("Choose reviewed Stripe tax codes. Tablets and bottles can have different tax treatment. A failed tax lookup never becomes zero tax."),
                        *[Div(H3(product.name), Label("Stripe product tax code", Input(name="tax_" + product.id, value=config.product_tax_codes_json.get(product.id, ""), placeholder="txcd_…")),
                            Label(Input(type="checkbox", name="subscriptions", value=product.id, checked=product.id in config.subscription_product_ids_json), " Eligible for monthly subscriptions")) for product in products],
                        Label(Input(type="checkbox", name="tax_reviewed", checked=config.tax_registration_reviewed), " I have reviewed this merchant's registrations and product categories in Stripe Tax."),
                        Div(H3("Discount policy"), P("Monthly subscriptions save 10%. A verified first-order offer takes another 10% off the reduced merchandise subtotal, once only: 19% combined before cent rounding. Renewals receive only the subscription saving. Shipping and tax are excluded from promotional discounts."), cls="e-note"),
                        Button("Save commerce settings", cls="e-button"), method="post", cls="e-form"), cls="e-card"),
                        Div(H2("Preview a US tax quote"), P("Sandbox only. No order is placed and no payment is taken. Configure and save the settings first."),
                            Form(csrf(session), Label("Product option", Select(*[Option(v.sku + " · " + v.name, value=v.id) for v in variants], name="variant_id")),
                                Label("Quantity", Input(name="quantity", type="number", min="1", max="25", value="1")),
                                Label(Input(type="checkbox", name="subscription"), " Monthly subscription"),
                                Label(Input(type="checkbox", name="first_order"), " Simulate verified first-order offer (merchant preview only)"),
                                address_fields("destination_", {}), Button("Calculate sandbox tax", cls="e-button"), method="post", action=f"/admin/sites/{site.id}/commerce/quote", cls="e-form"), cls="e-card"), cls="e-grid"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/commerce", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                site = site_builder_services.lock_site(db, site.id, actor(session), site.version)
                config = commerce.settings_for(db, site, create=True)
                config = db.scalar(select(SiteCommerceSettings).where(SiteCommerceSettings.id == config.id,
                    SiteCommerceSettings.site_id == site.id, SiteCommerceSettings.tenant_id == site.tenant_id).with_for_update().execution_options(populate_existing=True))
                if config.version != int(form.get("version", 0)):
                    raise CommerceError("Settings changed in another session. Reload before saving.")
                mode = str(form.get("mode", "disabled"))
                if mode not in {"disabled", "sandbox"}:
                    raise CommerceError("Only disabled or sandbox mode is available before live approval.")
                states = sorted(set(form.getlist("states")))
                if not states or not set(states).issubset(commerce.US_STATES):
                    raise CommerceError("Select valid US delivery destinations.")
                origin = {key: str(form.get("origin_" + key, "")).strip()[:200] for key in ("line1", "line2", "city", "state", "postal_code", "country")}
                if origin["country"] not in commerce.EU_COUNTRIES:
                    raise CommerceError("Choose an EU fulfilment country.")
                products = set(db.scalars(select(Product.id).where(Product.tenant_id == site.tenant_id)))
                eligible = sorted(set(form.getlist("subscriptions")))
                if not set(eligible).issubset(products):
                    raise CommerceError("Choose products belonging to this site.")
                codes = {pid: str(form.get("tax_" + pid, "")).strip() for pid in products if form.get("tax_" + pid)}
                if any(not re.fullmatch(r"txcd_\d+", code) for code in codes.values()):
                    raise CommerceError("Stripe tax categories must use txcd_ followed by digits.")
                from app.site_samples import invalidate_reviews, pending_reviews, values_for
                previous_values = values_for(site, config)
                config.mode, config.origin_json, config.allowed_states_json = mode, origin, states
                config.shipping_minor = amount(form.get("shipping_minor", ""), optional=True)
                config.free_shipping_threshold_minor = amount(form.get("free_shipping_threshold_minor", "7500"))
                config.product_tax_codes_json, config.subscription_product_ids_json = codes, eligible
                config.tax_registration_reviewed = form.get("tax_reviewed") == "on"
                # Track edits to fields already participating in sample review.
                if site.settings_json.get("sample_fields"):
                    site.settings_json = invalidate_reviews(site.settings_json, previous_values, values_for(site, config))
                if mode == "sandbox" and pending_reviews(site.settings_json):
                    raise CommerceError("Review merchant details and replace samples before enabling Stripe sandbox. Save changes with commerce disabled first.")
                config.version += 1
                site.version += 1
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/commerce", status_code=303)
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/commerce/quote", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                config = commerce.settings_for(db, site)
                if not config:
                    raise CommerceError("Save the commerce settings first.")
                destination = {key: form.get("destination_" + key, "") for key in ("line1", "line2", "city", "state", "postal_code", "country")}
                quote = commerce.quote_order(db, site, config, [{"variant_id": str(form.get("variant_id", "")),
                    "quantity": int(form.get("quantity", "1")), "subscription": form.get("subscription") == "on"}],
                    destination, StripeGateway(site), first_order_discount=form.get("first_order") == "on")
                db.commit()
                return shell("Sandbox tax quote", A("← Commerce settings", href=f"/admin/sites/{site.id}/commerce"),
                    Div(H2("Delivery to " + commerce.US_STATES[destination["state"]]),
                        P("Merchandise: " + money(quote.subtotal_minor, "USD")), P("Savings: −" + money(quote.discount_minor, "USD")),
                        P("Shipping: " + money(quote.shipping_minor, "USD")), P("Sales tax: " + money(quote.tax_minor, "USD")),
                        P("Total: " + money(quote.total_minor, "USD"), cls="e-total"),
                        *[P(str(item.get("tax_rate_details", {}).get("state", "US")) + " · " + str(item.get("taxability_reason", "tax")) + " · " + money(item.get("amount", 0), "USD")) for item in quote.snapshot_json["tax_breakdown"]],
                        Small("Stripe calculation " + quote.provider_id + " · expires " + quote.expires_at.isoformat()),
                        P("This is a sandbox calculation, not an order or a payment. It reflects this Stripe account's configured tax registrations."), cls="e-card"))
        except (CommerceError, ValueError) as exc:
            return error(exc)
