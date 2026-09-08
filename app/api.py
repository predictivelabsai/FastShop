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
from app.models import Category, ProductVariant
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
        variants.append(
            {
                "id": variant.id,
                "sku": variant.sku,
                "name": variant.name,
                "attributes": variant.attributes_json,
                "available": available_stock(session, variant.id),
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

