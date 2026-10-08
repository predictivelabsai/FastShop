"""Static public SaaS marketing routes, independent of every tenant surface."""

from fasthtml.common import (
    H1,
    H2,
    H3,
    A,
    Article,
    Body,
    Details,
    Div,
    Footer,
    Head,
    Header,
    Html,
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
    Title,
    to_xml,
)
from starlette.responses import HTMLResponse

GITHUB_URL = "https://github.com/predictivelabsai/FastShop"


def _head(title: str, description: str):
    return (
        Title(f"{title} — FastShop"),
        Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Meta(name="description", content=description),
        Meta(name="robots", content="index,follow"),
        Meta(name="theme-color", content="#f4f1e9"),
        Meta(property="og:title", content=f"{title} — FastShop"),
        Meta(property="og:description", content=description),
        Meta(property="og:type", content="website"),
        Link(rel="icon", href="/static/favicon.svg", type="image/svg+xml"),
        Link(rel="stylesheet", href="/static/marketing.css"),
    )


def _document(title: str, description: str, *content):
    markup = to_xml(
        Html(Head(*_head(title, description)), Body(*content), lang="en"),
        indent=True,
    )
    return HTMLResponse("<!doctype html>\n" + markup)


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
            A("Signup status", href="/signup", cls="m-button m-button-small"),
            cls="m-nav",
        ),
        cls="m-header",
    )


def _footer():
    return Footer(
        Div(
            Div(
                _brand(),
                P("Build the storefront. Keep control of the launch."),
                cls="m-footer-intro",
            ),
            Nav(
                A("How it works", href="#how-it-works"),
                A("Features", href="#commerce"),
                A("Pricing preview", href="#pricing"),
                A("FAQ", href="#faq"),
                aria_label="Page links",
            ),
            Nav(
                A("Signup status", href="/signup"),
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


def _workflow_step(label: str, title: str, body: str):
    return Li(
        Span(label, cls="m-step-label"),
        Div(H3(title), P(body)),
    )


def _proof_surface():
    return Div(
        Div(
            Div(
                Span(cls="m-proof-dot"),
                Span(cls="m-proof-dot"),
                Span(cls="m-proof-dot"),
                aria_hidden="true",
                cls="m-proof-dots",
            ),
            Span("Draft / Home"),
            Span("Ready for review", cls="m-proof-status"),
            cls="m-proof-bar",
        ),
        Div(
            Div(
                Span("North & Pine", cls="m-proof-brand"),
                Div(Span("Shop"), Span("Journal"), Span("About"), cls="m-proof-links"),
                cls="m-proof-nav",
            ),
            Div(
                Div(
                    Small("NEW SEASON / DRAFT COPY"),
                    P("Useful objects, made for unhurried homes.", cls="m-proof-title"),
                    Span("Explore the collection", cls="m-proof-cta"),
                    cls="m-proof-copy",
                ),
                Div(
                    Div(Span("Image direction"), Strong("Warm studio still life")),
                    Div(Span("Catalog"), Strong("12 products mapped")),
                    Div(Span("Checkout"), Strong("Sandbox ready")),
                    cls="m-proof-notes",
                ),
                cls="m-proof-hero",
            ),
            Div(
                Div(Span("Pages"), Strong("6")),
                Div(Span("Blocks"), Strong("24")),
                Div(Span("Menus"), Strong("2")),
                cls="m-proof-metrics",
            ),
            cls="m-proof-page",
        ),
        Div(Span("Generated draft"), Span("Illustrative interface"), cls="m-proof-caption"),
        cls="m-proof",
        role="img",
        aria_label=(
            "Illustrative FastShop draft showing generated store copy, pages, blocks, "
            "menus, catalog mapping, and sandbox checkout readiness"
        ),
    )


def marketing_page():
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
                            A("Check signup status", href="/signup", cls="m-button"),
                            A("See how it works", href="#how-it-works", cls="m-text-link"),
                            cls="m-hero-actions",
                        ),
                        P(
                            "Signup opens in Phase 5b. The product workflows shown here are "
                            "already built and review-gated.",
                            cls="m-hero-note",
                        ),
                        cls="m-hero-copy",
                    ),
                    _proof_surface(),
                    cls="m-hero m-shell",
                ),
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
                            "Sign up and open a private store workspace. Phase 5b will replace "
                            "today's placeholder with the real provisioning flow.",
                        ),
                        _workflow_step(
                            "Step 2",
                            "Describe the business",
                            "Give FastShop the offer, audience, pages, and tone. The generator "
                            "builds a structured draft site—not a published black box.",
                        ),
                        _workflow_step(
                            "Step 3",
                            "Refine, review, and go live",
                            "Edit blocks in the visual builder, accept or reject proposed "
                            "changes, then pass publish, domain, and commerce checks.",
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
                        H2("Bring the store you already have."),
                        P(
                            "Every import begins with a dry-run report. You review counts, "
                            "warnings, and mappings before tenant-scoped changes are applied."
                        ),
                        cls="m-section-heading",
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
                        H2("Simple plans based on sites and AI generations."),
                        P(
                            "Real plan details arrive with metering in Phase 5d. For now, join "
                            "the signup path and keep your store-building flow intact."
                        ),
                    ),
                    A("Check signup status", href="/signup", cls="m-button m-button-inverse"),
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
                                "Not yet. The signup page in this release is an honest placeholder. "
                                "Phase 5b adds account creation and first-site provisioning."
                            ),
                        ),
                        Details(
                            Summary("How much will FastShop cost?"),
                            P(
                                "Pricing has not been published. Phase 5d will introduce plans "
                                "based on sites and AI generations, with the real limits and plan "
                                "details shown before billing arrives."
                            ),
                        ),
                        cls="m-faq-list",
                    ),
                    id="faq",
                    cls="m-section m-shell",
                ),
                id="content",
            ),
            _footer(),
            cls="m-page",
        ),
    )


def signup_page():
    description = "FastShop signup is launching in Phase 5b. No information is collected yet."
    return _document(
        "Signup is launching",
        description,
        A("Skip to content", href="#content", cls="m-skip"),
        Div(
            Header(Div(_brand(), A("Back to overview", href="/marketing/", cls="m-text-link"), cls="m-nav"), cls="m-header"),
            Main(
                Section(
                    Div(
                        Span("Phase 5b", cls="m-launch-label"),
                        H1("Sign up is launching."),
                        P(
                            "This page is holding the route for FastShop's self-serve account "
                            "flow. It does not collect your email or create an account yet."
                        ),
                        P(
                            "Leave your store-building flow intact: explore what FastShop already "
                            "does, then return here when provisioning opens."
                        ),
                        Div(
                            A("Explore FastShop", href="/marketing/", cls="m-button"),
                            A(
                                "Read the documentation",
                                href=GITHUB_URL,
                                target="_blank",
                                rel="noopener noreferrer",
                                cls="m-text-link",
                            ),
                            cls="m-hero-actions",
                        ),
                        cls="m-signup-copy",
                    ),
                    Div(
                        Div(Span("1"), P("Create an account")),
                        Div(Span("2"), P("Describe the business")),
                        Div(Span("3"), P("Open the generated draft")),
                        Small("The complete onboarding path arrives in Phase 5b–5c."),
                        cls="m-signup-path",
                    ),
                    id="content",
                    cls="m-signup m-shell",
                ),
            ),
            _footer(),
            cls="m-page",
        ),
    )


def register_marketing_routes(rt):
    """Register static platform routes without tenant, session, or database dependencies."""

    @rt("/marketing/")
    def get():
        return marketing_page()

    @rt("/signup")
    def get():
        return signup_page()
