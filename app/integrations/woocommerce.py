"""Bounded WooCommerce REST v3 migration connector.

Imports read WooCommerce and write only FastShop-owned rows after a stored dry-run
is confirmed. Exports are reviewable JSON bundles; no remote writes occur.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, select

from app import content
from app.connectors import ConnectorCapabilities, CredentialState
from app.customer_services import customer_for, normalized_email
from app.models import (
    Category,
    Channel,
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

PAGE_SIZE = 50
MAX_PAGES = 5
MAX_ITEMS = 750
MAX_REQUESTS = 40
TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 2_000_000
PLAN_VERSION = 1
SYSTEM = "woocommerce"
_RESOURCE_LISTS = {"categories", "products", "orders", "customers"}
_RESOURCE_PATH = re.compile(r"(?:categories|products|orders|customers)(?:/\d+|/\d+/variations)?")
_DECIMAL = re.compile(r"\d+(?:\.\d+)?")
_CURRENCY_SCALES = {
    "BHD": 3, "CLP": 0, "DJF": 0, "EUR": 2, "GBP": 2, "ISK": 0,
    "JPY": 0, "JOD": 3, "KRW": 0, "KWD": 3, "OMR": 3, "TND": 3,
    "USD": 2, "VND": 0,
}


@dataclass(frozen=True)
class _WooConfig:
    origin: str
    key: str
    secret: str
    currency: str
    scale: int
    fixture: Path | None = None


def _prefix(site) -> str:
    return f"FASTSHOP_WOOCOMMERCE_{site.id.upper()}_"


def _valid_origin(origin: str) -> bool:
    parsed = urlsplit(origin)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path or host == "localhost"
            or "." not in host or host.endswith((".localhost", ".local", ".internal"))):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return address.is_global


def _config(site, *, allow_incomplete: bool = False) -> _WooConfig:
    prefix = _prefix(site)
    fixture_value = os.getenv("FASTSHOP_WOOCOMMERCE_FIXTURE_PATH", "").strip()
    fixture = None
    if fixture_value and os.getenv("FASTSHOP_ENV", "development").lower() != "production":
        fixture = Path(fixture_value).resolve()
    enabled = os.getenv(prefix + "ENABLED", "").lower() == "true" or fixture is not None
    origin = os.getenv(prefix + "URL", "").rstrip("/") or ("https://fixture.woocommerce.test" if fixture else "")
    key = os.getenv(prefix + "CONSUMER_KEY", "") or ("ck_fixture" if fixture else "")
    secret = os.getenv(prefix + "CONSUMER_SECRET", "") or ("cs_fixture" if fixture else "")
    channel_currency = str(getattr(site, "channel_currency", "") or "USD").upper()
    currency = os.getenv(prefix + "CURRENCY", channel_currency).strip().upper()
    default_scale = _CURRENCY_SCALES.get(currency, 2)
    try:
        scale = int(os.getenv(prefix + "CURRENCY_SCALE", str(default_scale)))
    except ValueError:
        scale = -1
    config = _WooConfig(origin, key, secret, currency, scale, fixture)
    if allow_incomplete:
        return config
    if not enabled:
        raise CommerceError("WooCommerce integration is disabled for this site.")
    if not _valid_origin(origin):
        raise CommerceError("Configure a public HTTPS WooCommerce origin without a path.")
    if not key.startswith("ck_") or not secret.startswith("cs_"):
        raise CommerceError("Configure this site's WooCommerce API credentials.")
    if not re.fullmatch(r"[A-Z]{3}", currency) or not 0 <= scale <= 4:
        raise CommerceError("Configure this site's WooCommerce currency and decimal scale.")
    if fixture is not None and (not fixture.is_file() or fixture.suffix.lower() != ".json"):
        raise CommerceError("The development WooCommerce fixture is unavailable.")
    return config


def money_minor(value: str, *, currency: str, scale: int) -> int:
    """Use Decimal and HALF_UP at the exact source-currency scale; never float."""
    try:
        if not isinstance(value, str) or not _DECIMAL.fullmatch(value) or not 0 <= scale <= 4:
            raise ValueError
        amount = (Decimal(value) * (Decimal(10) ** scale)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if not amount.is_finite() or amount < 0 or amount > 100_000_000_000:
            raise ValueError
        return int(amount)
    except (InvalidOperation, ValueError):
        raise CommerceError(f"WooCommerce returned an invalid {currency} amount.") from None


def usd_minor(value: str) -> int:
    """Backward-compatible strict USD helper used by existing boundary tests."""
    if isinstance(value, str) and "." in value and len(value.rsplit(".", 1)[1]) > 2:
        raise CommerceError("WooCommerce returned an invalid USD amount.")
    return money_minor(value, currency="USD", scale=2)


def decimal_amount(minor: int, scale: int) -> str:
    if type(minor) is not int or minor < 0 or not 0 <= scale <= 4:
        raise CommerceError("Invalid FastShop amount for WooCommerce export.")
    if scale == 0:
        return str(minor)
    factor = 10 ** scale
    return f"{minor // factor}.{minor % factor:0{scale}d}"


def _plain(value, limit=30_000) -> str:
    text = re.sub(r"<[^>]*>", " ", html.unescape(str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _slug(value, fallback) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return (value or fallback)[:100].strip("-")


def _version(item: dict) -> str:
    supplied = str(item.get("date_modified_gmt") or item.get("date_modified") or "")
    if supplied:
        return supplied[:80]
    encoded = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()[:32]


def _fixture_transport(path: Path) -> httpx.MockTransport:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CommerceError("The development WooCommerce fixture is invalid.") from None
    if not isinstance(data, dict):
        raise CommerceError("The development WooCommerce fixture is invalid.")

    def respond(request):
        relative = request.url.path.removeprefix("/wp-json/wc/v3/")
        match = re.fullmatch(r"products/(\d+)/variations", relative)
        items = data.get("variations", {}).get(match.group(1), []) if match else data.get(relative, [])
        if not isinstance(items, list):
            return httpx.Response(500, json={"error": "invalid fixture"})
        page = int(request.url.params.get("page", "1"))
        per_page = int(request.url.params.get("per_page", str(PAGE_SIZE)))
        start = (page - 1) * per_page
        return httpx.Response(200, json=items[start:start + per_page])

    return httpx.MockTransport(respond)


class WooCommerceGateway:
    """Read-only REST plumbing; tenant permission is checked before construction."""

    def __init__(self, site, *, tenant_id, transport=None):
        if not tenant_id or site.tenant_id != tenant_id:
            raise CommerceError("Store not found.")
        config = _config(site)
        self.currency, self.scale = config.currency, config.scale
        self._key, self._secret, self._origin = config.key, config.secret, config.origin
        self._transport = transport or (_fixture_transport(config.fixture) if config.fixture else None)
        self._requests = 0

    def _get(self, resource, params=None):
        if not _RESOURCE_PATH.fullmatch(resource):
            raise CommerceError("Unsupported WooCommerce resource.")
        self._requests += 1
        if self._requests > MAX_REQUESTS:
            raise CommerceError("WooCommerce request limit reached; narrow the bounded import.")
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS, transport=self._transport,
                              follow_redirects=False, trust_env=False) as client:
                response = client.get(self._origin + "/wp-json/wc/v3/" + resource,
                    params=params, auth=(self._key, self._secret))
                response.raise_for_status()
                if len(response.content) > MAX_RESPONSE_BYTES:
                    raise ValueError
                result = response.json()
                if not isinstance(result, (list, dict)):
                    raise ValueError
                return result
        except (httpx.HTTPError, ValueError):
            raise CommerceError(
                "WooCommerce read failed; check origin, permissions and plugin availability."
            ) from None

    def list_resources(self, resource, *, page=1, per_page=PAGE_SIZE):
        if resource not in _RESOURCE_LISTS and not re.fullmatch(r"products/\d+/variations", resource):
            raise CommerceError("Unsupported WooCommerce resource.")
        if type(page) is not int or page < 1 or type(per_page) is not int or not 1 <= per_page <= 100:
            raise CommerceError("Invalid WooCommerce pagination.")
        result = self._get(resource, {"page": page, "per_page": per_page})
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise CommerceError("WooCommerce returned an invalid resource list.")
        return result

    def paged(self, resource):
        rows = []
        for page in range(1, MAX_PAGES + 1):
            batch = self.list_resources(resource, page=page, per_page=PAGE_SIZE)
            rows.extend(batch)
            if len(rows) > MAX_ITEMS:
                raise CommerceError("WooCommerce item limit reached; split the migration into smaller runs.")
            if len(batch) < PAGE_SIZE:
                break
        else:
            raise CommerceError(f"WooCommerce {resource} exceeded the {MAX_PAGES}-page import limit.")
        return rows

    def order(self, order_id):
        if type(order_id) is not int or order_id < 1:
            raise CommerceError("Invalid WooCommerce order reference.")
        result = self._get(f"orders/{order_id}")
        if not isinstance(result, dict) or result.get("id") != order_id:
            raise CommerceError("WooCommerce returned an unexpected order.")
        return result

    def connection_status(self):
        self.list_resources("products", per_page=1)
        return {"connected": True, "access": "read-only", "sync_enabled": False,
                "bounded_import_available": True, "checkout_owner": "fastshop-after-import",
                "subscriptions": "not-imported"}

    def create_order(self, *args, **kwargs):
        raise CommerceError("Live WooCommerce writes are not enabled; use the reviewable export bundle.")

    def create_coupon(self, *args, **kwargs):
        raise CommerceError("WooCommerce coupon synchronisation is not implemented.")

    def manage_subscription(self, *args, **kwargs):
        raise CommerceError("WooCommerce Subscriptions requires a selected plugin and a separate adapter.")


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
        mapping = ExternalMapping(tenant_id=site.tenant_id, site_id=site.id, system=SYSTEM,
            resource_type=resource_type, external_id=str(external_id), local_id=local_id)
        db.add(mapping)
    mapping.local_id, mapping.version = local_id, str(version)[:80]
    db.flush()
    return mapping


def _existing_action(db, site, resource_type, external_id) -> str:
    return "update" if _mapping(db, site, resource_type, external_id) else "create"


def _unique_slug(db, site, model, value: str, external_id) -> str:
    if not db.scalar(select(model.id).where(model.tenant_id == site.tenant_id, model.slug == value)):
        return value
    suffix = "-wc-" + str(external_id)
    return (value[:max(1, 100 - len(suffix))].rstrip("-") + suffix)[:100]


def _unique_sku(db, site, value: str, external_id) -> str:
    candidate = value[:100] or ("WC-" + str(external_id))[:100]
    used = db.scalar(select(ProductVariant.id).where(
        ProductVariant.tenant_id == site.tenant_id, ProductVariant.sku == candidate))
    return candidate if not used else (candidate[:80] + "-WC-" + str(external_id))[:100]


def _normalize_category(item: dict) -> dict:
    external_id = item.get("id")
    if type(external_id) is not int or external_id < 1:
        raise CommerceError("WooCommerce returned a category without a valid id.")
    name = _plain(item.get("name"), 160)
    if not name:
        raise CommerceError("WooCommerce returned a category without a name.")
    return {"external_id": str(external_id), "name": name,
            "slug": _slug(item.get("slug"), "category-" + str(external_id)),
            "description": _plain(item.get("description")),
            "parent_external_id": str(item.get("parent")) if item.get("parent") else "",
            "version": _version(item)}


def _normalize_variant(item: dict, product: dict, currency: str, scale: int) -> dict:
    external_id = item.get("id")
    if external_id is None:
        external_id = f"{product['id']}:default"
    name = _plain(item.get("name") or product.get("name") or "Default", 180) or "Default"
    price = str(item.get("regular_price") or item.get("price") or product.get("regular_price")
                or product.get("price") or "").strip()
    price_minor = money_minor(price, currency=currency, scale=scale) if price else None
    return {"external_id": str(external_id), "name": name,
            "sku": _plain(item.get("sku") or product.get("sku"), 100),
            "price_minor": price_minor, "version": _version(item)}


def _normalize_product(item: dict, variations: list[dict], currency: str, scale: int) -> dict:
    external_id = item.get("id")
    if type(external_id) is not int or external_id < 1:
        raise CommerceError("WooCommerce returned a product without a valid id.")
    name = _plain(item.get("name"), 220)
    if not name:
        raise CommerceError("WooCommerce returned a product without a name.")
    images = item.get("images") if isinstance(item.get("images"), list) else []
    image_url = content.safe_url(str(images[0].get("src", "")), media=True) if images and isinstance(images[0], dict) else ""
    category_ids = [str(row["id"]) for row in item.get("categories", [])
                    if isinstance(row, dict) and type(row.get("id")) is int]
    raw_variants = variations if variations else [{}]
    return {"external_id": str(external_id), "name": name,
            "slug": _slug(item.get("slug"), "product-" + str(external_id)),
            "subtitle": _plain(item.get("short_description"), 260),
            "description": _plain(item.get("description")), "image_url": image_url,
            "category_external_ids": category_ids, "published": item.get("status") == "publish",
            "variants": [_normalize_variant(row, item, currency, scale) for row in raw_variants],
            "version": _version(item)}


def _normalize_customer(item: dict) -> dict:
    external_id = item.get("id")
    if type(external_id) is not int or external_id < 1:
        raise CommerceError("WooCommerce returned a customer without a valid id.")
    email = normalized_email(item.get("email", ""))
    name = _plain(" ".join(filter(None, [item.get("first_name"), item.get("last_name")])), 160)
    return {"external_id": str(external_id), "email": email, "name": name,
            "version": _version(item)}


def _currency_scale(currency: str, configured_currency: str, configured_scale: int) -> int:
    return configured_scale if currency == configured_currency else _CURRENCY_SCALES.get(currency, 2)


def _normalize_order(item: dict, configured_currency: str, configured_scale: int) -> dict:
    external_id = item.get("id")
    if type(external_id) is not int or external_id < 1:
        raise CommerceError("WooCommerce returned an order without a valid id.")
    currency = str(item.get("currency") or configured_currency).upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise CommerceError("WooCommerce returned an order with an invalid currency.")
    scale = _currency_scale(currency, configured_currency, configured_scale)
    billing = item.get("billing") if isinstance(item.get("billing"), dict) else {}
    shipping = item.get("shipping") if isinstance(item.get("shipping"), dict) else {}
    email = normalized_email(billing.get("email", ""))
    name = _plain(" ".join(filter(None, [billing.get("first_name"), billing.get("last_name")])), 160)
    lines = []
    for raw in item.get("line_items", []):
        if not isinstance(raw, dict) or type(raw.get("quantity")) is not int or raw["quantity"] < 1:
            raise CommerceError("WooCommerce returned an invalid order line.")
        total_minor = money_minor(str(raw.get("total", "")), currency=currency, scale=scale)
        subtotal_minor = money_minor(str(raw.get("subtotal", raw.get("total", ""))), currency=currency, scale=scale)
        quantity = raw["quantity"]
        unit_minor = int((Decimal(subtotal_minor) / quantity).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        product_id = str(raw.get("product_id") or "")
        lines.append({"product_external_id": product_id,
                      "variant_external_id": str(raw.get("variation_id") or f"{product_id}:default"),
                      "name": _plain(raw.get("name"), 220) or "Imported item",
                      "sku": _plain(raw.get("sku"), 100), "quantity": quantity,
                      "unit_price_minor": unit_minor, "subtotal_minor": subtotal_minor,
                      "total_minor": total_minor})
    status = str(item.get("status", "pending"))
    payment_status = "paid" if status in {"processing", "completed"} else "pending"
    order_status = "fulfilled" if status == "completed" else ("cancelled" if status in {"cancelled", "refunded", "failed"} else "unfulfilled")
    return {"external_id": str(external_id), "number": _plain(item.get("number") or external_id, 40),
            "email": email, "customer_name": name, "currency": currency, "currency_scale": scale,
            "status": order_status, "payment_status": payment_status,
            "subtotal_minor": sum(line["subtotal_minor"] for line in lines),
            "discount_minor": money_minor(str(item.get("discount_total", "0")), currency=currency, scale=scale),
            "shipping_minor": money_minor(str(item.get("shipping_total", "0")), currency=currency, scale=scale),
            "tax_minor": money_minor(str(item.get("total_tax", "0")), currency=currency, scale=scale),
            "total_minor": money_minor(str(item.get("total", "0")), currency=currency, scale=scale),
            "shipping_address": {key: _plain(shipping.get(key), 200) for key in
                ("first_name", "last_name", "company", "address_1", "address_2", "city", "state", "postcode", "country")},
            "lines": lines, "version": _version(item)}


class WooCommerceConnector:
    platform = SYSTEM
    label = "WooCommerce"
    capabilities = ConnectorCapabilities(
        imports=("categories", "products", "customers", "orders"),
        exports=("categories", "products"), max_pages=MAX_PAGES,
        max_items=MAX_ITEMS, timeout_seconds=TIMEOUT_SECONDS,
    )

    def credential_state(self, site) -> CredentialState:
        config = _config(site, allow_incomplete=True)
        prefix = _prefix(site)
        fixture_ready = config.fixture is not None and config.fixture.is_file()
        enabled = os.getenv(prefix + "ENABLED", "").lower() == "true"
        configured = fixture_ready or (enabled and _valid_origin(config.origin)
            and config.key.startswith("ck_") and config.secret.startswith("cs_")
            and bool(re.fullmatch(r"[A-Z]{3}", config.currency)) and 0 <= config.scale <= 4)
        message = ("Development fixture store ready." if fixture_ready else
                   "Operator credentials are configured." if configured else
                   "Ask the operator to configure this site's WooCommerce connection.")
        return CredentialState(configured, message, "fixture" if fixture_ready else "sandbox")

    def fetch(self, site, *, transport=None) -> dict:
        gateway = WooCommerceGateway(site, tenant_id=site.tenant_id, transport=transport)
        categories, products = gateway.paged("categories"), gateway.paged("products")
        customers, orders = gateway.paged("customers"), gateway.paged("orders")
        variations = {}
        for product in products:
            if product.get("type") == "variable":
                variations[str(product.get("id"))] = gateway.paged(f"products/{product.get('id')}/variations")
        total = len(categories) + len(products) + len(customers) + len(orders) + sum(map(len, variations.values()))
        if total > MAX_ITEMS:
            raise CommerceError(f"WooCommerce returned more than the {MAX_ITEMS}-item run limit.")
        return {"categories": categories, "products": products, "customers": customers,
                "orders": orders, "variations": variations, "currency": gateway.currency,
                "currency_scale": gateway.scale}

    def dry_run(self, db, site, user_id, *, transport=None):
        raw = self.fetch(site, transport=transport)
        warnings, unmapped = [], []
        categories = [_normalize_category(item) for item in raw["categories"]]
        products = [_normalize_product(item, raw["variations"].get(str(item["id"]), []),
                     raw["currency"], raw["currency_scale"]) for item in raw["products"]]
        customers = []
        for item in raw["customers"]:
            try:
                customers.append(_normalize_customer(item))
            except CommerceError:
                unmapped.append({"type": "customer", "external_id": str(item.get("id", "")),
                                 "reason": "missing or invalid email"})
        orders = []
        for item in raw["orders"]:
            try:
                orders.append(_normalize_order(item, raw["currency"], raw["currency_scale"]))
            except CommerceError as exc:
                unmapped.append({"type": "order", "external_id": str(item.get("id", "")),
                                 "reason": str(exc)})

        known_categories = {row["external_id"] for row in categories}
        known_products = {row["external_id"] for row in products}
        known_variants = {variant["external_id"] for row in products for variant in row["variants"]}
        for product in products:
            missing = [value for value in product["category_external_ids"]
                       if value not in known_categories and not _mapping(db, site, "category", value)]
            if missing:
                unmapped.append({"type": "product", "external_id": product["external_id"],
                                 "reason": "category ids not fetched: " + ", ".join(missing)})
        for order in orders:
            for line in order["lines"]:
                product_ok = line["product_external_id"] in known_products or _mapping(db, site, "product", line["product_external_id"])
                variant_ok = line["variant_external_id"] in known_variants or _mapping(db, site, "variant", line["variant_external_id"])
                if not product_ok or not variant_ok:
                    unmapped.append({"type": "order_line", "external_id": order["external_id"],
                                     "reason": f"catalog mapping unavailable for {line['name']}"})
        if any(order["currency"] != raw["currency"] for order in orders):
            warnings.append("Orders in other currencies retain their own ISO minor-unit scale.")
        if unmapped:
            warnings.append("Unmapped order lines keep snapshots but do not attach to a FastShop variant.")

        resources = {"categories": categories, "products": products,
                     "customers": customers, "orders": orders}
        counts, samples = {}, []
        singular = {"categories": "category", "products": "product",
                    "customers": "customer", "orders": "order"}
        for name, rows in resources.items():
            actions = [_existing_action(db, site, singular[name], row["external_id"]) for row in rows]
            counts[name] = {"fetched": len(raw[name]), "create": actions.count("create"),
                            "update": actions.count("update"),
                            "skip": len(raw[name]) - len(rows)}
            for row, action in list(zip(rows, actions, strict=True))[:3]:
                samples.append({"type": singular[name], "external_id": row["external_id"],
                    "label": row.get("name") or row.get("email") or row.get("number"), "action": action})
        payload = {"schema_version": PLAN_VERSION, "platform": SYSTEM,
                   "currency": raw["currency"], "currency_scale": raw["currency_scale"], **resources}
        report = {"platform": SYSTEM, "mode": "dry-run", "counts": counts,
                  "samples": samples, "unmapped_items": unmapped[:100], "warnings": warnings,
                  "limits": {"max_pages": MAX_PAGES, "max_items": MAX_ITEMS,
                             "timeout_seconds": TIMEOUT_SECONDS}}
        return payload, report

    @staticmethod
    def _validate_payload(payload):
        if (not isinstance(payload, dict) or payload.get("schema_version") != PLAN_VERSION
                or payload.get("platform") != SYSTEM):
            raise CommerceError("The reviewed WooCommerce plan is invalid.")
        total = 0
        for key in ("categories", "products", "customers", "orders"):
            if not isinstance(payload.get(key), list):
                raise CommerceError("The reviewed WooCommerce plan is incomplete.")
            total += len(payload[key])
        if total > MAX_ITEMS:
            raise CommerceError("The reviewed WooCommerce plan exceeds the item limit.")

    def apply_import(self, db, site, user_id, payload):
        self._validate_payload(payload)
        counts = {name: {"created": 0, "updated": 0} for name in
                  ("categories", "products", "customers", "orders")}
        fallback_category = None
        for row in payload["categories"]:
            mapping = _mapping(db, site, "category", row["external_id"])
            category = db.scalar(select(Category).where(Category.id == mapping.local_id,
                Category.tenant_id == site.tenant_id)) if mapping else None
            action = "updated" if category else "created"
            if category is None:
                category = Category(tenant_id=site.tenant_id,
                    slug=_unique_slug(db, site, Category, row["slug"], row["external_id"]), name=row["name"])
                db.add(category)
                db.flush()
            category.name, category.description = row["name"], row["description"]
            _map(db, site, "category", row["external_id"], category.id, row["version"])
            counts["categories"][action] += 1
        for row in payload["categories"]:
            if row["parent_external_id"]:
                mapping = _mapping(db, site, "category", row["external_id"])
                parent = _mapping(db, site, "category", row["parent_external_id"])
                if mapping and parent and mapping.local_id != parent.local_id:
                    category = db.scalar(select(Category).where(Category.id == mapping.local_id,
                        Category.tenant_id == site.tenant_id))
                    if category:
                        category.parent_id = parent.local_id

        product_type = db.scalar(select(ProductType).where(
            ProductType.tenant_id == site.tenant_id, ProductType.slug == "physical"))
        if product_type is None:
            product_type = ProductType(tenant_id=site.tenant_id, slug="physical", name="Physical product")
            db.add(product_type)
            db.flush()
        for row in payload["products"]:
            category = None
            for external_id in row["category_external_ids"]:
                mapping = _mapping(db, site, "category", external_id)
                if mapping:
                    category = db.scalar(select(Category).where(Category.id == mapping.local_id,
                        Category.tenant_id == site.tenant_id))
                    if category:
                        break
            if category is None:
                if fallback_category is None:
                    fallback_category = db.scalar(select(Category).where(
                        Category.tenant_id == site.tenant_id, Category.slug == "woocommerce-import"))
                    if fallback_category is None:
                        fallback_category = Category(tenant_id=site.tenant_id,
                            slug="woocommerce-import", name="WooCommerce import")
                        db.add(fallback_category)
                        db.flush()
                category = fallback_category
            mapping = _mapping(db, site, "product", row["external_id"])
            product = db.scalar(select(Product).where(Product.id == mapping.local_id,
                Product.tenant_id == site.tenant_id)) if mapping else None
            action = "updated" if product else "created"
            if product is None:
                product = Product(tenant_id=site.tenant_id, product_type_id=product_type.id,
                    category_id=category.id,
                    slug=_unique_slug(db, site, Product, row["slug"], row["external_id"]), name=row["name"])
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
                    ProductVariant.product_id == product.id)) if variant_mapping else None
                if variant is None:
                    variant = ProductVariant(tenant_id=site.tenant_id, product_id=product.id,
                        sku=_unique_sku(db, site, variant_row["sku"], variant_row["external_id"]),
                        name=variant_row["name"], sort_order=index)
                    db.add(variant)
                    db.flush()
                variant.name, variant.sort_order, variant.is_active = variant_row["name"], index, True
                _map(db, site, "variant", variant_row["external_id"], variant.id, variant_row["version"])
                listing = db.scalar(select(VariantChannelListing).where(
                    VariantChannelListing.variant_id == variant.id,
                    VariantChannelListing.channel_id == site.channel_id))
                if variant_row["price_minor"] is None:
                    if listing:
                        db.delete(listing)
                elif listing:
                    listing.currency, listing.price_minor = payload["currency"], variant_row["price_minor"]
                else:
                    db.add(VariantChannelListing(variant_id=variant.id, channel_id=site.channel_id,
                        currency=payload["currency"], price_minor=variant_row["price_minor"]))
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
            order = db.scalar(select(Order).where(Order.id == mapping.local_id,
                Order.tenant_id == site.tenant_id)) if mapping else None
            action = "updated" if order else "created"
            if order is None:
                number = ("WC-" + row["number"])[:40]
                if db.scalar(select(Order.id).where(Order.tenant_id == site.tenant_id, Order.number == number)):
                    number = ("WC-" + row["external_id"])[:40]
                order = Order(tenant_id=site.tenant_id, channel_id=site.channel_id,
                    number=number, idempotency_key=("woo:" + site.id + ":" + row["external_id"])[:80],
                    email=row["email"], currency=row["currency"], subtotal_minor=0, total_minor=0)
                db.add(order)
                db.flush()
            order.email, order.status, order.payment_status = row["email"], row["status"], row["payment_status"]
            order.currency, order.subtotal_minor = row["currency"], row["subtotal_minor"]
            order.discount_minor, order.shipping_minor = row["discount_minor"], row["shipping_minor"]
            order.tax_minor, order.total_minor = row["tax_minor"], row["total_minor"]
            order.shipping_address_json = row["shipping_address"]
            db.execute(delete(OrderLine).where(OrderLine.order_id == order.id))
            for line in row["lines"]:
                variant_mapping = _mapping(db, site, "variant", line["variant_external_id"])
                variant = db.scalar(select(ProductVariant).where(
                    ProductVariant.id == variant_mapping.local_id,
                    ProductVariant.tenant_id == site.tenant_id)) if variant_mapping else None
                db.add(OrderLine(order_id=order.id, variant_id=variant.id if variant else None,
                    sku=line["sku"], product_name=line["name"], variant_name=variant.name if variant else "",
                    quantity=line["quantity"], unit_price_minor=line["unit_price_minor"],
                    total_minor=line["total_minor"]))
            link = db.scalar(select(SiteOrder).where(SiteOrder.order_id == order.id,
                SiteOrder.tenant_id == site.tenant_id, SiteOrder.site_id == site.id))
            if link is None:
                db.add(SiteOrder(tenant_id=site.tenant_id, site_id=site.id,
                    order_id=order.id, customer_id=customer.id))
            else:
                link.customer_id = customer.id
            _map(db, site, "order", row["external_id"], order.id, row["version"])
            counts["orders"][action] += 1
        db.flush()
        return counts

    def export_bundle(self, db, site):
        channel = db.scalar(select(Channel).where(
            Channel.id == site.channel_id, Channel.tenant_id == site.tenant_id))
        currency = channel.currency if channel else "USD"
        config = _config(site, allow_incomplete=True)
        scale = config.scale if config.currency == currency and 0 <= config.scale <= 4 else _CURRENCY_SCALES.get(currency, 2)
        categories = list(db.scalars(select(Category).where(
            Category.tenant_id == site.tenant_id).order_by(Category.sort_order, Category.id)))
        category_payloads = []
        for category in categories:
            mapping = db.scalar(select(ExternalMapping).where(ExternalMapping.tenant_id == site.tenant_id,
                ExternalMapping.site_id == site.id, ExternalMapping.system == SYSTEM,
                ExternalMapping.resource_type == "category", ExternalMapping.local_id == category.id))
            payload = {"name": category.name, "slug": category.slug, "description": category.description}
            if category.parent_id:
                parent = db.scalar(select(ExternalMapping).where(ExternalMapping.tenant_id == site.tenant_id,
                    ExternalMapping.site_id == site.id, ExternalMapping.system == SYSTEM,
                    ExternalMapping.resource_type == "category", ExternalMapping.local_id == category.parent_id))
                if parent and parent.external_id.isdigit():
                    payload["parent"] = int(parent.external_id)
            category_payloads.append({"local_id": category.id,
                "external_id": mapping.external_id if mapping else None, "payload": payload})
        products = list(db.scalars(select(Product).where(
            Product.tenant_id == site.tenant_id).order_by(Product.id)))
        product_payloads = []
        for product in products:
            variants = list(db.scalars(select(ProductVariant).where(
                ProductVariant.tenant_id == site.tenant_id,
                ProductVariant.product_id == product.id).order_by(ProductVariant.sort_order)))
            variant_payloads = []
            for variant in variants:
                listing = db.scalar(select(VariantChannelListing).where(
                    VariantChannelListing.variant_id == variant.id,
                    VariantChannelListing.channel_id == site.channel_id))
                values = {"sku": variant.sku, "description": variant.name}
                if listing:
                    values["regular_price"] = decimal_amount(listing.price_minor, scale)
                variant_payloads.append(values)
            category_mapping = db.scalar(select(ExternalMapping).where(
                ExternalMapping.tenant_id == site.tenant_id, ExternalMapping.site_id == site.id,
                ExternalMapping.system == SYSTEM, ExternalMapping.resource_type == "category",
                ExternalMapping.local_id == product.category_id))
            category_ref = ({"id": int(category_mapping.external_id)}
                if category_mapping and category_mapping.external_id.isdigit() else
                {"name": product.category.name, "slug": product.category.slug})
            payload = {"name": product.name, "slug": product.slug,
                "status": "publish" if product.is_published else "draft",
                "description": product.description, "short_description": product.subtitle,
                "type": "variable" if len(variant_payloads) > 1 else "simple",
                "categories": [category_ref],
                "images": [{"src": product.image_url}] if product.image_url else []}
            if len(variant_payloads) == 1:
                payload.update(variant_payloads[0])
            else:
                payload["variations"] = variant_payloads
            mapping = db.scalar(select(ExternalMapping).where(ExternalMapping.tenant_id == site.tenant_id,
                ExternalMapping.site_id == site.id, ExternalMapping.system == SYSTEM,
                ExternalMapping.resource_type == "product", ExternalMapping.local_id == product.id))
            product_payloads.append({"local_id": product.id,
                "external_id": mapping.external_id if mapping else None, "payload": payload})
        return {"schema_version": PLAN_VERSION, "platform": SYSTEM, "mode": "review-only",
                "currency": currency, "currency_scale": scale,
                "categories": category_payloads, "products": product_payloads}


def verify_webhook(payload: bytes, signature: str, secret: str):
    """Verify raw Woo webhook HMAC; no ingestion route is exposed in this slice."""
    if not secret or not signature or len(payload) > 2_000_000:
        raise CommerceError("Invalid WooCommerce webhook.")
    expected = base64.b64encode(hmac.new(secret.encode(), payload, hashlib.sha256).digest()).decode()
    try:
        if not hmac.compare_digest(expected, signature):
            raise ValueError
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (TypeError, ValueError, UnicodeError):
        raise CommerceError("Invalid WooCommerce webhook.") from None
