"""Editorial storefront rendering from tenant-owned published content."""

from __future__ import annotations

import base64
import re
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
    Iframe,
    Img,
    Input,
    Label,
    Link,
    Main,
    Meta,
    Nav,
    NotStr,
    Option,
    P,
    Script,
    Section,
    Select,
    Small,
    Source,
    Span,
    Strong,
    Style,
    Summary,
    Textarea,
    Title,
    Video,
)
from sqlalchemy import String, cast, select

from app.content import catalog_product, site_pages
from app.models import Category, Product, ProductVariant, SiteMedia, SitePage, VariantChannelListing
from app.services import money
from app.site_blocks import default_locale, resolve_document
from app.site_widgets import (
    WIDGET_CSS,
    WIDGETS,
    decorate_block,
    needs_pack1_css,
    needs_pack2_css,
    needs_pack2_js,
    needs_pack3_css,
    needs_pack3_js,
    needs_pack4_css,
    needs_pack4_js,
    needs_widget_css,
    needs_widget_js,
    needs_widgets,
    render_widget,
    rich_content,
    strip_editor_hooks,
)
from app.ui import static_asset


def label(config, key):
    from app.site_generic_shell import DEFAULTS

    return (config or {}).get("generic_labels", {}).get(key, DEFAULTS[key])


def paragraphs(value, *, preview=False, field=""):
    from app.rich_text import sanitise_rich_text
    safe = sanitise_rich_text(str(value or ""))
    attrs = {"data_field": field, "data_field_type": "richtext"} if preview and field else {}
    if attrs:
        return [rich_content(safe, **attrs)]
    if re.search(r"<(?:p|br|h[2-4]|strong|b|em|i|u|a|ul|ol|li|blockquote|hr)(?:\s|>)", safe):
        return [rich_content(safe)]
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


def cta(section, base, config):
    return A(section.get("button", label(config, "cta")), Span("↗", aria_hidden="true"),
             href=url(base, section["link"]), cls="h-button") if section.get("link") else None


def _research_preview_marker(preview, section, index, field):
    return {"data_block_id": section.get("id"), "data_field": f"items.{index}.{field}"} if preview else {}


# Recognisable payment-method chips (brief §4.3). Brand-neutral SVG badges carrying the
# label, so they read as icons rather than a run-on line of text.
_PAYMENT_METHODS = ["Visa", "Mastercard", "PayPal", "Apple Pay", "Google Pay"]

_SOCIAL_GLYPHS = {
    "instagram": '<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r="1.2"/>',
    "facebook": '<path d="M13.5 21v-8h2.5l.4-3h-2.9V8.2c0-.9.3-1.5 1.6-1.5H16.5V4.1C16.1 4 15.1 4 14 4c-2.3 0-3.9 1.4-3.9 4v2H7.5v3h2.6v8z"/>',
}


def _payment_chip(label):
    return Span(label, cls="h-pay-chip", role="img", aria_label=label + " accepted")


def payment_methods():
    return Div(*[_payment_chip(label) for label in _PAYMENT_METHODS], cls="h-pay-methods",
               aria_label="Accepted payment methods (planned)")


def stars(rating):
    full = int(round(rating))
    return Span("★" * full + "☆" * (5 - full), cls="h-stars", aria_label=f"{rating} out of 5")


def review_card(review):
    return Div(stars(review.rating), H3(review.title), *paragraphs(review.body),
               Span(f"— {review.author_name}", cls="h-review-meta"), cls="h-review-card")


def social_link(social):
    glyph = _SOCIAL_GLYPHS.get(str(social.get("label", "")).lower())
    icon = NotStr(f'<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6">{glyph}</svg>') if glyph else Span(social["label"][:1])
    return A(icon, Span(social["label"], cls="h-visually-hidden"),
             href=social.get("url") or "#", cls="h-social-icon",
             aria_label=social["label"] + (" — placeholder link" if not social.get("url") else ""),
             **({"target": "_blank", "rel": "noopener noreferrer"} if social.get("url") else {}))


def _snippet_node(item, placement, *, head=False):
    if item["consent"] == "none":
        node = NotStr(item["content"])
    else:
        encoded = base64.b64encode(item["content"].encode("utf-8")).decode("ascii")
        node = NotStr(
            f'<template data-fastshop-snippet="{placement}" data-consent="{item["consent"]}" '
            f'data-content="{encoded}"></template>'
        )
    if head:
        # FastHTML moves objects with a head-tag marker into the generated document head.
        node.tag = "meta"
    return node


def product_cards(db, site, base, config, category=""):
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
            Div(image(product.image_url, f"{product.name}: {label(config, 'card_product_alt')}"), Span(label(config, "placeholder_image"), cls="h-image-label"), cls="h-product-image"),
            Div(H3(product.name), Span(money(listing.price_minor, listing.currency) + label(config, "draft_price_suffix") if listing else label(config, "card_price_confirm")), cls="h-card-caption"),
            P(product.subtitle),
            href=url(base, f"/products/{product.slug}")), cls="h-product-card"))
    return Div(*result, cls="h-products") if result else P(label(config, "collection_empty"))


def render_section(db, site, page, config, section, base, csrf, preview=False, first=False, *, blog_articles=None):
    node = render_widget(db, site, page, config, section, base, csrf, preview,
                         lambda child: render_section(db, site, page, config, child, base, csrf, preview))
    if node is not None or section["type"] in WIDGETS:
        return node
    return decorate_block(_render_section(db, site, page, config, section, base, csrf, preview, first, blog_articles=blog_articles), section, preview)


def _render_section(db, site, page, config, section, base, csrf, preview=False, first=False, *, blog_articles=None):
    image = partial(owned_image, db, site)
    kind = section["type"]
    heading = section.get("heading", "")
    heading_tag = H1 if first and page.kind != "article" and kind not in {"hero", "product"} else H2
    intro = Div(Small(section.get("eyebrow", ""), cls="h-eyebrow"), heading_tag(heading) if heading else None,
                *paragraphs(section.get("body"), preview=preview, field="body"), cls="h-section-intro")
    if kind == "hero":
        media = Video(Source(src=section["mobile_video"], type="video/mp4", media="(max-width: 600px)") if section.get("mobile_video") else None,
                      Source(src=section["video"], type="video/mp4"), autoplay=True, muted=True, loop=True,
                      playsinline=True, poster=section.get("image", ""), aria_label=label(config, "hero_video_alt")) if section.get("video") else image(section.get("image"), label(config, "editorial_image_alt"), eager=True)
        return Section(Div(media, cls="h-hero-media"), Div(Small(section.get("eyebrow", ""), cls="h-eyebrow"),
            H1(heading), *paragraphs(section.get("body"), preview=preview, field="body"),
            cta(section, base, config), cls="h-hero-copy"),
            Button(label(config, "video_toggle"), type="button", data_video_toggle="", cls="h-video-toggle") if section.get("video") else None,
            Span(label(config, "placeholder_media"), cls="h-image-label"), cls="h-hero")
    if kind == "products":
        return Section(intro, product_cards(db, site, base, config, section.get("category", "")), cls="h-section h-container")
    if kind == "split":
        media = Video(Source(src=section["video"], type="video/mp4"), controls=True, muted=True, playsinline=True,
                      preload="none", poster=section.get("image", ""), aria_label=label(config, "split_video_alt")) if section.get("video") else image(section.get("image"), label(config, "editorial_image_alt"))
        return Section(Div(media, Span(label(config, "placeholder_media"), cls="h-image-label"), cls="h-split-media"),
            Div(intro, cta(section, base, config), cls="h-split-copy"), cls="h-split h-section")
    if kind == "facts":
        return Section(intro, Div(*[Div(Strong(item["value"]), P(item["label"])) for item in config.get("facts", [])], cls="h-facts"),
            Small(label(config, "facts_source"), A(label(config, "facts_source_name"), href=config.get("science_source", ""), target="_blank", rel="noopener noreferrer"),
                  label(config, "facts_date_prefix") + config.get("science_date", label(config, "facts_date"))), cls="h-section h-facts-section")
    if kind == "claims":
        approved = [item for item in config.get("claims", []) if item.get("approved")]
        return Section(intro, *[P(item["text"]) for item in approved],
                rich_content(config.get("footer", ""), cls="h-disclaimer") if approved else P(label(config, "claims_pending")), cls="h-section h-container")
    if kind == "reviews":
        from app.site_reviews import approved_reviews, review_summary
        product_id = page.product_id if page.kind == "product" else None
        reviews = approved_reviews(db, site, product_id)
        if reviews:
            count, average = review_summary(db, site, product_id)
            return Section(Small(label(config, "reviews_eyebrow"), cls="h-eyebrow"), H2(heading),
                P(f"{average} average from {count} approved review" + ("s" if count != 1 else "")),
                Div(*[review_card(r) for r in reviews], cls="h-reviews-list"), cls="h-section h-reviews")
        return Section(Small(label(config, "reviews_empty_eyebrow"), cls="h-eyebrow"), H2(heading),
            *paragraphs(section.get("body"), preview=preview, field="body"),
            Span(label(config, "reviews_sample"), cls="h-tag"), cls="h-section h-reviews")
    if kind == "articles":
        from app.site_blog import articles_for, metadata
        articles = articles_for(db, site, preview=preview)[:3] if blog_articles is None else blog_articles
        cards = []
        for p in articles:
            card = Article(A(image(resolve_document(p.draft_json if preview else p.published_json, default_locale(site, preview=preview)).get("image"), label(config, "editorial_image_alt")),
            Small(resolve_document(p.draft_json if preview else p.published_json, default_locale(site, preview=preview)).get("category", "LEARN"), cls="h-eyebrow"),
            H3(resolve_document(p.draft_json if preview else p.published_json, default_locale(site, preview=preview))["title"]), Span(label(config, "article_read")), href=url(base, p.path)))
            author = metadata(p.draft_json if preview else p.published_json)["author_name"] or f"{site.name} team"
            cards.append(Div(card, P("By " + author, cls="h-blog-byline")) if blog_articles is not None else card)
        return Section(intro, Div(*cards, cls="h-articles"), cls="h-section h-container")
    if kind in {"research", "references"}:
        research_items = []
        for index, item in enumerate(section.get("items", [])):
            research_items.append(Article(
                Small(item.get("theme", label(config, "research_source")), cls="h-eyebrow",
                      **_research_preview_marker(preview, section, index, "theme")),
                H3(item.get("heading", label(config, "research_study")),
                   **_research_preview_marker(preview, section, index, "heading")),
            rich_content(item.get("body", ""), **_research_preview_marker(preview, section, index, "body")),
                A(label(config, "research_read"), href=item["url"], target="_blank", rel="noopener noreferrer"),
                data_research_theme=item.get("theme", "")))
        return Section(intro, Div(*[Button(label(config, key), type="button", data_research_filter=label(config, key), cls="h-filter", aria_pressed=str(index == 0).lower()) for index, key in enumerate(("research_all", "research_exercise", "research_reviews", "research_meta"))], cls="h-filters") if kind == "research" else None,
            Div(*research_items, cls="h-research"), cls="h-section h-container")
    if kind == "faq":
        return Section(intro, Div(*[Details(
            Summary(item["heading"]),
            *paragraphs(item.get("body"), preview=preview, field=f"items.{index}.body"),
        ) for index, item in enumerate(section.get("items", []))], cls="h-faq"), cls="h-section h-container")
    if kind == "team":
        return Section(intro, Div(*[Article(image(item["image"], item["heading"]) if item.get("image") else Div(Span(label(config, "team_photo")), cls="h-person-placeholder"), H3(item["heading"]),
            *paragraphs(item.get("body", ""), preview=preview, field=f"items.{index}.body"),
            A(label(config, "team_learn"), href=item["url"], target="_blank", rel="noopener noreferrer") if item.get("url") else None)
            for index, item in enumerate(section.get("items", []))], cls="h-team"), cls="h-section h-container")
    if kind == "contact":
        return Section(Div(H2(label(config, "contact_heading")), A(config.get("email", ""), href="mailto:" + config.get("email", "")),
            P(config.get("company", site.name)), P(config.get("address", "")), cls="h-contact-info"),
            Form(Input(type="hidden", name="csrf_token", value=csrf),
                 Label(label(config, "contact_name"), Input(name="name", required=True, maxlength=160, autocomplete="name")),
                 Label(label(config, "contact_email"), Input(name="email", type="email", required=True, maxlength=320, autocomplete="email")),
                 Label(label(config, "contact_message"), Textarea(name="message", required=True, minlength=10, maxlength=5000, rows=6)),
                 Div(Label(label(config, "contact_honeypot"), Input(name="website", tabindex="-1", autocomplete="off")), cls="h-honeypot", aria_hidden="true"),
                 P(label(config, "contact_consent") + " ", A(label(config, "privacy"), href=url(base, "/pages/privacy-policy"))),
                 Button(label(config, "contact_send"), type="submit", cls="h-button"), method="post", action=base + "/contact", cls="h-contact-form"), cls="h-section h-container h-contact")
    if kind == "embed":
        frame = Iframe(
            src=section["url"],
            title=heading or "Embedded content",
            loading="lazy",
            referrerpolicy="no-referrer",
            sandbox="allow-scripts",
            allow="camera 'none'; microphone 'none'; geolocation 'none'; payment 'none'",
            width="1280",
            height="720",
            cls="h-embed-frame",
        ) if section.get("url") else None
        return Section(intro, frame, cls="h-section h-container h-embed")
    if kind == "product":
        from app.commerce import payment_mode, payments_enabled, settings_for
        commerce_config = settings_for(db, site)
        checkout_enabled = bool(commerce_config and payments_enabled(db, site, commerce_config) and not preview)
        provider_mode = payment_mode(db, site, commerce_config) if checkout_enabled else "sandbox"
        product = catalog_product(db, site, page.product_id) if page.product_id else None
        variants = list(db.scalars(select(ProductVariant).where(ProductVariant.product_id == product.id, ProductVariant.tenant_id == site.tenant_id).order_by(ProductVariant.sort_order))) if product else []
        listing = db.scalar(select(VariantChannelListing).where(VariantChannelListing.channel_id == site.channel_id, VariantChannelListing.variant_id == variants[0].id)) if variants else None
        subscription_preview = section.get("subscription_preview", False)
        subscription_enabled = bool(checkout_enabled and product and product.id in commerce_config.subscription_product_ids_json)
        gallery = [section.get("image", ""), *section.get("gallery", [])]
        return Section(Figure(Div(image(section.get("image"), label(config, "product_alt"), eager=True), data_gallery_main=""),
            Div(*[Button(image(src, label(config, "gallery_alt") + f" {i + 1}"), type="button", data_gallery_src=src, aria_label=f"{label(config, 'gallery_view')} {i + 1}") for i, src in enumerate(gallery) if src], cls="h-gallery-thumbs"),
            Figcaption(label(config, "product_gallery_caption"))),
            Div(Small(product.name if product else label(config, "product_eyebrow"), cls="h-eyebrow"), H1(heading),
                *paragraphs(section.get("body"), preview=preview, field="body"),
                rich_content(product.description_html, cls="h-product-description")
                if product and product.description_html else None,
                P(money(listing.price_minor, listing.currency) + label(config, "price_unit") + (label(config, "one_time_price_suffix") if subscription_enabled else "") if listing else label(config, "price_confirm"), cls="h-price"),
                Small(label(config, "price_notice")),
                Form(Input(type="hidden", name="csrf_token", value=csrf),
                    Label(label(config, "choose_option"), Select(*[Option(v.name, value=v.id) for v in variants if v.is_active], name="variant_id", aria_label=label(config, "choose_option"), required=True)),
                    Label(label(config, "quantity"), Input(type="number", name="quantity", value=1, min=1, max=25, required=True)),
                    Label(label(config, "purchase_option"), Select(Option(label(config, "one_time"), value="off", selected=True),
                        Option(label(config, "subscribe"), value="on"), name="subscription")) if subscription_enabled else None,
                    P(label(config, "checkout_status_prefix") + (label(config, "live_checkout") if provider_mode == "live" else label(config, "sandbox_checkout")) + ". " + label(config, "shipping_notice")),
                    P(label(config, "monthly_notice")) if subscription_enabled else None,
                    Button(label(config, "add_bag"), cls="h-button"), method="post", action=base + "/cart/add", cls="h-contact-form") if checkout_enabled and listing else None,
                Label(label(config, "choose_option"), Select(*[Option(v.name, value=v.id) for v in variants], aria_label=label(config, "choose_option"))) if len(variants) > 1 and not checkout_enabled else None,
                Div(Label(Input(type="radio", name="purchase", checked=True), " " + label(config, "one_time")),
                    Label(Input(type="radio", name="purchase"), " " + label(config, "subscribe")), cls="h-purchase") if subscription_preview and not checkout_enabled else None,
                Button(label(config, "add_cart_phase2"), disabled=True, cls="h-button") if not checkout_enabled else None,
                P((label(config, "live_payments") if provider_mode == "live" else label(config, "sandbox_payments")) if checkout_enabled else label(config, "design_preview"), cls="h-muted"), cls="h-product-copy"), cls="h-product-detail h-container")
    return Section(intro, cta(section, base, config), cls="h-section h-container h-editorial")


def has_published_recently_viewed(db, site):
    """Load only matching published page documents, within this site and tenant."""
    documents = db.scalars(select(SitePage.published_json).where(
        SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
        SitePage.published_json.is_not(None),
        cast(SitePage.published_json, String).contains('"recently_viewed"')))
    return any(needs_widgets(doc.get("blocks", doc.get("sections", [])) if isinstance(doc, dict) else doc,
                             {"recently_viewed"}) for doc in documents)


def storefront(db, site, page, base, csrf, canonical, *, preview=False, editor=False, message="",
               newsletter_result="", blog_listing=False, blog_category="", blog_tag="",
               policy_html=None, policy_empty_message=None):
    from app.site_theme import pack_renderer
    config = site.settings_json if preview else site.published_settings_json
    if renderer := pack_renderer(config or {}, "storefront"):
        return renderer(db, site, page, base, csrf, canonical, preview=preview,
                        editor=editor, message=message)
    from app.commerce import payments_enabled, settings_for
    from app.customer_services import consent_text
    from app.site_theme import theme_style
    commerce_config = settings_for(db, site)
    customer_services_enabled = bool(commerce_config and payments_enabled(db, site, commerce_config))
    if customer_services_enabled:
        from app.commerce import payment_mode
        site._payment_mode = payment_mode(db, site, commerce_config)
    from app.site_tracking import product_item, tracking_nodes

    product = product_item(db, site, page) if not preview else None
    tracking_event = ({"name": "view_item", "currency": product["item"]["currency"],
                       "value": product["item"]["price"], "items": [product["item"]],
                       "variants": product["variants"]}
                      if product else {})
    provider_nodes = tracking_nodes(db, site, tracking_event, preview=preview)
    record_recent_product = bool(not preview and site.status == "published" and
        page.kind == "product" and page.product_id and has_published_recently_viewed(db, site))
    image = partial(owned_image, db, site)
    config = site.settings_json if preview else site.published_settings_json
    from app.site_editable_settings import shell_fields, shell_values
    generic_fields = shell_fields({"theme_pack": "generic"})
    generic_values = shell_values({**(config or {}), "theme_pack": "generic"}, generic_fields)
    config = {**(config or {}), "generic_labels": generic_values.get("generic_labels", {})}
    document = resolve_document(page.draft_json if preview else page.published_json, default_locale(site, preview=preview))
    from app.custom_data import resolve_dynamic_document
    from app.store_preferences import head_nodes
    from app.store_preferences import metadata as preference_metadata
    document = resolve_dynamic_document(db, site, page, document)
    preferences = preference_metadata(db, site, page, document)
    from app.site_blog import articles_for, categories_for, category_slug, metadata
    blog = metadata(document)
    listing_articles = articles_for(db, site, preview=preview, category=blog_category, tag=blog_tag) if blog_listing else None
    blog_filters = None
    if blog_listing:
        available = articles_for(db, site, preview=preview)
        category_slugs = {category_slug(p.draft_json if preview else p.published_json, site, preview=preview) for p in available}
        categories = [c for c in categories_for(db, site) if c.slug in category_slugs]
        tags = sorted({t for p in available for t in metadata(p.draft_json if preview else p.published_json)["tags"]})
        active = next((c.name for c in categories if c.slug == blog_category), blog_category) or blog_tag
        blog_filters = Div(
            Nav(A("All articles", href=url(base, "/blog"), aria_current="page" if not active else None),
                *[A(c.name, href=url(base, "/blog/category/" + c.slug), aria_current="page" if c.slug == blog_category else None) for c in categories],
                aria_label="Article categories", cls="h-blog-filters"),
            Nav(*[A(t, href=url(base, "/blog/tag/" + t), aria_current="page" if t == blog_tag else None) for t in tags],
                aria_label="Article tags", cls="h-blog-filters") if tags else None,
            P((active + " · " if active else "") + f"{len(listing_articles)} article" + ("" if len(listing_articles) == 1 else "s"), role="status"),
            P("No published articles in this category yet. Browse all articles above.") if not listing_articles else None,
            cls="h-container h-blog-tools")
        if active:
            document["title"] += " — " + active
    from app.site_seo import metadata as seo_metadata
    from app.site_seo import seo_document
    from app.site_structured_data import jsonld_for_page
    document = seo_document(db, site, page, document)
    seo = seo_metadata(site, page, document, canonical, preview=preview)
    structured_data = jsonld_for_page(
        db, site, page, document, seo["canonical"], seo, preview=preview,
    )
    from app.site_menus import rendered_navigation
    header_navigation = rendered_navigation(db, site, "header", preview=preview)
    footer_navigation = rendered_navigation(db, site, "footer", preview=preview)
    from app.site_snippets import published_snippets
    snippets = published_snippets(db, site, preview=preview)
    head_snippets = [_snippet_node(item, "head", head=True) for item in snippets["head"]]
    pre_footer_snippets = [_snippet_node(item, "pre-footer") for item in snippets["pre-footer"]]
    foot_snippets = [_snippet_node(item, "foot") for item in snippets["foot"]]
    has_gated_snippets = any(
        item["consent"] != "none" for placement in snippets.values() for item in placement
    )
    anchor_ids = {item["block_id"] for item in header_navigation + footer_navigation
                  if item.get("kind") == "anchor" and item["path"].split("#")[0] == page.path}
    home = page.path == "/"
    def section_view(section, index):
        rendered = render_section(db, site, page, config, section, base, csrf, preview, index == 0, blog_articles=listing_articles)
        if not editor:
            strip_editor_hooks(rendered)
        if rendered is not None and section["id"] in anchor_ids:
            rendered.attrs["id"] = "block-" + section["id"]
        if preview and rendered is not None:
            rendered.attrs["data-builder-section"] = section["id"]
            rendered.attrs["data-builder-label"] = (section.get("heading") or section["type"])[:100]
        return Div(blog_filters, rendered) if blog_listing and section["type"] == "articles" else rendered
    def brand(light=False):
        return image(config.get("logo_light" if light else "logo"), config.get("name", site.name), eager=True) or Span(config.get("name", site.name))
    newsletter_status = (
        label(config, "newsletter_success") if newsletter_result == "success"
        else label(config, "newsletter_error") if newsletter_result == "error" else ""
    )
    newsletter_active = site.status == "published" and not preview
    newsletter_block = Div(
        H2(label(config, "newsletter_title")),
        P(label(config, "newsletter_body")),
        P(newsletter_status, role="status", aria_live="polite") if newsletter_status else None,
        Form(
            Input(type="hidden", name="csrf_token", value=csrf),
            Input(type="hidden", name="newsletter_source", value="footer"),
            Label(label(config, "newsletter_email"), Input(
                name="email", type="email", required=True, maxlength=320,
                autocomplete="email", placeholder=label(config, "email_placeholder"),
            )),
            Label(Input(type="checkbox", name="marketing_consent", required=True),
                  " " + consent_text(site)),
            Input(name="website", tabindex="-1", autocomplete="off", style="display:none"),
            Button(label(config, "newsletter_submit"), type="submit", cls="h-button"),
            method="post", action=base + "/newsletter", cls="h-newsletter-form",
        ) if newsletter_active else P(label(config, "newsletter_preview"), cls="h-newsletter-preview"),
        cls="h-footer-newsletter h-container", id="footer",
    )
    return (
        Title(seo["title"]), Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Meta(name="description", content=seo["description"]),
        Meta(name="robots", content="noindex,nofollow" if site.status != "published" or preview else "noindex" if seo["noindex"] else "noindex,follow" if blog_listing and (blog_tag or not listing_articles) else "index,follow"),
        Meta(property="og:title", content=seo["social_title"]), Meta(property="og:description", content=seo["description"]),
        Meta(property="og:image", content=seo["image"]) if seo["image"] else None,
        Meta(property="og:type", content=seo["og_type"]),
        Meta(property="og:url", content=seo["canonical"]), Meta(name="twitter:card", content="summary_large_image" if seo["image"] else "summary"),
        Meta(name="google-site-verification", content=seo["google_verification"]) if seo["google_verification"] else None,
        Meta(name="msvalidate.01", content=seo["bing_verification"]) if seo["bing_verification"] else None,
        Link(rel="canonical", href=seo["canonical"]),
        *structured_data,
        *head_snippets,
        *head_nodes(db, site, preferences, preview=preview),
        *provider_nodes,
        Link(rel="icon", href=static_asset("favicon.svg"), type="image/svg+xml") if not preferences or not preferences.favicon_id else None, Link(rel="stylesheet", href=static_asset("site-builder.css")), Script(src=static_asset("site-builder.js"), defer=True),
        Script(src=static_asset("site-snippets.js"), defer=True) if has_gated_snippets else None,
        Link(rel="stylesheet", href=static_asset("site-theme.css")),
        Link(rel="stylesheet", href=static_asset("site-newsletter.css")),
        Link(rel="stylesheet", href=static_asset("site-blog.css")) if blog_listing else None,
        Style(NotStr(WIDGET_CSS)) if needs_widget_css(document["blocks"]) else None,
        Link(rel="stylesheet", href=static_asset("widgets-pack1.css")) if needs_pack1_css(document["blocks"]) else None,
        Script(src=static_asset("widgets-pack1.js"), defer=True) if needs_widget_js(document["blocks"]) else None,
        Link(rel="stylesheet", href=static_asset("widgets-pack2.css")) if needs_pack2_css(document["blocks"]) else None,
        Script(src=static_asset("widgets-pack2.js"), defer=True) if needs_pack2_js(document["blocks"]) else None,
        Link(rel="stylesheet", href=static_asset("widgets-pack3.css")) if needs_pack3_css(document["blocks"]) else None,
        Script(src=static_asset("widgets-pack3.js"), defer=True) if needs_pack3_js(document["blocks"]) else None,
        Link(rel="stylesheet", href=static_asset("widgets-pack4.css")) if needs_pack4_css(document["blocks"]) else None,
        Script(src=static_asset("widgets-pack4.js"), defer=True) if needs_pack4_js(document["blocks"]) or record_recent_product else None,
        Div(hidden=True, data_recent_product=page.product_id, data_site_id=site.id, data_required_consent="preferences") if record_recent_product else None,
        Script(src=static_asset("site-preview.js"), defer=True) if preview else None,
        Link(rel="stylesheet", href=static_asset("site-preview.css")) if preview else None,
        Link(rel="stylesheet", href=static_asset("cart-drawer.css")) if customer_services_enabled and not preview else None,
        Script(src=static_asset("cart-drawer.js"), defer=True) if customer_services_enabled and not preview else None,
        Div(
            A(label(config, "skip"), href="#content", cls="h-skip"),
            Div("SANDBOX · Test purchases only" if customer_services_enabled and not preview else "PHASE 1 PREVIEW · Design & content · Purchasing opens after review", cls="h-preview-note") if site.status != "published" or preview or customer_services_enabled else None,
            Div(config.get("announcement", ""), cls="h-announcement"),
            Header(A(brand(), href=base + "/", cls="h-brand h-brand-dark"), A(brand(True), href=base + "/", cls="h-brand h-brand-light"),
                Button(label(config, "menu"), type="button", data_menu_toggle="", aria_expanded="false", aria_controls="site-navigation", cls="h-menu-toggle"),
                Nav(*[Details(Summary(label(config, "shop")), Div(A(label(config, "all_products"), href=url(base, "/shop")), *[A(category["label"], href=url(base, category["path"])) for category in config.get("collections", [])], cls="h-dropdown"), cls="h-shop-menu") if item["label"] == "Shop" and item["path"] == "/shop" and config.get("collections") else A(item["label"], href=url(base, item["path"])) for item in header_navigation], id="site-navigation", cls="h-nav"),
                Div(A(label(config, "account"), href=base + "/account", aria_label=label(config, "account")) if commerce_config and commerce_config.customer_accounts_enabled else None, A(label(config, "bag"), href=base + "/cart", data_cart_open="") if customer_services_enabled else Button(label(config, "bag_empty"), type="button", data_commerce_notice="", aria_label=label(config, "cart_aria")), cls="h-header-actions"),
                cls="h-header", data_builder_shell="header" if preview else None),
            Div(message, role="status", cls="h-message") if message else None,
            Main(Div(Small(document.get("category", "LEARN"), cls="h-eyebrow"), H1(document["title"]),
                P("By " + blog["author_name"] if blog["author_name"] else f"By {site.name} team · Draft for editorial review"),
                P(blog["author_bio"]) if blog["author_bio"] else None,
                image(document.get("image"), label(config, "editorial_image_alt")), cls="h-article-heading h-container") if page.kind == "article" else None,
                H1(document["title"], cls="h-container h-policy-title") if policy_html is not None else None,
                Div(NotStr(policy_html), cls="h-container h-policy-content") if policy_html else None,
                P(policy_empty_message, cls="h-container h-policy-empty") if policy_empty_message else None,
                *[section_view(s, i) for i, s in enumerate(document.get("blocks", [])) if not s.get("hidden")], id="content"),
            Section(Div(Small(label(config, "offer_eyebrow"), cls="h-eyebrow"), H2(config.get("offer", "Stay curious.")), P(label(config, "offer_body"))),
                    Button(label(config, "offer_preview"), type="button", data_offer_open="", cls="h-button"), cls="h-offer"),
            *pre_footer_snippets,
            Footer(Div(Div(A(brand(), href=base + "/", cls="h-brand"), P(config.get("tagline", ""))),
                Div(H3(label(config, "footer_explore")), *[A(item["label"], href=url(base, item["path"])) for item in footer_navigation]),
                Div(H3(label(config, "footer_help")), *[A(label, href=url(base, "/pages/" + slug)) for slug, label in [("terms-and-conditions", label(config, "terms")), ("privacy-policy", label(config, "privacy_policy")), ("faq", label(config, "faq")), ("returns-and-refunds", label(config, "returns")), ("shipping-policy", label(config, "shipping_policy"))]]),
                Div(H3(config.get("company", site.name)), P(config.get("address", "")), A(config.get("email", ""), href="mailto:" + config.get("email", "")),
                    Div(*[social_link(s) for s in config.get("socials", [])], cls="h-socials")), cls="h-footer-grid"),
                newsletter_block,
                rich_content(config.get("footer", ""), cls="h-disclaimer"), Div(Span(label(config, "copyright") + config.get("company", site.name)), payment_methods(), Button(label(config, "cookies"), type="button", data_cookie_open=""), cls="h-footer-bottom"),
                cls="h-footer", data_builder_shell="footer" if preview else None),
            *foot_snippets,
            Div(H2("Your privacy, your choice."), P("Essential storage keeps this site working. Preferences, analytics and marketing are off unless you choose them."),
                Details(Summary("Preferences"), Label(Input(type="checkbox", checked=True, disabled=True), " Essential (always on)"), Label(Input(type="checkbox", id="consent-preferences"), " Preferences: remembers things like products you viewed on this device"), Label(Input(type="checkbox", id="consent-analytics"), " Analytics"), Label(Input(type="checkbox", id="consent-marketing"), " Marketing")),
                Div(Button("Accept all", data_consent="all"), Button("Decline", data_consent="none"), Button("Save preferences", data_consent="custom")),
                A("Privacy policy", href=url(base, "/pages/privacy-policy")), id="h-cookie", cls="h-cookie", role="region", aria_label="Cookie preferences", hidden=True),
            Div(Button(label(config, "offer_close"), type="button", data_offer_close=""), H2(config.get("offer", "Stay curious.")),
                Form(Input(type="hidden", name="csrf_token", value=csrf),
                    P(label(config, "offer_confirm")),
                    Label(label(config, "offer_email"), Input(name="email", type="email", required=True, maxlength=320, autocomplete="email")),
                    Label(Input(name="marketing_consent", type="checkbox", required=True), " " + consent_text(site)),
                    Input(name="website", tabindex="-1", autocomplete="off", style="display:none"),
                    Button(label(config, "offer_submit"), type="submit", cls="h-button"), method="post", action=base + "/newsletter") if customer_services_enabled else Div(
                    P(label(config, "offer_preview_body")),
                    Label(label(config, "offer_email"), Input(type="email", placeholder=label(config, "email_placeholder"), disabled=True)),
                    Label(Input(type="checkbox", disabled=True), " " + consent_text(site))),
                A(label(config, "privacy"), href=url(base, "/pages/privacy-policy")), id="h-offer-panel", cls="h-offer-panel", hidden=True),
            Div(id="h-toast", role="status", cls="h-toast", hidden=True),
            cls="h-site " + ("h-home" if home else "h-inner"), data_site=site.id,
            style=theme_style(config), data_themed="true" if theme_style(config) else "false",
            data_builder_page=page.id if preview else "",
        ),
    )
