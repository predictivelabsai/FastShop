"""Bounded Shopify GraphQL Admin API migration connector.

The connector reads a shop with an operator-managed Admin API token and writes
only FastShop-owned rows after a stored dry run is explicitly confirmed. All
order and catalog amounts use Shopify shop currency; presentment amounts are
intentionally ignored so a migration has one deterministic source scale.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import httpx
from sqlalchemy import delete, select

from app import content
from app.connectors import ConnectorCapabilities, CredentialState
from app.customer_services import customer_for, normalized_email
from app.models import (
    Category,
    ExternalMapping,
    Order,
    OrderLine,
    Product,
    ProductType,
    ProductVariant,
    SiteOrder,
    VariantChannelListing,
)
from app.services import CommerceError

API_VERSION = "2026-10"
PAGE_SIZE = 50
MAX_PAGES = 5
MAX_ITEMS = 750
MAX_REQUESTS = 40
TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 2_000_000
PLAN_VERSION = 1
SYSTEM = "shopify"
_SHOP = re.compile(r"[a-z0-9][a-z0-9-]*\.myshopify\.com")
_DECIMAL = re.compile(r"\d+(?:\.\d+)?")
_GID = re.compile(r"gid://shopify/([A-Za-z]+)/([A-Za-z0-9_-]+)")
_CURRENCY_SCALES = {
    "BHD": 3,
    "CLP": 0,
    "DJF": 0,
    "EUR": 2,
    "GBP": 2,
    "ISK": 0,
    "JPY": 0,
    "JOD": 3,
    "KRW": 0,
    "KWD": 3,
    "OMR": 3,
    "TND": 3,
    "USD": 2,
    "VND": 0,
}

_SHOP_QUERY = """
query Shop {
  shop { currencyCode }
}
"""

_COLLECTIONS_QUERY = """
query Collections($first: Int!, $after: String) {
  collections(first: $first, after: $after, sortKey: ID) {
    nodes { id title handle description updatedAt }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_PRODUCTS_QUERY = """
query Products($first: Int!, $after: String) {
  products(first: $first, after: $after, sortKey: ID) {
    nodes {
      id title handle descriptionHtml productType status updatedAt
      featuredImage { url }
      collections(first: 25) {
        nodes { id }
        pageInfo { hasNextPage }
      }
      variants(first: 100) {
        nodes { id title sku price updatedAt }
        pageInfo { hasNextPage }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_CUSTOMERS_QUERY = """
query Customers($first: Int!, $after: String) {
  customers(first: $first, after: $after, sortKey: ID) {
    nodes {
      id firstName lastName displayName updatedAt
      defaultEmailAddress { emailAddress }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_ORDERS_QUERY = """
query Orders($first: Int!, $after: String) {
  orders(first: $first, after: $after, sortKey: ID) {
    nodes {
      id name email updatedAt cancelledAt
      displayFinancialStatus displayFulfillmentStatus
      customer {
        firstName lastName displayName
        defaultEmailAddress { emailAddress }
      }
      currentSubtotalPriceSet { shopMoney { amount currencyCode } }
      currentTotalDiscountsSet { shopMoney { amount currencyCode } }
      totalShippingPriceSet { shopMoney { amount currencyCode } }
      currentTotalTaxSet { shopMoney { amount currencyCode } }
      currentTotalPriceSet { shopMoney { amount currencyCode } }
      shippingAddress {
        firstName lastName company address1 address2 city provinceCode zip countryCodeV2
      }
      lineItems(first: 100) {
        nodes {
          name sku quantity
          product { id }
          variant { id title }
          originalUnitPriceSet { shopMoney { amount currencyCode } }
          discountedTotalSet { shopMoney { amount currencyCode } }
        }
        pageInfo { hasNextPage }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_OPERATIONS = {
    "Shop": ("shop", _SHOP_QUERY),
    "Collections": ("collections", _COLLECTIONS_QUERY),
    "Products": ("products", _PRODUCTS_QUERY),
    "Customers": ("customers", _CUSTOMERS_QUERY),
    "Orders": ("orders", _ORDERS_QUERY),
}


@dataclass(frozen=True)
class _ShopifyConfig:
    shop: str
    token: str
    fixture: Path | None = None


def _prefix(site) -> str:
    return f"FASTSHOP_SHOPIFY_{site.id.upper()}_"


def _valid_shop(value: str) -> bool:
    return bool(_SHOP.fullmatch(value))


def _valid_token(value: str) -> bool:
    return 8 <= len(value) <= 512 and not re.search(r"\s", value)


def _config(site, *, allow_incomplete: bool = False) -> _ShopifyConfig:
    prefix = _prefix(site)
    fixture_value = os.getenv("FASTSHOP_SHOPIFY_FIXTURE_PATH", "").strip()
    fixture = None
    if fixture_value and os.getenv("FASTSHOP_ENV", "development").lower() != "production":
        fixture = Path(fixture_value).resolve()
    enabled = os.getenv(prefix + "ENABLED", "").lower() == "true" or fixture is not None
    shop = os.getenv(prefix + "SHOP", "").strip().lower()
    token = os.getenv(prefix + "ADMIN_API_ACCESS_TOKEN", "")
    if fixture:
        shop = shop or "fixture.myshopify.com"
        token = token or "shpat_fixture"
    config = _ShopifyConfig(shop, token, fixture)
    if allow_incomplete:
        return config
    if not enabled:
        raise CommerceError("Shopify integration is disabled for this site.")
    if not _valid_shop(shop):
        raise CommerceError("Configure this site's exact myshopify.com shop domain.")
    if not _valid_token(token):
        raise CommerceError("Configure this site's Shopify Admin API access token.")
    if fixture is not None and (not fixture.is_file() or fixture.suffix.lower() != ".json"):
        raise CommerceError("The development Shopify fixture is unavailable.")
    return config


def currency_scale(currency: str) -> int:
    """Return the ISO source scale used for Shopify shop-money amounts."""
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise CommerceError("Shopify returned an invalid shop currency.")
    return _CURRENCY_SCALES.get(currency, 2)


def money_minor(value: str, *, currency: str, scale: int) -> int:
    """Use Decimal and HALF_UP at the exact source-currency scale; never float."""
    try:
        if not isinstance(value, str) or not _DECIMAL.fullmatch(value) or not 0 <= scale <= 4:
            raise ValueError
        amount = (Decimal(value) * (Decimal(10) ** scale)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        if not amount.is_finite() or amount < 0 or amount > 100_000_000_000:
            raise ValueError
        return int(amount)
    except (InvalidOperation, ValueError):
        raise CommerceError(f"Shopify returned an invalid {currency} amount.") from None


def _plain(value, limit=30_000) -> str:
    text = re.sub(r"<[^>]*>", " ", html.unescape(str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _slug(value, fallback) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return (value or fallback)[:100].strip("-")


def _external_id(value, kind: str) -> str:
    match = _GID.fullmatch(str(value or ""))
    if not match or match.group(1) != kind or len(match.group(0)) > 140:
        raise CommerceError(f"Shopify returned a {kind.lower()} without a valid id.")
    return match.group(0)


def _id_suffix(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "", value.rsplit("/", 1)[-1])[-24:] or "import"


def _version(item: dict) -> str:
    supplied = str(item.get("updatedAt") or "")
    if supplied:
        return supplied[:80]
    encoded = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()[:32]


def _connection_nodes(value) -> list[dict]:
    if not isinstance(value, dict) or not isinstance(value.get("nodes"), list):
        raise CommerceError("Shopify returned an invalid GraphQL connection.")
    if any(not isinstance(item, dict) for item in value["nodes"]):
        raise CommerceError("Shopify returned an invalid GraphQL connection.")
    return value["nodes"]


def _fixture_transport(path: Path) -> httpx.MockTransport:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CommerceError("The development Shopify fixture is invalid.") from None
    if not isinstance(data, dict) or not isinstance(data.get("shop"), dict):
        raise CommerceError("The development Shopify fixture is invalid.")

    def connection(items, variables):
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            return None
        first = variables.get("first", PAGE_SIZE)
        after = variables.get("after")
        if type(first) is not int or not 1 <= first <= 100:
            return None
        try:
            start = int(after) if after else 0
        except (TypeError, ValueError):
            return None
        end = min(start + first, len(items))
        nodes = json.loads(json.dumps(items[start:end]))
        return {"nodes": nodes, "pageInfo": {
            "hasNextPage": end < len(items), "endCursor": str(end) if end < len(items) else None,
        }}

    def respond(request):
        try:
            body = json.loads(request.content)
            operation = body["operationName"]
            variables = body.get("variables", {})
            if operation == "Shop":
                return httpx.Response(200, json={"data": {"shop": data["shop"]}})
            field = _OPERATIONS[operation][0]
            result = connection(data.get(field, []), variables)
            if result is None:
                raise ValueError
            if field == "products":
                for node in result["nodes"]:
                    node["collections"] = connection(node.get("collections", []), {"first": 25})
                    node["variants"] = connection(node.get("variants", []), {"first": 100})
            if field == "orders":
                for node in result["nodes"]:
                    node["lineItems"] = connection(node.get("lineItems", []), {"first": 100})
            return httpx.Response(200, json={"data": {field: result}})
        except (KeyError, TypeError, ValueError):
            return httpx.Response(400, json={"errors": [{"message": "invalid fixture request"}]})

    return httpx.MockTransport(respond)


class ShopifyGateway:
    """Read-only GraphQL plumbing; tenant permission is checked before construction."""

    def __init__(self, site, *, tenant_id, transport=None):
        if not tenant_id or site.tenant_id != tenant_id:
            raise CommerceError("Store not found.")
        config = _config(site)
        self._endpoint = f"https://{config.shop}/admin/api/{API_VERSION}/graphql.json"
        self._token = config.token
        self._transport = transport or (_fixture_transport(config.fixture) if config.fixture else None)
        self._requests = 0

    def execute(self, operation: str, variables: dict | None = None) -> dict:
        if operation not in _OPERATIONS:
            raise CommerceError("Unsupported Shopify resource.")
        self._requests += 1
        if self._requests > MAX_REQUESTS:
            raise CommerceError("Shopify request limit reached; narrow the bounded import.")
        _, query = _OPERATIONS[operation]
        try:
            with httpx.Client(
                timeout=TIMEOUT_SECONDS,
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.post(
                    self._endpoint,
                    headers={"X-Shopify-Access-Token": self._token},
                    json={"query": query, "operationName": operation, "variables": variables or {}},
                )
                response.raise_for_status()
                if len(response.content) > MAX_RESPONSE_BYTES:
                    raise ValueError
                result = response.json()
                if (not isinstance(result, dict) or result.get("errors")
                        or not isinstance(result.get("data"), dict)):
                    raise ValueError
                return result["data"]
        except (httpx.HTTPError, ValueError):
            raise CommerceError(
                "Shopify read failed; check the shop domain, token, scopes and API access."
            ) from None

    def shop_currency(self) -> str:
        shop = self.execute("Shop").get("shop")
        currency = str(shop.get("currencyCode", "")) if isinstance(shop, dict) else ""
        currency_scale(currency)
        return currency

    def paged(self, operation: str) -> list[dict]:
        field = _OPERATIONS[operation][0]
        rows = []
        after = None
        for _page in range(1, MAX_PAGES + 1):
            connection = self.execute(operation, {"first": PAGE_SIZE, "after": after}).get(field)
            batch = _connection_nodes(connection)
            rows.extend(batch)
            if len(rows) > MAX_ITEMS:
                raise CommerceError("Shopify item limit reached; split the migration into smaller runs.")
            page_info = connection.get("pageInfo")
            if not isinstance(page_info, dict):
                raise CommerceError("Shopify returned invalid pagination data.")
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")
            if not isinstance(after, str) or not after:
                raise CommerceError("Shopify returned invalid pagination data.")
        else:
            raise CommerceError(f"Shopify {field} exceeded the {MAX_PAGES}-page import limit.")
        return rows


def _mapping(db, site, resource_type: str, external_id) -> ExternalMapping | None:
    return db.scalar(select(ExternalMapping).where(
        ExternalMapping.tenant_id == site.tenant_id,
        ExternalMapping.site_id == site.id,
        ExternalMapping.system == SYSTEM,
        ExternalMapping.resource_type == resource_type,
        ExternalMapping.external_id == str(external_id),
    ))


def _map(db, site, resource_type: str, external_id, local_id: str, version: str) -> ExternalMapping:
    mapping = _mapping(db, site, resource_type, external_id)
    if mapping is None:
        mapping = ExternalMapping(
            tenant_id=site.tenant_id,
            site_id=site.id,
            system=SYSTEM,
            resource_type=resource_type,
            external_id=str(external_id),
            local_id=local_id,
        )
        db.add(mapping)
    mapping.local_id, mapping.version = local_id, str(version)[:80]
    db.flush()
    return mapping


def _existing_action(db, site, resource_type, external_id) -> str:
    return "update" if _mapping(db, site, resource_type, external_id) else "create"


def _unique_slug(db, site, model, value: str, external_id) -> str:
    if not db.scalar(select(model.id).where(model.tenant_id == site.tenant_id, model.slug == value)):
        return value
    suffix = "-shop-" + _id_suffix(str(external_id))
    return (value[:max(1, 100 - len(suffix))].rstrip("-") + suffix)[:100]


def _unique_sku(db, site, value: str, external_id) -> str:
    suffix = _id_suffix(str(external_id))
    candidate = value[:100] or ("SHOP-" + suffix)[:100]
    used = db.scalar(select(ProductVariant.id).where(
        ProductVariant.tenant_id == site.tenant_id, ProductVariant.sku == candidate
    ))
    return candidate if not used else (candidate[:70] + "-SHOP-" + suffix)[:100]


def _normalize_collection(item: dict) -> dict:
    external_id = _external_id(item.get("id"), "Collection")
    name = _plain(item.get("title"), 160)
    if not name:
        raise CommerceError("Shopify returned a collection without a title.")
    return {
        "external_id": external_id,
        "name": name,
        "slug": _slug(item.get("handle"), "collection-" + _id_suffix(external_id)),
        "description": _plain(item.get("description")),
        "version": _version(item),
    }


def _normalize_variant(item: dict, currency: str, scale: int) -> dict:
    external_id = _external_id(item.get("id"), "ProductVariant")
    name = _plain(item.get("title") or "Default", 180) or "Default"
    price = item.get("price")
    price_minor = money_minor(price, currency=currency, scale=scale) if price not in (None, "") else None
    return {
        "external_id": external_id,
        "name": name,
        "sku": _plain(item.get("sku"), 100),
        "price_minor": price_minor,
        "version": _version(item),
    }


def _normalize_product(item: dict, currency: str, scale: int) -> dict:
    external_id = _external_id(item.get("id"), "Product")
    name = _plain(item.get("title"), 220)
    if not name:
        raise CommerceError("Shopify returned a product without a title.")
    image = item.get("featuredImage") if isinstance(item.get("featuredImage"), dict) else {}
    image_url = content.safe_url(str(image.get("url", "")), media=True) if image.get("url") else ""
    collections = _connection_nodes(item.get("collections"))
    variants = _connection_nodes(item.get("variants"))
    if not variants:
        raise CommerceError("Shopify returned a product without a variant.")
    return {
        "external_id": external_id,
        "name": name,
        "slug": _slug(item.get("handle"), "product-" + _id_suffix(external_id)),
        "subtitle": _plain(item.get("productType"), 260),
        "description": _plain(item.get("descriptionHtml")),
        "image_url": image_url,
        "collection_external_ids": [
            _external_id(row.get("id"), "Collection") for row in collections
        ],
        "published": item.get("status") == "ACTIVE",
        "variants": [_normalize_variant(row, currency, scale) for row in variants],
        "version": _version(item),
    }


def _customer_email(item: dict) -> str:
    address = item.get("defaultEmailAddress")
    return normalized_email(address.get("emailAddress", "") if isinstance(address, dict) else "")


def _normalize_customer(item: dict) -> dict:
    external_id = _external_id(item.get("id"), "Customer")
    email = _customer_email(item)
    name = _plain(" ".join(filter(None, [item.get("firstName"), item.get("lastName")])), 160)
    name = name or _plain(item.get("displayName"), 160)
    return {"external_id": external_id, "email": email, "name": name, "version": _version(item)}


def _shop_money(value, currency: str, scale: int) -> int:
    money = value.get("shopMoney") if isinstance(value, dict) else None
    if not isinstance(money, dict) or money.get("currencyCode") != currency:
        raise CommerceError("Shopify returned money outside the configured shop currency.")
    return money_minor(money.get("amount"), currency=currency, scale=scale)


def _normalize_order(item: dict, currency: str, scale: int) -> dict:
    external_id = _external_id(item.get("id"), "Order")
    customer = item.get("customer") if isinstance(item.get("customer"), dict) else {}
    address = customer.get("defaultEmailAddress")
    customer_email = address.get("emailAddress", "") if isinstance(address, dict) else ""
    email = normalized_email(item.get("email") or customer_email)
    customer_name = _plain(
        " ".join(filter(None, [customer.get("firstName"), customer.get("lastName")])), 160
    ) or _plain(customer.get("displayName"), 160)
    lines = []
    line_connection = item.get("lineItems")
    for raw in _connection_nodes(line_connection):
        quantity = raw.get("quantity")
        if type(quantity) is not int or quantity < 1:
            raise CommerceError("Shopify returned an invalid order line.")
        product = raw.get("product") if isinstance(raw.get("product"), dict) else {}
        variant = raw.get("variant") if isinstance(raw.get("variant"), dict) else {}
        product_id = _external_id(product.get("id"), "Product") if product.get("id") else ""
        variant_id = (
            _external_id(variant.get("id"), "ProductVariant") if variant.get("id") else ""
        )
        lines.append({
            "product_external_id": product_id,
            "variant_external_id": variant_id,
            "name": _plain(raw.get("name"), 220) or "Imported item",
            "sku": _plain(raw.get("sku"), 100),
            "quantity": quantity,
            "unit_price_minor": _shop_money(raw.get("originalUnitPriceSet"), currency, scale),
            "total_minor": _shop_money(raw.get("discountedTotalSet"), currency, scale),
        })
    financial = str(item.get("displayFinancialStatus") or "PENDING")
    payment_status = (
        "refunded" if financial == "REFUNDED" else
        "paid" if financial in {"PAID", "PARTIALLY_REFUNDED"} else "pending"
    )
    fulfillment = str(item.get("displayFulfillmentStatus") or "UNFULFILLED")
    order_status = "cancelled" if item.get("cancelledAt") else (
        "fulfilled" if fulfillment == "FULFILLED" else "unfulfilled"
    )
    shipping = item.get("shippingAddress") if isinstance(item.get("shippingAddress"), dict) else {}
    shipping_keys = {
        "first_name": "firstName", "last_name": "lastName", "company": "company",
        "address_1": "address1", "address_2": "address2", "city": "city",
        "state": "provinceCode", "postcode": "zip", "country": "countryCodeV2",
    }
    return {
        "external_id": external_id,
        "number": _plain(str(item.get("name") or _id_suffix(external_id)).lstrip("#"), 40),
        "email": email,
        "customer_name": customer_name,
        "currency": currency,
        "currency_scale": scale,
        "status": order_status,
        "payment_status": payment_status,
        "subtotal_minor": _shop_money(item.get("currentSubtotalPriceSet"), currency, scale),
        "discount_minor": _shop_money(item.get("currentTotalDiscountsSet"), currency, scale),
        "shipping_minor": _shop_money(item.get("totalShippingPriceSet"), currency, scale),
        "tax_minor": _shop_money(item.get("currentTotalTaxSet"), currency, scale),
        "total_minor": _shop_money(item.get("currentTotalPriceSet"), currency, scale),
        "shipping_address": {
            target: _plain(shipping.get(source), 200) for target, source in shipping_keys.items()
        },
        "lines": lines,
        "version": _version(item),
    }


class ShopifyConnector:
    platform = SYSTEM
    label = "Shopify"
    capabilities = ConnectorCapabilities(
        imports=("collections", "products", "customers", "orders"),
        exports=(),
        max_pages=MAX_PAGES,
        max_items=MAX_ITEMS,
        timeout_seconds=TIMEOUT_SECONDS,
    )

    def credential_state(self, site) -> CredentialState:
        config = _config(site, allow_incomplete=True)
        prefix = _prefix(site)
        fixture_ready = config.fixture is not None and config.fixture.is_file()
        enabled = os.getenv(prefix + "ENABLED", "").lower() == "true"
        configured = fixture_ready or (
            enabled and _valid_shop(config.shop) and _valid_token(config.token)
        )
        message = (
            "Development fixture store ready." if fixture_ready else
            "Operator Admin API credentials are configured." if configured else
            "Ask the operator to configure this site's Shopify connection."
        )
        return CredentialState(configured, message, "fixture" if fixture_ready else "sandbox")

    def fetch(self, site, *, transport=None) -> dict:
        gateway = ShopifyGateway(site, tenant_id=site.tenant_id, transport=transport)
        currency = gateway.shop_currency()
        scale = currency_scale(currency)
        collections = gateway.paged("Collections")
        products = gateway.paged("Products")
        customers = gateway.paged("Customers")
        orders = gateway.paged("Orders")
        warnings = []
        for product in products:
            if product.get("variants", {}).get("pageInfo", {}).get("hasNextPage"):
                warnings.append(f"Product {product.get('id', '')} has more than 100 variants; split the migration.")
            if product.get("collections", {}).get("pageInfo", {}).get("hasNextPage"):
                warnings.append(f"Product {product.get('id', '')} belongs to more than 25 collections; only the first 25 were reviewed.")
        for order in orders:
            if order.get("lineItems", {}).get("pageInfo", {}).get("hasNextPage"):
                warnings.append(f"Order {order.get('id', '')} has more than 100 lines; split the migration.")
        if any("split the migration" in warning for warning in warnings):
            raise CommerceError("Shopify nested resource limit reached; split the migration.")
        variants = sum(len(_connection_nodes(row.get("variants"))) for row in products)
        total = len(collections) + len(products) + len(customers) + len(orders) + variants
        if total > MAX_ITEMS:
            raise CommerceError(f"Shopify returned more than the {MAX_ITEMS}-item run limit.")
        return {
            "collections": collections,
            "products": products,
            "customers": customers,
            "orders": orders,
            "currency": currency,
            "currency_scale": scale,
            "warnings": warnings,
        }

    def dry_run(self, db, site, user_id, *, transport=None):
        raw = self.fetch(site, transport=transport)
        warnings, unmapped = list(raw["warnings"]), []
        collections = [_normalize_collection(item) for item in raw["collections"]]
        products = [
            _normalize_product(item, raw["currency"], raw["currency_scale"])
            for item in raw["products"]
        ]
        customers = []
        for item in raw["customers"]:
            try:
                customers.append(_normalize_customer(item))
            except CommerceError:
                unmapped.append({
                    "type": "customer", "external_id": str(item.get("id", "")),
                    "reason": "missing or invalid email",
                })
        orders = []
        for item in raw["orders"]:
            try:
                orders.append(_normalize_order(item, raw["currency"], raw["currency_scale"]))
            except CommerceError as exc:
                unmapped.append({
                    "type": "order", "external_id": str(item.get("id", "")),
                    "reason": str(exc),
                })

        known_collections = {row["external_id"] for row in collections}
        known_products = {row["external_id"] for row in products}
        known_variants = {
            variant["external_id"] for row in products for variant in row["variants"]
        }
        for product in products:
            missing = [
                value for value in product["collection_external_ids"]
                if value not in known_collections and not _mapping(db, site, "collection", value)
            ]
            if missing:
                unmapped.append({
                    "type": "product", "external_id": product["external_id"],
                    "reason": "collection ids not fetched: " + ", ".join(missing),
                })
        for order in orders:
            for line in order["lines"]:
                product_ok = line["product_external_id"] in known_products or _mapping(
                    db, site, "product", line["product_external_id"]
                )
                variant_ok = line["variant_external_id"] in known_variants or _mapping(
                    db, site, "variant", line["variant_external_id"]
                )
                if not product_ok or not variant_ok:
                    unmapped.append({
                        "type": "order_line", "external_id": order["external_id"],
                        "reason": f"catalog mapping unavailable for {line['name']}",
                    })
        if unmapped:
            warnings.append(
                "Unmapped order lines keep snapshots but do not attach to a FastShop variant."
            )
        warnings.append(
            "Shopify presentment amounts are ignored; all imported amounts use shop currency."
        )

        resources = {
            "collections": collections,
            "products": products,
            "customers": customers,
            "orders": orders,
        }
        counts, samples = {}, []
        singular = {
            "collections": "collection", "products": "product",
            "customers": "customer", "orders": "order",
        }
        for name, rows in resources.items():
            actions = [_existing_action(db, site, singular[name], row["external_id"]) for row in rows]
            counts[name] = {
                "fetched": len(raw[name]), "create": actions.count("create"),
                "update": actions.count("update"), "skip": len(raw[name]) - len(rows),
            }
            for row, action in list(zip(rows, actions, strict=True))[:3]:
                samples.append({
                    "type": singular[name], "external_id": row["external_id"],
                    "label": row.get("name") or row.get("email") or row.get("number"),
                    "action": action,
                })
        payload = {
            "schema_version": PLAN_VERSION,
            "platform": SYSTEM,
            "currency": raw["currency"],
            "currency_scale": raw["currency_scale"],
            **resources,
        }
        report = {
            "platform": SYSTEM,
            "mode": "dry-run",
            "counts": counts,
            "samples": samples,
            "unmapped_items": unmapped[:100],
            "warnings": warnings,
            "limits": {
                "max_pages": MAX_PAGES,
                "max_items": MAX_ITEMS,
                "timeout_seconds": TIMEOUT_SECONDS,
            },
        }
        return payload, report

    @staticmethod
    def _validate_payload(payload):
        if (not isinstance(payload, dict) or payload.get("schema_version") != PLAN_VERSION
                or payload.get("platform") != SYSTEM):
            raise CommerceError("The reviewed Shopify plan is invalid.")
        if payload.get("currency_scale") != currency_scale(payload.get("currency", "")):
            raise CommerceError("The reviewed Shopify plan has an invalid currency scale.")
        total = 0
        for key in ("collections", "products", "customers", "orders"):
            if not isinstance(payload.get(key), list):
                raise CommerceError("The reviewed Shopify plan is incomplete.")
            total += len(payload[key])
        total += sum(len(row.get("variants", [])) for row in payload["products"])
        if total > MAX_ITEMS:
            raise CommerceError("The reviewed Shopify plan exceeds the item limit.")

    def apply_import(self, db, site, user_id, payload):
        self._validate_payload(payload)
        counts = {
            name: {"created": 0, "updated": 0}
            for name in ("collections", "products", "customers", "orders")
        }
        for row in payload["collections"]:
            mapping = _mapping(db, site, "collection", row["external_id"])
            category = db.scalar(select(Category).where(
                Category.id == mapping.local_id, Category.tenant_id == site.tenant_id
            )) if mapping else None
            action = "updated" if category else "created"
            if category is None:
                category = Category(
                    tenant_id=site.tenant_id,
                    slug=_unique_slug(db, site, Category, row["slug"], row["external_id"]),
                    name=row["name"],
                )
                db.add(category)
                db.flush()
            category.name, category.description = row["name"], row["description"]
            _map(db, site, "collection", row["external_id"], category.id, row["version"])
            counts["collections"][action] += 1

        product_type = db.scalar(select(ProductType).where(
            ProductType.tenant_id == site.tenant_id, ProductType.slug == "physical"
        ))
        if product_type is None:
            product_type = ProductType(
                tenant_id=site.tenant_id, slug="physical", name="Physical product"
            )
            db.add(product_type)
            db.flush()
        fallback_category = None
        for row in payload["products"]:
            category = None
            for external_id in row["collection_external_ids"]:
                collection_mapping = _mapping(db, site, "collection", external_id)
                if collection_mapping:
                    category = db.scalar(select(Category).where(
                        Category.id == collection_mapping.local_id,
                        Category.tenant_id == site.tenant_id,
                    ))
                if category:
                    break
            if category is None:
                if fallback_category is None:
                    fallback_category = db.scalar(select(Category).where(
                        Category.tenant_id == site.tenant_id,
                        Category.slug == "shopify-import",
                    ))
                    if fallback_category is None:
                        fallback_category = Category(
                            tenant_id=site.tenant_id,
                            slug="shopify-import",
                            name="Shopify import",
                        )
                        db.add(fallback_category)
                        db.flush()
                category = fallback_category
            mapping = _mapping(db, site, "product", row["external_id"])
            product = db.scalar(select(Product).where(
                Product.id == mapping.local_id, Product.tenant_id == site.tenant_id
            )) if mapping else None
            action = "updated" if product else "created"
            if product is None:
                product = Product(
                    tenant_id=site.tenant_id,
                    product_type_id=product_type.id,
                    category_id=category.id,
                    slug=_unique_slug(db, site, Product, row["slug"], row["external_id"]),
                    name=row["name"],
                )
                db.add(product)
                db.flush()
            content.validate_media_ownership(db, site, {"image": row["image_url"]})
            product.category_id, product.name = category.id, row["name"]
            product.subtitle, product.description = row["subtitle"], row["description"]
            product.image_url, product.is_published = row["image_url"], bool(row["published"])
            _map(db, site, "product", row["external_id"], product.id, row["version"])
            for index, variant_row in enumerate(row["variants"]):
                variant_mapping = _mapping(db, site, "variant", variant_row["external_id"])
                variant = db.scalar(select(ProductVariant).where(
                    ProductVariant.id == variant_mapping.local_id,
                    ProductVariant.tenant_id == site.tenant_id,
                    ProductVariant.product_id == product.id,
                )) if variant_mapping else None
                if variant is None:
                    variant = ProductVariant(
                        tenant_id=site.tenant_id,
                        product_id=product.id,
                        sku=_unique_sku(db, site, variant_row["sku"], variant_row["external_id"]),
                        name=variant_row["name"],
                        sort_order=index,
                    )
                    db.add(variant)
                    db.flush()
                variant.name, variant.sort_order, variant.is_active = variant_row["name"], index, True
                _map(db, site, "variant", variant_row["external_id"], variant.id, variant_row["version"])
                listing = db.scalar(select(VariantChannelListing).where(
                    VariantChannelListing.variant_id == variant.id,
                    VariantChannelListing.channel_id == site.channel_id,
                ))
                if variant_row["price_minor"] is None:
                    if listing:
                        db.delete(listing)
                elif listing:
                    listing.currency = payload["currency"]
                    listing.price_minor = variant_row["price_minor"]
                else:
                    db.add(VariantChannelListing(
                        variant_id=variant.id,
                        channel_id=site.channel_id,
                        currency=payload["currency"],
                        price_minor=variant_row["price_minor"],
                    ))
            counts["products"][action] += 1

        for row in payload["customers"]:
            mapping = _mapping(db, site, "customer", row["external_id"])
            customer = customer_for(db, site, row["email"], create=True)
            if row["name"]:
                customer.name = row["name"]
            _map(db, site, "customer", row["external_id"], customer.id, row["version"])
            counts["customers"]["updated" if mapping else "created"] += 1

        for row in payload["orders"]:
            customer = customer_for(db, site, row["email"], create=True)
            if row["customer_name"]:
                customer.name = row["customer_name"]
            mapping = _mapping(db, site, "order", row["external_id"])
            order = db.scalar(select(Order).where(
                Order.id == mapping.local_id, Order.tenant_id == site.tenant_id
            )) if mapping else None
            action = "updated" if order else "created"
            if order is None:
                number = ("SH-" + row["number"])[:40]
                if db.scalar(select(Order.id).where(
                    Order.tenant_id == site.tenant_id, Order.number == number
                )):
                    number = ("SH-" + _id_suffix(row["external_id"]))[:40]
                order = Order(
                    tenant_id=site.tenant_id,
                    channel_id=site.channel_id,
                    number=number,
                    idempotency_key=("shopify:" + site.id + ":" + row["external_id"])[:80],
                    email=row["email"],
                    currency=row["currency"],
                    subtotal_minor=0,
                    total_minor=0,
                )
                db.add(order)
                db.flush()
            order.email, order.status = row["email"], row["status"]
            order.payment_status, order.currency = row["payment_status"], row["currency"]
            order.subtotal_minor, order.discount_minor = row["subtotal_minor"], row["discount_minor"]
            order.shipping_minor, order.tax_minor = row["shipping_minor"], row["tax_minor"]
            order.total_minor, order.shipping_address_json = row["total_minor"], row["shipping_address"]
            db.execute(delete(OrderLine).where(OrderLine.order_id == order.id))
            for line in row["lines"]:
                variant_mapping = _mapping(db, site, "variant", line["variant_external_id"])
                variant = db.scalar(select(ProductVariant).where(
                    ProductVariant.id == variant_mapping.local_id,
                    ProductVariant.tenant_id == site.tenant_id,
                )) if variant_mapping else None
                db.add(OrderLine(
                    order_id=order.id,
                    variant_id=variant.id if variant else None,
                    sku=line["sku"],
                    product_name=line["name"],
                    variant_name=variant.name if variant else "",
                    quantity=line["quantity"],
                    unit_price_minor=line["unit_price_minor"],
                    total_minor=line["total_minor"],
                ))
            link = db.scalar(select(SiteOrder).where(
                SiteOrder.order_id == order.id,
                SiteOrder.tenant_id == site.tenant_id,
                SiteOrder.site_id == site.id,
            ))
            if link is None:
                db.add(SiteOrder(
                    tenant_id=site.tenant_id,
                    site_id=site.id,
                    order_id=order.id,
                    customer_id=customer.id,
                ))
            else:
                link.customer_id = customer.id
            _map(db, site, "order", row["external_id"], order.id, row["version"])
            counts["orders"][action] += 1
        db.flush()
        return counts

    def export_bundle(self, db, site):
        raise CommerceError("Shopify export is not included in this migration connector.")
