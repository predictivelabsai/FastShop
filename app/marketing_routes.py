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
    Title,
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
        Link(rel="stylesheet", href="/static/fonts.css"),
        Link(rel="stylesheet", href="/static/marketing.css"),
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
            A("Create workspace", href="/signup", cls="m-button m-button-small"),
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
                A("Create workspace", href="/signup"),
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
                            "Free, Basic, and Pro scale site, AI-generation, product, and "
                            "publishing limits. Start free, then manage paid upgrades in "
                            "Billing when checkout is configured."
                        ),
                    ),
                    A("Create your workspace", href="/signup", cls="m-button m-button-inverse"),
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
            _footer(),
            cls="m-page",
        ),
    )


def _field_error(message: str, error_id: str):
    return P(message, id=error_id, cls="m-field-error", role="alert") if message else None


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
                H1("Create your FastShop workspace."),
                P(
                    "Your account opens with a private, structured storefront draft. "
                    "You can edit every page before publication."
                ),
                P(
                    "Email verification does not block building, and checkout stays disabled "
                    "until the separate go-live checks are complete."
                ),
                A("Already have an account? Sign in", href="/login", cls="m-text-link"),
                cls="m-signup-copy",
            ),
            Div(
                H2("Set up your account"),
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
                    Small(
                        f"Use {signup_services.SIGNUP_PASSWORD_MIN_LENGTH}–"
                        f"{signup_services.SIGNUP_PASSWORD_MAX_LENGTH} characters.",
                        id="password-help",
                    ),
                    Button("Create workspace", type="submit", cls="m-button"),
                    method="post",
                    action="/signup",
                    cls="m-signup-form",
                ),
                Div(
                    Span("or", aria_hidden="true"),
                    A(
                        "Continue with Google",
                        href="/auth/google?signup=1",
                        cls="m-google-link",
                    ),
                    cls="m-signup-google",
                )
                if auth.google_enabled()
                else None,
                Small(
                    "Submitting creates a private workspace. Public-launch terms and consent "
                    "will be presented separately when their reviewed copy is ready."
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
            _footer(),
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
