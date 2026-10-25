"""Versioned FastAPI integration and headless catalog surface."""

from __future__ import annotations

import secrets
from collections.abc import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.config import settings
from app.db import SessionLocal, health
from app.models import (
    Category,
    ProductImage,
    ProductOption,
    ProductOptionValue,
    ProductVariant,
    VariantChannelListing,
)
from app.services import available_stock, money, product_by_slug, products, tenant_channel


class HealthResponse(BaseModel):
    status: str
    product: str
    version: str
    database: str
    writes_enabled: bool


class ProductSummary(BaseModel):
    id: str
    slug: str
    name: str
    subtitle: str
    category: str
    image_url: str
    price_minor: int
    compare_at_minor: int | None
    currency: str
    formatted_price: str
    available: int
    handle: str = ""
    status: str = "active"
    vendor: str = ""
    product_type: str = ""
    tags: list[str] = Field(default_factory=list)
    description_html: str = ""
    short_description: str = ""
    seo_title: str = ""
    seo_description: str = ""
    metafields: dict = Field(default_factory=dict)


class StockUpdate(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=0, le=1_000_000)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


bearer = HTTPBearer(auto_error=False)


def require_token(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer),
) -> None:
    if not settings.api_token:
        raise HTTPException(status_code=503, detail="API writes are not configured")
    supplied = credentials.credentials if credentials else ""
    if not secrets.compare_digest(settings.api_token, supplied):
        raise HTTPException(status_code=401, detail="Valid bearer token required")


api = FastAPI(
    title="FastShop API",
    version=__version__,
    description=(
        "Multi-channel commerce API for the FastShop storefront and integrations. "
        "Reads are public; selected writes require a deployment token."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    servers=[{"url": f"{settings.public_url}/api", "description": "Production"}],
    contact={"name": "FastSME", "url": "https://fastsme.com"},
    license_info={"name": "MIT"},
)
api.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.public_url],
    allow_credentials=False,
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["Accept", "Content-Type", "Authorization"],
)

from app.billing_webhooks import register_billing_webhooks  # noqa: E402
from app.commerce_webhooks import register_commerce_webhooks  # noqa: E402

register_commerce_webhooks(api)
register_billing_webhooks(api)


@api.get("/", tags=["System"])
def index() -> dict:
    return {
        "name": "FastShop API",
        "version": __version__,
        "documentation": f"{settings.public_url}/developers",
        "openapi": f"{settings.public_url}/api/openapi.json",
    }


@api.get("/v1/health", response_model=HealthResponse, tags=["System"])
def api_health() -> HealthResponse:
    database = health()
    return HealthResponse(
        status="ok",
        product="FastShop",
        version=__version__,
        database=database["database"],
        writes_enabled=bool(settings.api_token),
    )


@api.get("/v1/products", response_model=list[ProductSummary], tags=["Catalog"])
def list_products(
    q: str = Query(default="", max_length=120),
    category: str = Query(default="", max_length=120),
    session: Session = Depends(get_session),
) -> list[ProductSummary]:
    return [
        ProductSummary(
            id=card.product.id,
            slug=card.product.slug,
            name=card.product.name,
            subtitle=card.product.subtitle,
            category=card.product.category.name,
            image_url=card.product.image_url,
            price_minor=card.price_minor,
            compare_at_minor=card.compare_at_minor,
            currency=card.currency,
            formatted_price=money(card.price_minor, card.currency),
            available=card.available,
            handle=card.product.slug,
            status=card.product.status,
            vendor=card.product.vendor,
            product_type=card.product.product_type,
            tags=card.product.tags_json,
            description_html=card.product.description_html,
            short_description=card.product.short_description,
            seo_title=card.product.seo_title,
            seo_description=card.product.seo_description,
            metafields=product_metafields(session, card.product),
        )
        for card in products(session, query=q, category=category)
    ]


@api.get("/v1/products/{slug}", tags=["Catalog"])
def get_product(slug: str, session: Session = Depends(get_session)) -> dict:
    product = product_by_slug(session, slug)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    _, channel = tenant_channel(session)
    variants = []
    for variant in product.variants:
        if not variant.is_active:
            continue
        listing = session.scalar(select(VariantChannelListing).where(
            VariantChannelListing.variant_id == variant.id,
            VariantChannelListing.channel_id == channel.id))
        variants.append(
            {
                "id": variant.id,
                "sku": variant.sku,
                "name": variant.name,
                "attributes": variant.attributes_json,
                "available": available_stock(session, variant.id),
                "barcode": variant.barcode,
                "metafields": product_metafields(session, product, variant=variant),
                "track_inventory": variant.track_inventory,
                "continue_selling_when_out_of_stock": variant.continue_selling_when_out_of_stock,
                "image_id": variant.image_id,
                "price_minor": listing.price_minor if listing else None,
                "compare_at_minor": listing.compare_at_minor if listing else None,
            }
        )
    return {
        "id": product.id,
        "slug": product.slug,
        "name": product.name,
        "description": product.description,
        "channel": channel.slug,
        "currency": channel.currency,
        "variants": variants,
        "metafields": product_metafields(session, product),
        "handle": product.slug,
        "status": product.status,
        "vendor": product.vendor,
        "product_type": product.product_type,
        "tags": product.tags_json,
        "description_html": product.description_html,
        "short_description": product.short_description,
        "seo_title": product.seo_title,
        "seo_description": product.seo_description,
        "template": product.template,
        "published_at": product.published_at,
        "requires_shipping": product.requires_shipping,
        "weight_grams": product.weight_grams,
        "dimensions": product.dimensions_json,
        "country_of_origin": product.country_of_origin,
        "hs_code": product.hs_code,
        "charge_tax": product.charge_tax,
        "tax_code": product.tax_code,
        "images": [{"id": image.id, "url": image.image_url, "alt": image.alt,
                    "position": image.position} for image in session.scalars(
                        select(ProductImage).where(ProductImage.tenant_id == product.tenant_id,
                            ProductImage.product_id == product.id).order_by(ProductImage.position))],
        "options": [{"name": option.name, "position": option.position,
                     "values": list(session.scalars(select(ProductOptionValue.value).where(
                         ProductOptionValue.tenant_id == product.tenant_id,
                         ProductOptionValue.option_id == option.id).order_by(ProductOptionValue.position)))}
                    for option in session.scalars(select(ProductOption).where(
                        ProductOption.tenant_id == product.tenant_id,
                        ProductOption.product_id == product.id).order_by(ProductOption.position))],
    }


@api.get("/v1/categories", tags=["Catalog"])
def list_categories(session: Session = Depends(get_session)) -> list[dict]:
    tenant, _ = tenant_channel(session)
    rows = session.scalars(
        select(Category).where(Category.tenant_id == tenant.id).order_by(Category.sort_order)
    )
    return [{"id": row.id, "slug": row.slug, "name": row.name, "description": row.description} for row in rows]


@api.post(
    "/v1/inventory",
    dependencies=[Depends(require_token)],
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Integrations"],
)
def propose_stock_update(payload: StockUpdate, session: Session = Depends(get_session)) -> dict:
    variant = session.scalar(select(ProductVariant).where(ProductVariant.sku == payload.sku))
    if not variant:
        raise HTTPException(status_code=404, detail="SKU not found")
    return {
        "status": "accepted_for_preview",
        "applied": False,
        "sku": payload.sku,
        "current_available": available_stock(session, variant.id),
        "proposed_quantity": payload.quantity,
    }


def product_metafields(db, product, *, variant=None):
    """Site keys preserve isolation when a catalogue product appears in several sites."""
    from sqlalchemy import or_

    from app.custom_data import values
    from app.models import Site, SitePage
    sites = db.scalars(select(Site).where(Site.tenant_id == product.tenant_id,
        or_(select(SitePage.id).where(SitePage.site_id == Site.id,
            SitePage.tenant_id == product.tenant_id, SitePage.product_id == product.id).exists(),
            select(VariantChannelListing.id).join(ProductVariant,
                ProductVariant.id == VariantChannelListing.variant_id).where(
                VariantChannelListing.channel_id == Site.channel_id,
                ProductVariant.tenant_id == product.tenant_id,
                ProductVariant.product_id == product.id).exists())).order_by(Site.slug))
    kind, owner_id = ("variant", variant.id) if variant else ("product", product.id)
    return {site.slug: values(db, site, kind, owner_id) for site in sites}
