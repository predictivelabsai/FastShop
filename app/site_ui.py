"""Editorial storefront rendering from tenant-owned published content."""

from __future__ import annotations

from functools import partial

from fasthtml.common import (
    H1,
    H2,
    H3,
    A,
    Article,
    Button,
    Details,
    Div,
    Figcaption,
    Figure,
    Footer,
    Form,
    Header,
    Img,
    Input,
    Label,
    Link,
    Main,
    Meta,
    Nav,
    Option,
    P,
    Script,
    Section,
    Select,
    Small,
    Source,
    Span,
    Strong,
    Summary,
    Textarea,
    Title,
    Video,
)
from sqlalchemy import select

from app.content import catalog_product, site_pages
from app.models import Category, Product, ProductVariant, SiteMedia, VariantChannelListing
from app.services import money


def paragraphs(value):
    return [P(part.strip()) for part in str(value or "").split("\n\n") if part.strip()]


def image(value, alt="Editorial placeholder photography", eager=False):
    return Img(src=value, alt=alt, loading="eager" if eager else "lazy", decoding="async") if value else None


def owned_image(db, site, value, alt="Editorial placeholder photography", eager=False):
    if value and value.startswith(f"/site-media/{site.id}/"):
        media = db.scalar(select(SiteMedia).where(SiteMedia.id == value.rsplit("/", 1)[-1],
            SiteMedia.site_id == site.id, SiteMedia.tenant_id == site.tenant_id))
        if media:
            alt = media.alt
    return image(value, alt, eager)


def url(base, path):
    return base + ("/" if path == "/" else path) if path.startswith("/") else path


def cta(section, base):
    return A(section.get("button", "Explore"), Span("↗", aria_hidden="true"),
             href=url(base, section["link"]), cls="h-button") if section.get("link") else None


def product_cards(db, site, base, category=""):
    image = partial(owned_image, db, site)
    query = select(Product).where(Product.tenant_id == site.tenant_id, Product.is_published.is_(True))
    if category:
        query = query.join(Category).where(Category.tenant_id == site.tenant_id, Category.slug == category)
    result = []
    published_paths = {page.path for page in site_pages(db, site) if page.published_json}
    for product in db.scalars(query.order_by(Product.created_at)):
        if f"/products/{product.slug}" not in published_paths:
            continue
        listing = db.scalar(select(VariantChannelListing).join(ProductVariant).where(
            ProductVariant.product_id == product.id, ProductVariant.tenant_id == site.tenant_id,
            VariantChannelListing.channel_id == site.channel_id,
        ).order_by(ProductVariant.sort_order))
        result.append(Article(A(
            Div(image(product.image_url, f"{product.name} — placeholder product mock-up"), Span("PLACEHOLDER IMAGE", cls="h-image-label"), cls="h-product-image"),
            Div(H3(product.name), Span(money(listing.price_minor, listing.currency) + " · draft price" if listing else "Price to be confirmed"), cls="h-card-caption"),
            P(product.subtitle),
            href=url(base, f"/products/{product.slug}")), cls="h-product-card"))
    return Div(*result, cls="h-products") if result else P("Your collection is coming soon.")


def render_section(db, site, page, config, section, base, csrf, preview=False, first=False):
    image = partial(owned_image, db, site)
    kind = section["type"]
    heading = section.get("heading", "")
    heading_tag = H1 if first and page.kind != "article" and kind not in {"hero", "product"} else H2
    intro = Div(Small(section.get("eyebrow", ""), cls="h-eyebrow"), heading_tag(heading) if heading else None,
                *paragraphs(section.get("body")), cls="h-section-intro")
    if kind == "hero":
        media = Video(Source(src=section["mobile_video"], type="video/mp4", media="(max-width: 600px)") if section.get("mobile_video") else None,
                      Source(src=section["video"], type="video/mp4"), autoplay=True, muted=True, loop=True,
                      playsinline=True, poster=section.get("image", ""), aria_label="Water and bubbles — placeholder video") if section.get("video") else image(section.get("image"), eager=True)
        return Section(Div(media, cls="h-hero-media"), Div(Small(section.get("eyebrow", ""), cls="h-eyebrow"),
            H1(heading), *paragraphs(section.get("body")), cta(section, base), cls="h-hero-copy"),
            Button("Pause motion", type="button", data_video_toggle="", cls="h-video-toggle") if section.get("video") else None,
            Span("PLACEHOLDER MEDIA", cls="h-image-label"), cls="h-hero")
    if kind == "products":
        return Section(intro, product_cards(db, site, base, section.get("category", "")), cls="h-section h-container")
    if kind == "split":
        media = Video(Source(src=section["video"], type="video/mp4"), controls=True, muted=True, playsinline=True,
                      preload="none", poster=section.get("image", ""), aria_label="Placeholder motion study") if section.get("video") else image(section.get("image"))
        return Section(Div(media, Span("PLACEHOLDER MEDIA", cls="h-image-label"), cls="h-split-media"),
            Div(intro, cta(section, base), cls="h-split-copy"), cls="h-split h-section")
    if kind == "facts":
        return Section(intro, Div(*[Div(Strong(item["value"]), P(item["label"])) for item in config.get("facts", [])], cls="h-facts"),
            Small("Source: ", A("hydrogenclinicalresearch.com", href=config.get("science_source", "https://hydrogenclinicalresearch.com/"), target="_blank", rel="noopener noreferrer"),
                  " — figures as of " + config.get("science_date", "date to be confirmed")), cls="h-section h-facts-section")
    if kind == "claims":
        approved = [item for item in config.get("claims", []) if item.get("approved")]
        return Section(intro, *[P(item["text"]) for item in approved],
                       P(config.get("footer", ""), cls="h-disclaimer") if approved else P("Our benefit statements are awaiting review. Explore the original research on our Science page."), cls="h-section h-container")
    if kind == "reviews":
        return Section(Small("ROOM FOR YOUR EXPERIENCE", cls="h-eyebrow"), H2(heading),
            *paragraphs(section.get("body")), Span("SAMPLE SECTION · NO CUSTOMER TESTIMONIALS", cls="h-tag"), cls="h-section h-reviews")
    if kind == "articles":
        articles = [p for p in site_pages(db, site) if p.kind == "article" and (preview or p.published_json)]
        articles.sort(key=lambda p: ((p.published_json or {}).get("published_at") or p.created_at.isoformat(), p.id), reverse=True)
        return Section(intro, Div(*[Article(A(image((p.draft_json if preview else p.published_json).get("image")),
            Small((p.draft_json if preview else p.published_json).get("category", "LEARN"), cls="h-eyebrow"),
            H3((p.draft_json if preview else p.published_json)["title"]), Span("Read the story ↗"), href=url(base, p.path))) for p in articles[:3]], cls="h-articles"), cls="h-section h-container")
    if kind in {"research", "references"}:
        return Section(intro, Div(*[Button(label, type="button", data_research_filter=label, cls="h-filter", aria_pressed=str(label == "All").lower()) for label in ["All", "Exercise", "Reviews", "Meta-analyses"]], cls="h-filters") if kind == "research" else None,
            Div(*[Article(Small(item.get("theme", "SOURCE"), cls="h-eyebrow"), H3(item.get("heading", "Study")),
                P(item.get("body", "")), A("Read original source ↗", href=item["url"], target="_blank", rel="noopener noreferrer"),
                data_research_theme=item.get("theme", "")) for item in section.get("items", [])], cls="h-research"), cls="h-section h-container")
    if kind == "faq":
        return Section(intro, Div(*[Details(Summary(item["heading"]), *paragraphs(item.get("body"))) for item in section.get("items", [])], cls="h-faq"), cls="h-section h-container")
    if kind == "team":
        return Section(intro, Div(*[Article(image(item["image"], item["heading"]) if item.get("image") else Div(Span("PHOTO PLACEHOLDER"), cls="h-person-placeholder"), H3(item["heading"]),
            P(item.get("body", "")), A("Learn more ↗", href=item["url"], target="_blank", rel="noopener noreferrer") if item.get("url") else None) for item in section.get("items", [])], cls="h-team"), cls="h-section h-container")
    if kind == "contact":
        return Section(Div(H2("Say hello."), A(config.get("email", ""), href="mailto:" + config.get("email", "")),
            P(config.get("company", site.name)), P(config.get("address", "")), cls="h-contact-info"),
            Form(Input(type="hidden", name="csrf_token", value=csrf),
                 Label("Your name", Input(name="name", required=True, maxlength=160, autocomplete="name")),
                 Label("Email address", Input(name="email", type="email", required=True, maxlength=320, autocomplete="email")),
                 Label("What's on your mind?", Textarea(name="message", required=True, minlength=10, maxlength=5000, rows=6)),
                 Div(Label("Leave this empty", Input(name="website", tabindex="-1", autocomplete="off")), cls="h-honeypot", aria_hidden="true"),
                 P("We'll use your details to answer this message. This does not sign you up for marketing emails. ", A("Privacy policy", href=url(base, "/pages/privacy-policy"))),
                 Button("Send message ↗", type="submit", cls="h-button"), method="post", action=base + "/contact", cls="h-contact-form"), cls="h-section h-container h-contact")
    if kind == "product":
        from app.commerce import settings_for
        commerce_config = settings_for(db, site)
        checkout_enabled = bool(commerce_config and commerce_config.mode == "sandbox" and not preview)
        product = catalog_product(db, site, page.product_id) if page.product_id else None
        variants = list(db.scalars(select(ProductVariant).where(ProductVariant.product_id == product.id, ProductVariant.tenant_id == site.tenant_id).order_by(ProductVariant.sort_order))) if product else []
        listing = db.scalar(select(VariantChannelListing).where(VariantChannelListing.channel_id == site.channel_id, VariantChannelListing.variant_id == variants[0].id)) if variants else None
        tablets = section.get("subscription_preview", False)
        subscription_enabled = bool(checkout_enabled and product and product.id in commerce_config.subscription_product_ids_json)
        gallery = [section.get("image", ""), *section.get("gallery", [])]
        return Section(Figure(Div(image(section.get("image"), "Placeholder product mock-up", eager=True), data_gallery_main=""),
            Div(*[Button(image(src, f"Placeholder gallery image {i + 1}"), type="button", data_gallery_src=src, aria_label=f"View gallery image {i + 1}") for i, src in enumerate(gallery) if src], cls="h-gallery-thumbs"),
            Figcaption("PLACEHOLDER PRODUCT IMAGES")),
            Div(Small(product.name if product else "PRODUCT", cls="h-eyebrow"), H1(heading), *paragraphs(section.get("body")),
                P(money(listing.price_minor, listing.currency) + (" / box · one-time price" if subscription_enabled else " / box") if listing else "US price to be confirmed", cls="h-price"),
                Small("PLACEHOLDER PRICE · final price and pack size pending"),
                Form(Input(type="hidden", name="csrf_token", value=csrf),
                    Label("Choose your option", Select(*[Option(v.name, value=v.id) for v in variants if v.is_active], name="variant_id", aria_label="Choose your option", required=True)),
                    Label("Quantity", Input(type="number", name="quantity", value=1, min=1, max=25, required=True)),
                    Label("Purchase option", Select(Option("One-time purchase", value="off", selected=True),
                        Option("Subscribe and save 10% · monthly", value="on"), name="subscription")) if subscription_enabled else None,
                    P("USD · Sandbox checkout. Shipping and state-specific sales tax are calculated before payment."),
                    P("Monthly delivery at 10% off merchandise. Skip, pause or cancel future deliveries in My account.") if subscription_enabled else None,
                    Button("Add to bag", cls="h-button"), method="post", action=base + "/cart/add", cls="h-contact-form") if checkout_enabled and listing else None,
                Label("Choose your option", Select(*[Option(v.name, value=v.id) for v in variants], aria_label="Choose your option")) if len(variants) > 1 and not checkout_enabled else None,
                Div(Label(Input(type="radio", name="purchase", checked=True), " One-time purchase"),
                    Label(Input(type="radio", name="purchase"), " Subscribe and save 10% · monthly"), cls="h-purchase") if tablets and not checkout_enabled else None,
                Button("Add to cart — coming in Phase 2", disabled=True, cls="h-button") if not checkout_enabled else None,
                P("Sandbox only. No live payments." if checkout_enabled else "Design preview. Orders and subscriptions are not open yet.", cls="h-muted"), cls="h-product-copy"), cls="h-product-detail h-container")
    return Section(intro, cta(section, base), cls="h-section h-container h-editorial")


def storefront(db, site, page, base, csrf, canonical, *, preview=False, message=""):
    from app.commerce import settings_for
    from app.customer_services import consent_text
    from app.site_analytics import measurement_id
    from app.site_theme import theme_style
    analytics_id = measurement_id(site, preview=preview)
    commerce_config = settings_for(db, site)
    customer_services_enabled = bool(commerce_config and commerce_config.mode == "sandbox")
    image = partial(owned_image, db, site)
    config = site.settings_json if preview else site.published_settings_json
    document = page.draft_json if preview else page.published_json
    home = page.path == "/"
    def section_view(section, index):
        rendered = render_section(db, site, page, config, section, base, csrf, preview, index == 0)
        if preview and rendered is not None:
            rendered.attrs["data-builder-section"] = section["id"]
            rendered.attrs["data-builder-label"] = (section.get("heading") or section["type"])[:100]
        return rendered
    def brand(light=False):
        return image(config.get("logo_light" if light else "logo"), config.get("name", site.name), eager=True) or Span(config.get("name", site.name))
    return (
        Title(f"{document['title']} — {site.name}"), Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Meta(name="description", content=document.get("description", "")), Meta(name="robots", content="noindex,nofollow" if site.status != "published" or preview else "index,follow"),
        Meta(property="og:title", content=document["title"]), Meta(property="og:description", content=document.get("description", "")),
        Meta(property="og:url", content=canonical), Link(rel="canonical", href=canonical),
        Link(rel="icon", href="/static/favicon.svg", type="image/svg+xml"), Link(rel="stylesheet", href="/static/site-builder.css"), Script(src="/static/site-builder.js", defer=True),
        Script(src="/static/site-analytics.js", defer=True) if analytics_id else None,
        Link(rel="stylesheet", href="/static/site-theme.css"),
        Script(src="/static/site-preview.js", defer=True) if preview else None,
        Link(rel="stylesheet", href="/static/site-preview.css") if preview else None,
        Link(rel="stylesheet", href="/static/cart-drawer.css") if customer_services_enabled and not preview else None,
        Script(src="/static/cart-drawer.js", defer=True) if customer_services_enabled and not preview else None,
        Div(
            A("Skip to content", href="#content", cls="h-skip"),
            Div("SANDBOX · Test purchases only" if customer_services_enabled and not preview else "PHASE 1 PREVIEW · Design & content · Purchasing opens after review", cls="h-preview-note") if site.status != "published" or preview or customer_services_enabled else None,
            Div(config.get("announcement", ""), cls="h-announcement"),
            Header(A(brand(), href=base + "/", cls="h-brand h-brand-dark"), A(brand(True), href=base + "/", cls="h-brand h-brand-light"),
                Button("Menu", type="button", data_menu_toggle="", aria_expanded="false", aria_controls="site-navigation", cls="h-menu-toggle"),
                Nav(*[Details(Summary("Shop"), Div(A("All products", href=url(base, "/shop")), *[A(category["label"], href=url(base, category["path"])) for category in config.get("collections", [])], cls="h-dropdown"), cls="h-shop-menu") if item["label"] == "Shop" and config.get("collections") else A(item["label"], href=url(base, item["path"])) for item in config.get("navigation", [])], id="site-navigation", cls="h-nav"),
                Div(A("Account", href=base + "/account", aria_label="My account") if customer_services_enabled else Button("Account", type="button", data_commerce_notice="", aria_label="My account — Phase 2"), A("Bag", href=base + "/cart", data_cart_open="") if customer_services_enabled else Button("Bag (0)", type="button", data_commerce_notice="", aria_label="Cart, zero items — Phase 2"), cls="h-header-actions"), cls="h-header"),
            Div(message, role="status", cls="h-message") if message else None,
            Main(Div(Small(document.get("category", "LEARN"), cls="h-eyebrow"), H1(document["title"]), P(f"By {site.name} team · Draft for editorial review"), image(document.get("image")), cls="h-article-heading h-container") if page.kind == "article" else None,
                *[section_view(s, i) for i, s in enumerate(document.get("sections", [])) if not s.get("hidden")], id="content"),
            Section(Div(Small("A LITTLE SOMETHING TO LOOK FORWARD TO", cls="h-eyebrow"), H2(config.get("offer", "Stay curious.")), P("Our first-order offer is coming when the shop opens.")),
                    Button("Preview the offer", type="button", data_offer_open="", cls="h-button"), cls="h-offer"),
            Footer(Div(Div(A(brand(), href=base + "/", cls="h-brand"), P(config.get("tagline", ""))),
                Div(H3("Explore"), *[A(item["label"], href=url(base, item["path"])) for item in config.get("navigation", [])]),
                Div(H3("Here to help"), *[A(label, href=url(base, "/pages/" + slug)) for slug, label in [("terms-and-conditions", "Terms and Conditions"), ("privacy-policy", "Privacy Policy"), ("faq", "FAQ"), ("returns-and-refunds", "Returns and Refunds")]]),
                Div(H3(config.get("company", site.name)), P(config.get("address", "")), A(config.get("email", ""), href="mailto:" + config.get("email", "")),
                    *[A(s["label"], href=s.get("url") or "#", aria_label=s["label"] + (" — placeholder link" if not s.get("url") else "")) for s in config.get("socials", [])]), cls="h-footer-grid"),
                P(config.get("footer", ""), cls="h-disclaimer"), Div(Span("© 2026 " + config.get("company", site.name)), Span("Visa · Mastercard · PayPal · Apple Pay · Google Pay — planned"), Button("Cookie preferences", type="button", data_cookie_open=""), cls="h-footer-bottom"), cls="h-footer"),
            Div(H2("Your privacy, your choice."), P("Essential storage keeps this site working. Analytics and marketing are off unless you choose them."),
                Details(Summary("Preferences"), Label(Input(type="checkbox", checked=True, disabled=True), " Essential (always on)"), Label(Input(type="checkbox", id="consent-analytics"), " Analytics"), Label(Input(type="checkbox", id="consent-marketing"), " Marketing")),
                Div(Button("Accept all", data_consent="all"), Button("Decline", data_consent="none"), Button("Save preferences", data_consent="custom")),
                A("Privacy policy", href=url(base, "/pages/privacy-policy")), id="h-cookie", cls="h-cookie", role="region", aria_label="Cookie preferences", hidden=True),
            Div(Button("Close ×", type="button", data_offer_close=""), H2(config.get("offer", "Stay curious.")),
                Form(Input(type="hidden", name="csrf_token", value=csrf),
                    P("Confirm your email to receive a personal first-order code. Checkout is still in sandbox setup."),
                    Label("Email address", Input(name="email", type="email", required=True, maxlength=320, autocomplete="email")),
                    Label(Input(name="marketing_consent", type="checkbox", required=True), " " + consent_text(site)),
                    Input(name="website", tabindex="-1", autocomplete="off", style="display:none"),
                    Button("Email my confirmation link", type="submit", cls="h-button"), method="post", action=base + "/newsletter") if customer_services_enabled else Div(
                    P("Design preview: email signup and discount delivery open in Phase 2. No email is collected here."),
                    Label("Email address", Input(type="email", placeholder="you@example.com", disabled=True)),
                    Label(Input(type="checkbox", disabled=True), " " + consent_text(site))),
                A("Privacy policy", href=url(base, "/pages/privacy-policy")), id="h-offer-panel", cls="h-offer-panel", hidden=True),
            Div(id="h-toast", role="status", cls="h-toast", hidden=True),
            cls="h-site " + ("h-home" if home else "h-inner"), data_site=site.id,
            data_ga4=analytics_id, data_analytics_path=base or "/",
            style=theme_style(config), data_themed="true" if theme_style(config) else "false",
            data_builder_page=page.id if preview else "",
        ),
    )
