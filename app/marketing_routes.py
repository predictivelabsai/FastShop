"""Static public SaaS marketing routes, independent of every tenant surface."""
from __future__ import annotations

import secrets

from fasthtml.common import (
    H1,
    H2,
    H3,
    A,
    Article,
    Body,
    Button,
    Details,
    Div,
    Footer,
    Form,
    Head,
    Header,
    Html,
    Img,
    Input,
    Label,
    Li,
    Link,
    Main,
    Meta,
    Nav,
    Ol,
    P,
    Section,
    Small,
    Span,
    Strong,
    Summary,
    Svg,
    Title,
    ft,
    to_xml,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.responses import HTMLResponse, RedirectResponse

from app import auth, plans, signup_services
from app.config import settings
from app.db import SessionLocal
from app.integrations.commerce_email import dispatch_mail
from app.models import User
from app.services import CommerceError
from app.site_blocks import BLOCK_TYPES
from app.site_generation import MAX_PAGES
from app.ui import static_asset

GITHUB_URL = "https://github.com/predictivelabsai/FastShop"
def _head(title: str, description: str):
    return (
        Title(f"{title} — FastShop"),
        Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Meta(name="description", content=description),
        Meta(name="robots", content="index,follow"),
        Meta(name="theme-color", content="#ffffff"),
        Meta(property="og:title", content=f"{title} — FastShop"),
        Meta(property="og:description", content=description),
        Meta(property="og:type", content="website"),
        Link(rel="icon", href=static_asset("favicon.svg"), type="image/svg+xml"),
        Link(rel="stylesheet", href=static_asset("fonts.css")),
        Link(rel="stylesheet", href=static_asset("platform.css")),
        Link(rel="stylesheet", href=static_asset("marketing.css")),
    )
def _document(title: str, description: str, *content, private: bool = False):
    markup = to_xml(
        Html(Head(*_head(title, description)), Body(*content), lang="en"),
        indent=True,
    )
    headers = (
        {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"}
        if private
        else None
    )
    return HTMLResponse("<!doctype html>\n" + markup, headers=headers)
def _brand():
    return A(
        Span("F", cls="m-brand-mark", aria_hidden="true"),
        Span("FastShop"),
        href="/marketing/",
        cls="m-brand",
        aria_label="FastShop marketing home",
    )
def _header():
    return Header(
        Div(
            _brand(),
            Nav(
                A("How it works", href="#how-it-works"),
                A("Commerce", href="#commerce"),
                A("Migration", href="#migration"),
                A("CMS", href="#cms"),
                aria_label="Marketing navigation",
                cls="m-nav-links",
            ),
            Div(
                A("Sign in", href="/login", cls="m-sign-in"),
                A("Create workspace", href="/signup", cls="m-button m-button-small"),
                cls="m-nav-actions",
            ),
            cls="m-nav",
        ),
        cls="m-header",
    )
def _footer(*, on_marketing: bool = False, show_signup_link: bool = True):
    page_prefix = "" if on_marketing else "/marketing/"
    return Footer(
        Div(
            Div(
                _brand(),
                P("Build the storefront. Keep control of the launch."),
                cls="m-footer-intro",
            ),
            Nav(
                A("How it works", href=f"{page_prefix}#how-it-works"),
                A("Features", href=f"{page_prefix}#commerce"),
                A("Pricing preview", href=f"{page_prefix}#pricing"),
                A("FAQ", href=f"{page_prefix}#faq"),
                aria_label="Page links",
            ),
            Nav(
                A("Create workspace", href="/signup") if show_signup_link else None,
                A(
                    "Documentation on GitHub",
                    href=GITHUB_URL,
                    target="_blank",
                    rel="noopener noreferrer",
                ),
                aria_label="FastShop links",
            ),
            cls="m-footer-grid",
        ),
        Div(
            Small("© FastShop"),
            Small("No tracking. No third-party page requests."),
            cls="m-footer-base",
        ),
        cls="m-footer",
    )


def _google_mark():
    return Svg(
        ft(
            "path",
            d=(
                "M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92"
                "c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57"
                "c2.08-1.92 3.28-4.74 3.28-8.09z"
            ),
            fill="#4285f4",
        ),
        ft(
            "path",
            d=(
                "M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77"
                "c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53"
                "H2.18v2.84C3.99 20.53 7.7 23 12 23z"
            ),
            fill="#34a853",
        ),
        ft(
            "path",
            d=(
                "M5.84 14.09A6.6 6.6 0 0 1 5.49 12c0-.73.13-1.43.35-2.09"
                "V7.07H2.18A11 11 0 0 0 1 12c0 1.78.43 3.45 1.18 4.93"
                "l2.85-2.22.81-.62z"
            ),
            fill="#fbbc05",
        ),
        ft(
            "path",
            d=(
                "M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15"
                "C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07"
                "l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
            ),
            fill="#ea4335",
        ),
        viewBox="0 0 24 24",
        aria_hidden="true",
        focusable="false",
    )
def _workflow_step(label: str, title: str, body: str, evidence: tuple[tuple[str, str], ...]):
    return Li(
        Span(label, cls="m-step-label"),
        Div(H3(title), P(body)),
        Div(
            *(
                Div(Span(item_label), Strong(value))
                for item_label, value in evidence
            ),
            cls="m-step-evidence",
            aria_label=f"{title} evidence",
        ),
    )
def _proof_surface():
    return Div(
        Div(
            Div(
                Div(
                    Div(
                        Span(cls="m-proof-dot"),
                        Span(cls="m-proof-dot"),
                        Span(cls="m-proof-dot"),
                        aria_hidden="true",
                        cls="m-proof-dots",
                    ),
                    Span("app.fastshop.dev/north-and-pine", cls="m-proof-url"),
                    Span("Draft saved", cls="m-proof-status"),
                    cls="m-proof-bar",
                ),
                Div(
                    Div(
                        Div(
                            Span("F", cls="m-builder-mark"),
                            Div(Small("Store"), Strong("North & Pine")),
                            cls="m-builder-identity",
                        ),
                        Div(
                            Span("Home", cls="is-active"),
                            Span("Shop"),
                            Span("Journal"),
                            Span("About"),
                            cls="m-builder-pages",
                        ),
                        Div(
                            Small("AI builder"),
                            P("Tighten the home page around the new collection."),
                            Span("Prepare update", cls="m-builder-prepare"),
                            cls="m-builder-agent",
                        ),
                        cls="m-builder-rail",
                    ),
                    Div(
                        Div(
                            Span("North & Pine", cls="m-proof-brand"),
                            Div(
                                Span("Shop"),
                                Span("Journal"),
                                Span("About"),
                                cls="m-proof-links",
                            ),
                            cls="m-proof-nav",
                        ),
                        Div(
                            Div(
                                Small("New season · draft copy"),
                                P("Useful objects, made for unhurried homes.", cls="m-proof-title"),
                                Span("Explore the collection", cls="m-proof-cta"),
                                cls="m-proof-copy",
                            ),
                            Img(
                                src="/static/img/product-interior.jpg",
                                alt="Bright living-room interior with an arc lamp and wooden table, from the store catalog",
                                cls="m-proof-photo",
                            ),
                            cls="m-proof-hero",
                        ),
                        Div(
                            Div(
                                Img(
                                    src="/static/img/product-cactus.jpg",
                                    alt="Cactus in a terracotta pot on a pink ground",
                                ),
                                Strong("Terracotta planter"),
                                Small("Draft item"),
                                cls="m-product-card",
                            ),
                            Div(
                                Img(
                                    src="/static/img/product-armchair.jpg",
                                    alt="Yellow armchair beside a brass floor lamp",
                                ),
                                Strong("Canary armchair"),
                                Small("Draft item"),
                                cls="m-product-card",
                            ),
                            Div(
                                Img(
                                    src="/static/img/storefront-object.jpg",
                                    alt="Dried pampas arrangement in a ceramic vase on a leather sofa",
                                ),
                                Strong("Pampas arrangement"),
                                Small("Draft item"),
                                cls="m-product-card",
                            ),
                            cls="m-proof-products",
                        ),
                        cls="m-proof-page",
                    ),
                    cls="m-builder-workspace",
                ),
                cls="m-proof",
            ),
            Div(
                Small("AI builder · Update 03"),
                Strong("Home page refinement"),
                P("Rebalance the hero and surface the studio collection."),
                Div(Span("2 block edits"), Span("Review required")),
                cls="m-agent-panel",
            ),
            Div(
                Small("Commerce go-live"),
                Div(Span(cls="m-check-dot is-ready"), Span("Published pages"), Strong("Ready")),
                Div(Span(cls="m-check-dot is-ready"), Span("Catalog + prices"), Strong("Ready")),
                Div(Span(cls="m-check-dot"), Span("Live provider"), Strong("Review")),
                cls="m-launch-chip",
            ),
            cls="m-proof-stage m-stage",
            role="group",
            aria_label=(
                "Illustrative FastShop builder showing a generated North and Pine storefront with "
                "real product imagery, an AI-prepared update, and a commerce go-live checklist"
            ),
        ),
        Div(
            Span("Generated storefront inside the FastShop builder"),
            Span("Illustrative interface"),
            cls="m-proof-caption",
        ),
    )
def _proof_layer():
    return Section(
        Div(
            Span("Works with", cls="m-proof-label"),
            Div(
                Div(Strong("Stripe"), Small("Sandbox-first checkout")),
                Div(Strong("WooCommerce + WordPress"), Small("REST + WXR")),
                Div(Strong("CSV + Google Merchant Center"), Small("Import + feed export")),
                Div(Strong("FastShop API"), Small("Versioned REST")),
                cls="m-integration-plates",
            ),
            cls="m-integrations m-shell",
        ),
        Div(
            Div(Strong(str(len(BLOCK_TYPES))), Span("supported block families")),
            Div(Strong(str(MAX_PAGES)), Span("maximum generated pages")),
            Div(Strong("2"), Span("required generated menus")),
            Div(Strong(str(len(plans.PLANS))), Span("plan tiers with enforced limits")),
            cls="m-capability-band m-shell",
        ),
        cls="m-proof-layer",
        aria_label="Verified FastShop integrations and capabilities",
    )
def _plan_limit(plan: plans.Plan):
    return Div(
        Strong(plan.name),
        Span(
            f"{plan.sites} sites · {plan.ai_generations_per_month} AI generations / month · "
            f"{plan.products:,} products · {plan.published_sites} published sites"
        ),
    )
def marketing_page():
    free = plans.PLANS["free"]
    basic = plans.PLANS["basic"]
    pro = plans.PLANS["pro"]
    description = (
        "Turn a short business description into a reviewable storefront with a full CMS, "
        "migration tools, and guarded commerce go-live."
    )
    return _document(
        "Build a store from a business brief",
        description,
        A("Skip to content", href="#content", cls="m-skip"),
        Div(
            _header(),
            Main(
                Section(
                    Div(
                        H1("From a short description to a live, checkout-capable store."),
                        P(
                            "FastShop generates a complete draft around your business—pages, "
                            "content, navigation, catalog direction, and design—then gives you "
                            "the builder and commerce controls to review every step before launch.",
                            cls="m-hero-lede",
                        ),
                        Div(
                            A("Create your workspace", href="/signup", cls="m-button"),
                            A("See how it works", href="#how-it-works", cls="m-text-link"),
                            cls="m-hero-actions",
                        ),
                        P(
                            "Create an account now; product publishing stays review-gated.",
                            cls="m-hero-note",
                        ),
                        cls="m-hero-copy",
                    ),
                    _proof_surface(),
                    cls="m-hero m-shell",
                ),
                _proof_layer(),
                Section(
                    Div(
                        H2("A direct path from idea to owned storefront."),
                        P(
                            "Generation starts the work. Your review, content decisions, and "
                            "go-live checks finish it."
                        ),
                        cls="m-section-heading",
                    ),
                    Ol(
                        _workflow_step(
                            "Step 1",
                            "Create your workspace",
                            "Sign up and open a private store workspace with a structured "
                            "starter site ready to edit.",
                            (
                                ("Workspace", "Private"),
                                ("Starting point", "Structured site"),
                                ("Publishing", "Off by default"),
                            ),
                        ),
                        _workflow_step(
                            "Step 2",
                            "Describe the business",
                            "Give FastShop the offer, audience, pages, and tone. The generator "
                            "builds a structured draft site—not a published black box.",
                            (
                                ("Brief", "Offer + audience"),
                                ("Output", "Draft pages"),
                                ("Navigation", "Header + footer"),
                            ),
                        ),
                        _workflow_step(
                            "Step 3",
                            "Refine, review, and go live",
                            "Edit blocks in the visual builder, accept or reject proposed "
                            "changes, then pass publish, domain, and commerce checks.",
                            (
                                ("Changes", "Accept or reject"),
                                ("Launch", "Publish checks"),
                                ("Payments", "Reviewed gate"),
                            ),
                        ),
                        cls="m-steps",
                    ),
                    id="how-it-works",
                    cls="m-section m-shell",
                ),
                Section(
                    Div(
                        H2("Commerce that stays careful when the stakes rise."),
                        P(
                            "Test the complete purchase path before real payments enter the "
                            "picture. Live acceptance remains a deliberate operator-reviewed gate."
                        ),
                        A("Review the commerce workflow", href="#faq", cls="m-text-link m-light-link"),
                        Div(
                            Div(
                                Img(
                                    src="/static/img/merchant-checkout.jpg",
                                    alt="Merchant taking a card payment at a counter terminal",
                                ),
                                Span("Checkout"),
                                cls="m-commerce-photo",
                            ),
                            Div(
                                Img(
                                    src="/static/img/workspace-lounge.jpg",
                                    alt="Operator lounge workspace with seating and laptops",
                                ),
                                Span("Operations"),
                                cls="m-commerce-photo",
                            ),
                            cls="m-commerce-photos",
                            aria_label="Illustrative photography of merchants at work",
                        ),
                        cls="m-dark-intro",
                    ),
                    Div(
                        Article(
                            Span("Checkout", cls="m-feature-tag"),
                            Div(
                                H3("Real Stripe sessions, sandbox first"),
                                P(
                                    "Exercise carts, tax, shipping, and checkout in test mode. "
                                    "Card data stays with the payment provider."
                                ),
                                cls="m-feature-copy",
                            ),
                        ),
                        Article(
                            Span("Go-live", cls="m-feature-tag"),
                            Div(
                                H3("Publication and domain checks before commerce"),
                                P(
                                    "Published content, policies, catalog, prices, domain binding, "
                                    "and provider mode are checked before checkout is enabled."
                                ),
                                cls="m-feature-copy",
                            ),
                        ),
                        Article(
                            Span("Operations", cls="m-feature-tag"),
                            Div(
                                H3("Orders, exact refunds, and revenue"),
                                P(
                                    "Manage fulfillment, confirm idempotent Stripe refunds, and "
                                    "read bounded server-rendered revenue reports in each site's currency."
                                ),
                                cls="m-feature-copy",
                            ),
                        ),
                        cls="m-commerce-list",
                    ),
                    id="commerce",
                    cls="m-section m-commerce",
                ),
                Section(
                    Div(
                        Div(
                            H2("Bring the store you already have."),
                            P(
                                "Every import begins with a dry-run report. You review counts, "
                                "warnings, and mappings before tenant-scoped changes are applied."
                            ),
                            cls="m-section-heading",
                        ),
                        Div(
                            Small("Reviewed import path"),
                            Div(Span("01"), Strong("Preview"), Small("Counts + warnings")),
                            Div(Span("02"), Strong("Review"), Small("Mappings + scope")),
                            Div(Span("03"), Strong("Apply"), Small("Tenant-owned rows")),
                            P("No provider write occurs during import review."),
                            cls="m-migration-evidence",
                        ),
                        cls="m-migration-top",
                    ),
                    Div(
                        Div(
                            Span("Shopify"),
                            Strong("Admin GraphQL"),
                            P("Products, orders, customers, content, and theme direction."),
                        ),
                        Div(
                            Span("WooCommerce"),
                            Strong("REST API"),
                            P("A reviewed migration path for catalog and commerce records."),
                        ),
                        Div(
                            Span("WordPress"),
                            Strong("REST + WXR"),
                            P("Import posts, pages, and media; export portable WXR content."),
                        ),
                        Div(
                            Span("Catalog feeds"),
                            Strong("CSV + Merchant Center"),
                            P("Preview bounded CSV imports and publish a scoped product feed."),
                        ),
                        cls="m-migration-list",
                    ),
                    id="migration",
                    cls="m-section m-shell",
                ),
                Section(
                    Div(
                        H2("A CMS that does not stop at the home page."),
                        P(
                            "Build the storefront and the publishing system behind it in the "
                            "same workspace."
                        ),
                        Div(
                            Span("Block builder"),
                            Span("Menus"),
                            Span("Media library"),
                            Span("Blog"),
                            Span("Snippets"),
                            cls="m-cms-index",
                        ),
                        cls="m-cms-copy",
                    ),
                    Div(
                        Div(Span("Page"), Strong("Journal / Materials"), cls="m-cms-row"),
                        Div(Span("Status"), Strong("Draft with 8 blocks"), cls="m-cms-row"),
                        Div(Span("Navigation"), Strong("Header · Footer"), cls="m-cms-row"),
                        Div(Span("Media"), Strong("Reusable · Alt text"), cls="m-cms-row"),
                        Div(Span("Publishing"), Strong("Review required"), cls="m-cms-row"),
                        Small("Illustrative content workspace"),
                        cls="m-cms-proof",
                    ),
                    id="cms",
                    cls="m-section m-cms m-shell",
                ),
                Section(
                    Div(
                        Div(
                            H2("Simple plans based on sites and AI generations."),
                            P(
                                "Free, Basic, and Pro scale site, AI-generation, product, and "
                                "publishing limits. Start free, then manage paid upgrades in "
                                "Billing when checkout is configured."
                            ),
                            cls="m-pricing-copy",
                        ),
                        Span("No invented prices", cls="m-pricing-note"),
                        cls="m-pricing-head",
                    ),
                    Div(
                        Div(
                            _plan_limit(free),
                            _plan_limit(basic),
                            _plan_limit(pro),
                            cls="m-plan-limits",
                        ),
                        A("Create your workspace", href="/signup", cls="m-button"),
                        cls="m-pricing-body",
                    ),
                    id="pricing",
                    cls="m-pricing m-shell",
                ),
                Section(
                    Div(H2("Questions before you build."), cls="m-section-heading"),
                    Div(
                        Details(
                            Summary("Can FastShop publish a generated site automatically?"),
                            P(
                                "No. Generation creates a private draft. You review the content, "
                                "accept or reject changes, and explicitly complete publication "
                                "and commerce checks."
                            ),
                        ),
                        Details(
                            Summary("Does FastShop support real payments?"),
                            P(
                                "FastShop includes Stripe checkout and a live-provider path, but "
                                "testing is sandbox-first and live credentials require explicit "
                                "operator acceptance."
                            ),
                        ),
                        Details(
                            Summary("What can I migrate?"),
                            P(
                                "The shipped connectors cover Shopify Admin GraphQL, WooCommerce "
                                "REST, WordPress REST and WXR export, plus CSV catalog import and "
                                "Google Merchant Center feed generation."
                            ),
                        ),
                        Details(
                            Summary("Will an import overwrite my store?"),
                            P(
                                "Imports are previewed before application, additive, idempotent, "
                                "and tenant-scoped. Each connector reports warnings and unmapped "
                                "items for review."
                            ),
                        ),
                        Details(
                            Summary("Is signup available today?"),
                            P(
                                "Signup availability is controlled by the public launch gate. "
                                "When open, signup creates your account and first private site."
                            ),
                        ),
                        Details(
                            Summary("How much will FastShop cost?"),
                            P(
                                f"Free includes {free.sites} sites, "
                                f"{free.ai_generations_per_month} AI generations per month, "
                                f"{free.products} products, and {free.published_sites} published "
                                f"sites. Basic raises those limits to {basic.sites}, "
                                f"{basic.ai_generations_per_month}, {basic.products}, and "
                                f"{basic.published_sites}; Pro to {pro.sites}, "
                                f"{pro.ai_generations_per_month}, {pro.products:,}, and "
                                f"{pro.published_sites}. Paid plan checkout is available from ",
                                A("Billing", href="/admin/billing"),
                                " when billing is configured.",
                            ),
                        ),
                        cls="m-faq-list",
                    ),
                    id="faq",
                    cls="m-section m-shell",
                ),
                id="content",
            ),
            _footer(on_marketing=True),
            cls="m-page",
        ),
    )
def _field_error(message: str, error_id: str):
    return P(message, id=error_id, cls="m-field-error", role="alert") if message else None
def login_page(
    csrf: str,
    error: str = "",
    google_enabled: bool = False,
    local_enabled: bool = True,
):
    return _document(
        "Sign in",
        "Sign in to your FastShop merchant workspace.",
        A("Skip to content", href="#content", cls="m-skip"),
        Div(
            Header(
                Div(
                    _brand(),
                    A("Create workspace", href="/signup", cls="m-text-link"),
                    cls="m-nav",
                ),
                cls="m-header",
            ),
            Main(
                Section(
                    Div(
                        H1("Welcome back."),
                        P(
                            "Sign in to manage your storefront, orders, and workspace.",
                            cls="m-auth-intro",
                        ),
                        P(error, cls="m-form-error", role="alert") if error else None,
                        A(
                            "Continue with Google",
                            href="/auth/google",
                            cls="m-auth-google",
                        )
                        if google_enabled
                        else None,
                        Div(Span("or"), cls="m-auth-divider", aria_hidden="true")
                        if google_enabled and local_enabled
                        else None,
                        Form(
                            Input(type="hidden", name="csrf_token", value=csrf),
                            Label(
                                "Email address",
                                Input(
                                    type="email",
                                    name="email",
                                    required=True,
                                    autocomplete="email",
                                ),
                            ),
                            Label(
                                "Password",
                                Input(
                                    type="password",
                                    name="password",
                                    required=True,
                                    autocomplete="current-password",
                                ),
                            ),
                            Button("Sign in", type="submit", cls="m-button"),
                            action="/login",
                            method="post",
                            cls="m-auth-form",
                        )
                        if local_enabled
                        else None,
                        cls="m-auth-card",
                    ),
                    id="content",
                    cls="m-auth m-shell",
                )
            ),
            _footer(),
            cls="m-page m-auth-page",
        ),
        private=True,
    )
def signup_page(
    *,
    signup_open: bool,
    csrf: str = "",
    status: str = "",
    field_errors: dict[str, str] | None = None,
    values: dict[str, str] | None = None,
):
    field_errors = field_errors or {}
    values = values or {}
    description = (
        "Create a private FastShop merchant workspace."
        if signup_open
        else "FastShop public signup is currently closed."
    )
    if not signup_open:
        main_content = Section(
            Div(
                H1("Public signup is currently closed."),
                P(
                    "We are not accepting new self-serve accounts through this page. "
                    "Existing merchants can continue to sign in and work on their sites."
                ),
                Div(
                    A("Sign in", href="/login", cls="m-button"),
                    A("Explore FastShop", href="/marketing/", cls="m-text-link"),
                    cls="m-hero-actions",
                ),
                cls="m-signup-copy",
            ),
            Div(
                H2("What happens when signup opens"),
                Div(P("Create one secure account and a private workspace.")),
                Div(P("Start from a clean, structured storefront draft.")),
                Div(P("Review every page before anything is published.")),
                Small("No information is collected while signup is closed."),
                cls="m-signup-path",
            ),
            id="content",
            cls="m-signup m-shell",
        )
    else:
        generic_error = status in {"unable", "rate"}
        main_content = Section(
            Div(
                H1("Create your workspace."),
                P(
                    "Create an account and a private store workspace in one step."
                ),
                P(
                    "You get a free plan with real limits — no payment details required."
                ),
                A("Already have an account? Sign in", href="/login", cls="m-text-link"),
                cls="m-signup-copy",
            ),
            Div(
                Span("Set up your account", cls="m-signup-label"),
                P(
                    "We could not complete signup with these details. Review your entries "
                    "and try again later. If you may already have an account, ",
                    A("sign in", href="/login"),
                    ".",
                    id="signup-error",
                    cls="m-form-error",
                    role="alert",
                )
                if generic_error
                else None,
                P(
                    "Your session expired. Refresh this page and try again.",
                    cls="m-form-error",
                    role="alert",
                )
                if status == "session"
                else None,
                Form(
                    Input(type="hidden", name="csrf_token", value=csrf),
                    Label(
                        "Your name",
                        Input(
                            name="name",
                            value=values.get("name", ""),
                            required=True,
                            maxlength=signup_services.SIGNUP_NAME_MAX_LENGTH,
                            autocomplete="name",
                            aria_invalid="true" if field_errors.get("name") else None,
                            aria_describedby="name-error" if field_errors.get("name") else None,
                        ),
                    ),
                    _field_error(field_errors.get("name", ""), "name-error"),
                    Label(
                        "Email address",
                        Input(
                            name="email",
                            type="email",
                            value=values.get("email", ""),
                            required=True,
                            maxlength=signup_services.SIGNUP_EMAIL_MAX_LENGTH,
                            autocomplete="email",
                            aria_invalid="true" if field_errors.get("email") else None,
                            aria_describedby="email-error" if field_errors.get("email") else None,
                        ),
                    ),
                    _field_error(field_errors.get("email", ""), "email-error"),
                    Label(
                        "Password",
                        Input(
                            name="password",
                            type="password",
                            required=True,
                            minlength=signup_services.SIGNUP_PASSWORD_MIN_LENGTH,
                            maxlength=signup_services.SIGNUP_PASSWORD_MAX_LENGTH,
                            autocomplete="new-password",
                            aria_invalid="true" if field_errors.get("password") else None,
                            aria_describedby=(
                                "password-help password-error"
                                if field_errors.get("password")
                                else "password-help"
                            ),
                        ),
                    ),
                    _field_error(field_errors.get("password", ""), "password-error"),
                    Small(
                        f"At least {signup_services.SIGNUP_PASSWORD_MIN_LENGTH} characters.",
                        id="password-help",
                    ),
                    Label(
                        "Confirm password",
                        Input(
                            name="password_confirmation",
                            type="password",
                            required=True,
                            minlength=signup_services.SIGNUP_PASSWORD_MIN_LENGTH,
                            maxlength=signup_services.SIGNUP_PASSWORD_MAX_LENGTH,
                            autocomplete="new-password",
                            aria_invalid=(
                                "true" if field_errors.get("password_confirmation") else None
                            ),
                            aria_describedby=(
                                "password-confirmation-error"
                                if field_errors.get("password_confirmation")
                                else None
                            ),
                        ),
                    ),
                    _field_error(
                        field_errors.get("password_confirmation", ""),
                        "password-confirmation-error",
                    ),
                    Button("Create workspace", type="submit", cls="m-button"),
                    method="post",
                    action="/signup",
                    cls="m-signup-form",
                ),
                Div(
                    Span("or", aria_hidden="true"),
                    A(
                        _google_mark(),
                        Span("Continue with Google"),
                        href="/auth/google?signup=1",
                        cls="m-google-link",
                    ),
                    cls="m-signup-google",
                )
                if auth.google_enabled()
                else None,
                Small(
                    "Free plan. No payment details required. We email you a verification link "
                    "you can complete later.",
                    cls="m-signup-note",
                ),
                id="signup-form",
                tabindex="-1",
                cls="m-signup-form-sheet",
            ),
            id="content",
            cls="m-signup m-shell",
        )
    return _document(
        "Create your workspace" if signup_open else "Signup closed",
        description,
        A("Skip to content", href="#content", cls="m-skip"),
        Div(
            Header(Div(_brand(), A("Back to overview", href="/marketing/", cls="m-text-link"), cls="m-nav"), cls="m-header"),
            Main(main_content),
            _footer(show_signup_link=False),
            cls="m-page",
        ),
        private=True,
    )
def verification_page(csrf: str, *, status: str = ""):
    return _document(
        "Verify your email",
        "Confirm a FastShop account email address.",
        A("Skip to content", href="#content", cls="m-skip"),
        Div(
            Header(
                Div(
                    _brand(),
                    A("Back to overview", href="/marketing/", cls="m-text-link"),
                    cls="m-nav",
                ),
                cls="m-header",
            ),
            Main(
                Section(
                    Div(
                        H1("Confirm your email."),
                        P(
                            "Enter the verification code from your email, then select Confirm. "
                            "Opening this page alone does not change your account."
                        ),
                        P(
                            "This verification link is invalid, expired or already used.",
                            cls="m-form-error",
                            role="alert",
                        )
                        if status == "invalid"
                        else None,
                        Form(
                            Input(type="hidden", name="csrf_token", value=csrf),
                            Label(
                                "Verification code",
                                Input(
                                    name="token",
                                    required=True,
                                    maxlength=64,
                                    autocomplete="off",
                                    data_email_token="",
                                ),
                            ),
                            Button("Confirm email", type="submit", cls="m-button"),
                            method="post",
                            cls="m-signup-form",
                        ),
                        cls="m-signup-form-sheet m-verification-sheet",
                    ),
                    id="content",
                    cls="m-signup m-signup-single m-shell",
                )
            ),
            _footer(),
            cls="m-page",
        ),
        private=True,
    )
def verified_page():
    return _document(
        "Email verified",
        "Your FastShop account email is verified.",
        Div(
            Header(Div(_brand(), cls="m-nav"), cls="m-header"),
            Main(
                Section(
                    Div(
                        H1("Your email is verified."),
                        P("You can keep building your private storefront."),
                        A("Open your sites", href="/admin/sites", cls="m-button"),
                        cls="m-signup-copy",
                    ),
                    id="content",
                    cls="m-signup m-signup-single m-shell",
                )
            ),
            _footer(),
            cls="m-page",
        ),
        private=True,
    )
def _client_address(request) -> str:
    return request.client.host if request.client else "unknown"
def _remember_failure(session, *, errors=None, name="", email=""):
    session["signup_errors"] = errors or {}
    session["signup_values"] = {
        "name": str(name)[: signup_services.SIGNUP_NAME_MAX_LENGTH],
        "email": str(email)[: signup_services.SIGNUP_EMAIL_MAX_LENGTH],
    }
def _record_signup_failure(email: str, client_address: str) -> None:
    with SessionLocal() as db:
        signup_services.record_attempt(
            db, "signup", email, client_address, accepted=False
        )
        db.commit()
def register_marketing_routes(rt, csrf_token, require_csrf, establish_session):
    """Register public platform routes without consulting tenant authentication state."""
    @rt("/marketing/", methods=["GET"])
    def get():
        return marketing_page()
    @rt("/signup", methods=["GET"])
    def get(session, status: str = ""):
        errors = session.pop("signup_errors", {})
        values = session.pop("signup_values", {})
        return signup_page(
            signup_open=settings.signup_open,
            csrf=csrf_token(session) if settings.signup_open else "",
            status=status,
            field_errors=errors,
            values=values,
        )
    @rt("/signup", methods=["POST"])
    async def post(session, request):
        if not settings.signup_open:
            return RedirectResponse("/signup?status=closed", status_code=303)
        form = await request.form()
        try:
            require_csrf(session, str(form.get("csrf_token", "")))
        except CommerceError:
            return RedirectResponse("/signup?status=session#signup-form", status_code=303)
        raw_name = str(form.get("name", ""))
        raw_email = str(form.get("email", ""))
        password = str(form.get("password", ""))
        confirmation = str(form.get("password_confirmation", ""))
        address = _client_address(request)
        errors: dict[str, str] = {}
        try:
            name = signup_services.normalized_name(raw_name)
        except CommerceError as exc:
            name = raw_name.strip()
            errors["name"] = str(exc)
        try:
            email = signup_services.normalized_email(raw_email)
        except CommerceError as exc:
            email = raw_email.strip().lower()
            errors["email"] = str(exc)
        try:
            password = signup_services.validate_password(password, confirmation)
        except CommerceError as exc:
            if "confirmation" in str(exc):
                errors["password_confirmation"] = str(exc)
            else:
                errors["password"] = str(exc)
        with SessionLocal() as db:
            limited = signup_services.rate_limited(db, "signup", email, address)
            existing = (
                db.scalar(select(User.id).where(User.email == email))
                if "email" not in errors
                else None
            )
            if limited or existing or errors:
                if not limited:
                    signup_services.record_attempt(
                        db, "signup", email, address, accepted=False
                    )
                db.commit()
                _remember_failure(
                    session,
                    errors={} if existing or limited else errors,
                    name=raw_name,
                    email=raw_email,
                )
                return RedirectResponse("/signup?status=unable#signup-form", status_code=303)
        result = None
        for slug_start in range(1, 4):
            try:
                with SessionLocal() as db:
                    result = signup_services.provision_password_signup(
                        db,
                        name,
                        email,
                        password,
                        address,
                        slug_start=slug_start,
                    )
                    db.commit()
                break
            except IntegrityError:
                continue
            except signup_services.SignupUnavailable:
                break
        if not result:
            _record_signup_failure(email, address)
            _remember_failure(session, name=raw_name, email=raw_email)
            return RedirectResponse("/signup?status=unable#signup-form", status_code=303)
        establish_session(session, result.user, "admin")
        session["csrf_token"] = secrets.token_urlsafe(32)
        if result.message_id:
            dispatch_mail(result.message_id)
        return RedirectResponse(f"/admin/onboarding/{result.site.id}", status_code=303)
    @rt("/signup/verify/{tenant_id}/{verification_id}", methods=["GET"])
    def get(session, tenant_id: str, verification_id: str, status: str = ""):
        return verification_page(csrf_token(session), status=status)
    @rt("/signup/verify/{tenant_id}/{verification_id}", methods=["POST"])
    async def post(session, request, tenant_id: str, verification_id: str):
        form = await request.form()
        try:
            require_csrf(session, str(form.get("csrf_token", "")))
            with SessionLocal() as db:
                signup_services.consume_verification(
                    db, tenant_id, verification_id, str(form.get("token", ""))
                )
                db.commit()
            return RedirectResponse("/signup/verified", status_code=303)
        except CommerceError:
            return RedirectResponse(
                f"/signup/verify/{tenant_id}/{verification_id}?status=invalid",
                status_code=303,
            )
    @rt("/signup/verified", methods=["GET"])
    def get():
        return verified_page()
