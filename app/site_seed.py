"""Idempotent H2 4 You editorial fixture; never overwrite merchant edits."""

from __future__ import annotations

import copy

from sqlalchemy import select

from app.content import create_page
from app.models import (
    Category,
    Channel,
    Membership,
    Product,
    ProductType,
    ProductVariant,
    Site,
    Tenant,
    User,
    VariantChannelListing,
)
from app.site_articles import ARTICLES

ASSETS = "/static/h24you/"
DISCLAIMER = "These statements have not been evaluated by the Food and Drug Administration. This product is not intended to diagnose, treat, cure, or prevent any disease."
RESEARCH = [
    {"heading": "Molecular hydrogen: a review of the research", "body": "Ichihara et al. · Medical Gas Research · 2015. A review covering experimental and clinical literature; different methods should not be treated as interchangeable.", "url": "https://pubmed.ncbi.nlm.nih.gov/26483953/", "theme": "Reviews"},
    {"heading": "The evolution of molecular hydrogen", "body": "Dixon et al. · Medical Gas Research · 2013. An early overview of research approaches and questions for further investigation.", "url": "https://pubmed.ncbi.nlm.nih.gov/23680032/", "theme": "Reviews"},
    {"heading": "Acute supplementation and submaximal exercise", "body": "LeBaron et al. · Journal of Sports Medicine · 2019. A randomized, double-blind, placebo-controlled crossover pilot study examining exercise measurements.", "url": "https://pubmed.ncbi.nlm.nih.gov/30918832/", "theme": "Exercise"},
    {"heading": "Hydrogen water, endurance and perceived fatigue", "body": "Mikami et al. · Canadian Journal of Physiology and Pharmacology · 2019. A controlled study exploring measurements around exercise.", "url": "https://pubmed.ncbi.nlm.nih.gov/31251888/", "theme": "Exercise"},
    {"heading": "Exercise-induced oxidative stress: reviewing the evidence", "body": "Li et al. · Frontiers in Nutrition · 2024. A systematic review and meta-analysis examining exercise-related oxidative stress in healthy adults.", "url": "https://pubmed.ncbi.nlm.nih.gov/38590828/", "theme": "Meta-analyses"},
    {"heading": "Fatigue and aerobic capacity: a systematic review", "body": "Zhou et al. · Frontiers in Nutrition · 2023. A meta-analysis comparing evidence on fatigue and aerobic capacity.", "url": "https://pubmed.ncbi.nlm.nih.gov/36819697/", "theme": "Meta-analyses"},
    {"heading": "Hydrogen-rich water and blood lipid profiles", "body": "Todorovic et al. · Pharmaceuticals · 2023. A systematic review; follow the original source for methods and limitations.", "url": "https://doi.org/10.3390/ph16020142", "theme": "Meta-analyses"},
]


def section(kind, heading="", body="", **kwargs):
    return {"type": kind, "heading": heading, "body": body, **kwargs}


def seed_h24you(db, admin_email):
    existing = db.scalar(select(Site).where(Site.slug == "h24you"))
    if existing:
        return existing
    tenant = Tenant(slug="h24you", name="H2 4 You Ltd")
    db.add(tenant)
    db.flush()
    admin = db.scalar(select(User).where(User.email == admin_email))
    if admin:
        db.add(Membership(tenant_id=tenant.id, user_id=admin.id, role="admin"))
    channel = Channel(tenant_id=tenant.id, slug="us", name="United States", currency="USD", country_code="US", locale="en", prices_include_tax=False)
    db.add(channel)
    db.flush()
    config = {
        "name": "H2 | 4 YOU", "tagline": "A little wonder. In every glass.",
        "logo": ASSETS + "logo-dark.svg", "logo_light": ASSETS + "logo-light.svg",
        "accent": "#1554cc", "email": "info@h24you.com", "company": "H2 4 You Ltd",
        "address": "Staadioni 2, Aruküla, Estonia — PLACEHOLDER address",
        "announcement": "A fresh perspective on your everyday water.",
        "offer": "10% off your first order.", "footer": DISCLAIMER,
        "preview_notice": True, "science_date": "PLACEHOLDER — date to be confirmed",
        "science_source": "https://hydrogenclinicalresearch.com/",
        "facts": [{"value": "2,000+", "label": "published scientific articles"},
                  {"value": "120+", "label": "publications on human trials"},
                  {"value": "68", "label": "human publications on drinking hydrogen water"}],
        "claims": [{"key": "cellular", "text": "Supports cellular health.*", "approved": False},
                   {"key": "oxidative", "text": "Helps neutralize free radicals and supports the body's response to oxidative stress.*", "approved": False}],
        "navigation": [{"label": "Shop", "path": "/shop"}, {"label": "Science", "path": "/pages/science"},
                       {"label": "Learn", "path": "/blogs/learn"}, {"label": "About us", "path": "/pages/about-us"},
                       {"label": "Contact", "path": "/pages/contact"}],
        "collections": [{"label": "Hydrogen Tablets", "path": "/collections/hydrogen-tablets"},
                        {"label": "Hydrogen Water Bottles", "path": "/collections/hydrogen-water-bottles"}],
        "socials": [{"label": "Instagram", "url": ""}, {"label": "Facebook", "url": ""}],
    }
    site = Site(tenant_id=tenant.id, channel_id=channel.id, slug="h24you", name="H2 4 You",
                settings_json=config, published_settings_json=copy.deepcopy(config), status="preview")
    db.add(site)
    db.flush()
    physical = ProductType(tenant_id=tenant.id, slug="physical", name="Physical product")
    db.add(physical)
    db.flush()
    product_ids = {}
    for slug, name, category_slug, category_name, image, price, flavours in [
        ("hydrogen-tablets", "Hydrogen tablets", "hydrogen-tablets", "Hydrogen Tablets", "tablets-placeholder.webp", 2995, ["Unflavoured", "Raspberry", "Pineapple"]),
        ("hydroxy-go", "Hydroxy Go bottle", "hydrogen-water-bottles", "Hydrogen Water Bottles", "bottle-placeholder.webp", None, ["Original"]),
    ]:
        category = Category(tenant_id=tenant.id, slug=category_slug, name=category_name)
        db.add(category)
        db.flush()
        product = Product(tenant_id=tenant.id, product_type_id=physical.id, category_id=category.id,
                          slug=slug, name=name, image_url=ASSETS + image, is_published=True)
        product.subtitle = "Three flavours. One simple ritual." if slug == "hydrogen-tablets" else "A fresh perspective, wherever you go."
        db.add(product)
        db.flush()
        product_ids[slug] = product.id
        for i, flavour in enumerate(flavours):
            variant = ProductVariant(tenant_id=tenant.id, product_id=product.id, sku=f"H24-{slug}-{i}",
                                     name=flavour, attributes_json={"flavour": flavour}, sort_order=i)
            db.add(variant)
            db.flush()
            if price is not None:
                db.add(VariantChannelListing(variant_id=variant.id, channel_id=channel.id, currency="USD", price_minor=price))

    def page(path, title, kind, sections, **metadata):
        document = {"title": title, "description": metadata.pop("description", f"Explore {title.lower()} with H2 4 You. A fresh perspective on hydrogen water."), "sections": sections, **metadata}
        item = create_page(db, site, title, path, kind, document)
        item.published_json = copy.deepcopy(item.draft_json)
        return item

    page("/", "A little wonder. In every glass.", "home", [
        section("hero", "A little wonder.\nIn every glass.", "Meet molecular hydrogen. A fresh ritual, a little curiosity, and a whole new way to look at your water.", eyebrow="MEET YOUR NEW DAILY RITUAL", image=ASSETS + "water-placeholder.webp", video=ASSETS + "water-placeholder.mp4", mobile_video=ASSETS + "water-mobile-placeholder.mp4", link="/shop", button="Shop the collection"),
        section("products", "Two ways to make it yours.", "A drop-in ritual. A take-anywhere bottle. Find your everyday.", eyebrow="YOUR WATER, REIMAGINED"),
        section("split", "Small molecule.\nBig curiosity.", "Hydrogen is the simplest element. Molecular hydrogen brings two hydrogen atoms together as H₂. Researchers are exploring what happens when we bring it into everyday water. We're here for the questions, as much as the first sip.", image=ASSETS + "water-placeholder.webp", link="/pages/science", button="Explore the research", eyebrow="FOLLOW YOUR CURIOSITY"),
        section("facts", "A growing conversation in science."),
        section("reviews", "Your everyday stories belong here.", "This space is reserved for real customer experiences. Sample layout only — no customer reviews have been collected."),
        section("articles", "A little reading. A fresh perspective.", "Good questions make a great starting point.", eyebrow="THE LEARN JOURNAL"),
    ])
    for path, title, category in [("/shop", "Find your everyday.", ""), ("/collections/hydrogen-tablets", "Just add water.", "hydrogen-tablets"), ("/collections/hydrogen-water-bottles", "Take your ritual with you.", "hydrogen-water-bottles")]:
        page(path, title, "collection", [section("text", title, "A simple moment to make your own. Explore hydrogen tablets and our everyday bottle.", eyebrow="THE COLLECTION"), section("products", category=category)])

    tablets = page("/products/hydrogen-tablets", "Hydrogen tablets", "product", [
        section("product", "Just add water.\nMake it a moment.", "A fresh glass. A little fizz. An easy addition to the rhythm of your day.", image=ASSETS + "tablets-placeholder.webp", gallery=[ASSETS + "water-placeholder.webp", ASSETS + "ritual-placeholder.webp"], subscription_preview=True),
        section("faq", "The details", items=[
            {"heading": "How to use", "body": "Draft for review: drop one tablet in water, wait until dissolved, then drink. Final water volume and directions will follow the confirmed product label."},
            {"heading": "Ingredients", "body": "PLACEHOLDER: Supplement Facts and ingredients to be added."},
            {"heading": "Product details", "body": "PLACEHOLDER: final pack size, Supplement Facts and product details to be confirmed."},
            {"heading": "Shipping", "body": "PLACEHOLDER: free standard US shipping over $75. Final rates and delivery times will be confirmed before checkout opens."}]),
        section("split", "A small pause.\nA fresh start.", "The morning glass. The desk-side break. The moment after you put your gym bag down. Make room for a simple water ritual that fits into your day.", image=ASSETS + "tablets-placeholder.webp"),
        section("split", "Watch the ritual begin.", "A tablet, a glass, a little anticipation. PLACEHOLDER motion study: animated product imagery, to be replaced with real dissolving footage.", image=ASSETS + "tablets-placeholder.webp", video=ASSETS + "dissolving-placeholder.mp4"),
        section("split", "And then, the first sip.", "A moment to yourself. PLACEHOLDER motion study with a fictional model, to be replaced with original drinking footage.", image=ASSETS + "ritual-placeholder.webp", video=ASSETS + "drinking-placeholder.mp4"),
        section("claims", "Research, with room for questions."),
        section("reviews", "Real experiences, coming soon.", "Sample review layout. No invented ratings, names or testimonials."),
    ])
    tablets.product_id = product_ids["hydrogen-tablets"]
    bottle = page("/products/hydroxy-go", "Hydroxy Go bottle", "product", [
        section("product", "Your water ritual.\nReady to go.", "Meet the Hydroxy Go bottle, reimagined with H2 4 You branding. Fill, start a cycle, and make a moment for yourself.", image=ASSETS + "bottle-placeholder.webp", gallery=[ASSETS + "water-placeholder.webp", ASSETS + "ritual-placeholder.webp"]),
        section("faq", "Get to know your bottle", items=[
            {"heading": "How does it work?", "body": "The source Hydroxy Go uses PEM electrolysis with a one-button control and 5- or 10-minute cycles. Final H2 4 You instructions are pending review."},
            {"heading": "Size and charging", "body": "Source specifications: 290 ml capacity; USB-C charging, 5V / 1A input; lithium-polymer battery. PLACEHOLDER: confirm these specifications for the supplied H2 4 You model."},
            {"heading": "What comes with it?", "body": "The source product lists the bottle, USB-C cable and user manual. PLACEHOLDER: final H2 4 You contents and warranty terms to be confirmed."},
            {"heading": "What water should I use?", "body": "Use clean drinking water and follow the supplied product manual. Final care, cleaning and compatible-water instructions will be added after review."},
            {"heading": "What is the price?", "body": "PLACEHOLDER: US price to be confirmed. The source Hydroxy Go page lists €190; this is a reference price, not a USD offer."}]),
        section("split", "A place in your everyday.", "On your desk, beside your book, or packed for the day ahead. A portable bottle gives a familiar ritual a home wherever you are.", image=ASSETS + "bottle-placeholder.webp"),
        section("text", "Source specifications", "Product information adapted from the Hydroxy Go source page; specifications and final packaging need confirmation.", link="https://hydroxy.health/toode/https-hydroxy-health-toode-hydroxy-go/", button="Read the source product page"),
    ])
    bottle.product_id = product_ids["hydroxy-go"]
    page("/pages/science", "Follow the questions.", "science", [
        section("text", "Small molecule.\nAn open field of research.", "Researchers are studying molecular hydrogen from many angles. Start with the original papers, look at the methods, and leave room for what we don't yet know.", eyebrow="THE SCIENCE"),
        section("facts", "A growing body of literature."),
        section("research", "Explore the research", "Much of the research is early-stage. Different populations, methods and outcomes mean results need careful interpretation. More research is needed.", items=RESEARCH),
        section("text", "Keep exploring.", "Visit an independent database of published hydrogen studies. A study listing is a starting point for reading, not proof of a product benefit.", link="https://hydrogenclinicalresearch.com/", button="Open the research database"),
    ])
    page("/blogs/learn", "Curiosity looks good on you.", "blog", [section("text", "A fresh perspective.\nOne good question at a time.", "Plain-language reading for curious minds. No shortcuts, no big promises. Just a place to start.", eyebrow="THE LEARN JOURNAL"), section("articles")])
    for article in ARTICLES:
        page("/blogs/learn/" + article["slug"], article["title"], "article",
             [section("text", heading, body) for heading, body in article["parts"]] +
             [section("references", "Studies referenced", items=[RESEARCH[i] for i in article["references"]])],
             category=article["category"], image=article["image"])
    page("/pages/about-us", "Curious by nature.", "content", [
        section("text", "Health enthusiasts.\nCurious humans.", "We care about supporting our own health and sharing that curiosity with others. H2 4 You brings together a love of everyday rituals and an interest in molecular hydrogen research.", eyebrow="ABOUT H2 4 YOU"),
        section("split", "It starts with a question.", "Hydroxy's founder, Helen Kaljuvee, describes a long-standing interest in nutrition and personal wellbeing. That curiosity led her to explore hydrogen and to share what she was learning. For H2 4 You, the invitation is simple: ask good questions, read the research, and make space for a ritual you enjoy.", image=ASSETS + "water-placeholder.webp"),
        section("team", "The people behind the curiosity", items=[
            {"heading": "Helen Kaljuvee", "body": "Founder of Hydroxy Health. Interested in nutrition, everyday wellbeing and sharing the questions that led her to molecular hydrogen. Biography adapted for review.", "url": "https://hydroxy.health/en/about-us/"},
            {"heading": "Andres Randma", "body": "Business partner and creator of Lumiorav magnesium water, as supplied in the brief. Bringing a shared interest in water and everyday wellbeing. Biography pending review.", "url": "https://lumiorav.ee/"}]),
    ])
    page("/pages/contact", "Let's talk.", "contact", [section("text", "A question?\nWe're all ears.", "Products, research, or just a hello. Send us a note and we'll be in touch.", eyebrow="CONTACT"), section("contact")])
    for slug, title in [("terms-and-conditions", "Terms and Conditions"), ("privacy-policy", "Privacy Policy"), ("returns-and-refunds", "Returns and Refunds")]:
        page(f"/pages/{slug}", title, "legal", [section("text", title, "PLACEHOLDER — draft awaiting the company's final US-facing policy and adviser review. This preview does not accept orders. Please contact info@h24you.com with questions.")])
    page("/pages/faq", "Good questions.", "content", [section("faq", "Frequently asked questions", items=[
        {"heading": "What is hydrogen water?", "body": "Water with dissolved molecular hydrogen (H₂). Visit Learn for an introduction and references."},
        {"heading": "Can I order now?", "body": "This is the Phase 1 design and content preview. Checkout will follow after the site has been reviewed."},
        {"heading": "Which tablet flavours are planned?", "body": "Unflavoured, Raspberry and Pineapple. Final ingredients and pack details are pending confirmation."}])])
    db.flush()
    return site
