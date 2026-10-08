"""Bounded CSV catalog migration and Google Merchant Center feed export."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from xml.dom import minidom

from sqlalchemy import func, select

from app import content
from app.connectors import (
    ConnectorCapabilities,
    CredentialState,
    ExportArtifact,
)
from app.models import (
    Category,
    Channel,
    ExternalMapping,
    Product,
    ProductType,
    ProductVariant,
    SitePage,
    Stock,
    VariantChannelListing,
    Warehouse,
)
from app.services import CommerceError

SYSTEM = "csv"
PLAN_VERSION = 1
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 750
MAX_COLUMNS = 32
MAX_CELL_CHARS = 20_000
MAX_ROW_CHARS = 65_536
MAX_IMAGES = 10
MAX_FEED_ITEMS = 750
MAX_PRICE_MINOR = 100_000_000
MAX_STOCK = 10_000_000
GOOGLE_NAMESPACE = "http://base.google.com/ns/1.0"

_ZERO_DECIMAL = {
    "BIF",
    "CLP",
    "DJF",
    "GNF",
    "JPY",
    "KMF",
    "KRW",
    "PYG",
    "RWF",
    "UGX",
    "VND",
    "VUV",
    "XAF",
    "XOF",
    "XPF",
}
_THREE_DECIMAL = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}
_HEADER_ALIASES = {
    "name": {"name", "product_name", "title"},
    "sku": {"sku", "variant_sku"},
    "description": {"description", "product_description", "body"},
    "category": {"category", "category_name", "collection"},
    "stock": {"stock", "inventory", "quantity"},
    "variant_name": {"variant_name", "variant"},
    "variant_options": {"variant_options", "options"},
    "image_urls": {"image_url", "image", "image_urls", "images"},
    "currency": {"currency", "currency_code"},
}


@dataclass
class _ParsedRow:
    row_number: int
    values: dict
    errors: list[str]
    external_id: str = ""


def _currency_scale(currency: str) -> int:
    if currency in _ZERO_DECIMAL:
        return 0
    if currency in _THREE_DECIMAL:
        return 3
    return 2


def _header(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.strip().lower())).strip("_")


def _plain(value: str, limit: int) -> str:
    value = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(value) > limit:
        raise CommerceError(f"must be at most {limit} characters")
    return value


def _slug(value: str, fallback: str = "item") -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:100] or fallback


def _parse_price(value: str, *, minor_units: bool, currency: str) -> int:
    if not isinstance(value, str):
        raise CommerceError("price must be supplied as a string, not a floating-point value")
    value = value.strip()
    if minor_units:
        if not re.fullmatch(r"\d+", value):
            raise CommerceError("price_minor must be an integer minor-unit string")
        amount = int(value)
    else:
        if not re.fullmatch(r"\d+(?:\.\d+)?", value):
            raise CommerceError("price must be a plain non-negative decimal string")
        try:
            decimal = Decimal(value)
        except InvalidOperation as exc:
            raise CommerceError("price must be a plain decimal string") from exc
        amount = int(
            (decimal * (10 ** _currency_scale(currency))).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    if not 1 <= amount <= MAX_PRICE_MINOR:
        raise CommerceError(f"price must resolve to 1–{MAX_PRICE_MINOR} minor units")
    return amount


def _parse_options(value: str) -> dict[str, str]:
    if not value.strip():
        return {}
    result = {}
    for item in value.split("|"):
        if "=" not in item:
            raise CommerceError("variant_options must use Name=Value pairs separated by |")
        name, option = (_plain(part, 100) for part in item.split("=", 1))
        if not name or not option:
            raise CommerceError("variant_options names and values cannot be blank")
        if name in result:
            raise CommerceError(f"variant option {name!r} is duplicated")
        result[name] = option
    if len(result) > 10:
        raise CommerceError("variant_options is limited to 10 pairs")
    return result


def _mapping(db, site, resource_type: str, external_id: str):
    return db.scalar(
        select(ExternalMapping).where(
            ExternalMapping.tenant_id == site.tenant_id,
            ExternalMapping.site_id == site.id,
            ExternalMapping.system == SYSTEM,
            ExternalMapping.resource_type == resource_type,
            ExternalMapping.external_id == external_id,
        )
    )


def _map(db, site, resource_type: str, external_id: str, local_id: str, version: str):
    mapping = _mapping(db, site, resource_type, external_id)
    if mapping is None:
        mapping = ExternalMapping(
            tenant_id=site.tenant_id,
            site_id=site.id,
            system=SYSTEM,
            resource_type=resource_type,
            external_id=external_id,
            local_id=local_id,
            version=version,
        )
        db.add(mapping)
    else:
        mapping.local_id = local_id
        mapping.version = version
    return mapping


def _unique_slug(db, site, name: str, external_id: str, *, exclude_id: str = "") -> str:
    base = _slug(name, "csv-product")
    candidate = base
    used = db.scalar(
        select(Product.id).where(
            Product.tenant_id == site.tenant_id,
            Product.slug == candidate,
            Product.id != exclude_id,
        )
    )
    if used:
        candidate = (
            base[:90].rstrip("-") + "-csv-" + hashlib.sha256(external_id.encode()).hexdigest()[:8]
        )
    return candidate


def _unique_sku(db, site, sku: str, external_id: str, *, exclude_id: str = "") -> str:
    base = sku or ("CSV-" + hashlib.sha256(external_id.encode()).hexdigest()[:12].upper())
    candidate = base[:100]
    used = db.scalar(
        select(ProductVariant.id).where(
            ProductVariant.tenant_id == site.tenant_id,
            ProductVariant.sku == candidate,
            ProductVariant.id != exclude_id,
        )
    )
    if used:
        suffix = "-CSV-" + hashlib.sha256(external_id.encode()).hexdigest()[:8].upper()
        candidate = base[: 100 - len(suffix)] + suffix
    return candidate


def _price_column(headers: list[str]) -> tuple[str, str | None, bool]:
    found = []
    for name in headers:
        if name == "price":
            found.append((name, None, False))
        elif name == "price_minor":
            found.append((name, None, True))
        elif match := re.fullmatch(r"price_([a-z]{3})(?:_(minor))?", name):
            found.append((name, match.group(1).upper(), bool(match.group(2))))
    if len(found) != 1:
        raise CommerceError(
            "CSV headers must contain exactly one price column: price, price_minor, price_<currency>, or price_<currency>_minor."
        )
    return found[0]


def _column_map(
    headers: list[str],
) -> tuple[dict[str, int], tuple[str, str | None, bool], list[str]]:
    columns = {}
    recognized = set()
    for canonical, aliases in _HEADER_ALIASES.items():
        matches = [index for index, name in enumerate(headers) if name in aliases]
        if len(matches) > 1:
            raise CommerceError(f"CSV headers contain ambiguous columns for {canonical}.")
        if matches:
            columns[canonical] = matches[0]
            recognized.add(headers[matches[0]])
    price = _price_column(headers)
    columns["price"] = headers.index(price[0])
    recognized.add(price[0])
    for required in ("name", "category"):
        if required not in columns:
            raise CommerceError(f"CSV headers must include {required}.")
    unknown = [name for name in headers if name not in recognized]
    return columns, price, unknown


def _cell(row: list[str], columns: dict[str, int], name: str) -> str:
    index = columns.get(name)
    return row[index] if index is not None and index < len(row) else ""


def parse_catalog_csv(db, site, source: bytes) -> tuple[dict, dict]:
    if not isinstance(source, bytes):
        raise CommerceError("Choose a UTF-8 CSV file to preview.")
    if not source:
        raise CommerceError("The uploaded CSV file is empty.")
    if len(source) > MAX_FILE_BYTES:
        raise CommerceError(f"CSV uploads are limited to {MAX_FILE_BYTES} bytes.")
    try:
        text = source.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CommerceError(
            "CSV uploads must be valid UTF-8 (an optional UTF-8 BOM is accepted)."
        ) from exc
    if "\x00" in text:
        raise CommerceError("CSV uploads cannot contain NUL bytes.")

    channel = db.scalar(
        select(Channel).where(
            Channel.id == site.channel_id,
            Channel.tenant_id == site.tenant_id,
        )
    )
    if channel is None or not re.fullmatch(r"[A-Z]{3}", channel.currency or ""):
        raise CommerceError(
            "Configure a three-letter currency on the target site's channel before importing CSV."
        )
    default_currency = channel.currency
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        raw_headers = next(reader)
        if not raw_headers:
            raise CommerceError("CSV uploads must contain a header row.")
        if len(raw_headers) > MAX_COLUMNS:
            raise CommerceError(f"CSV uploads are limited to {MAX_COLUMNS} columns.")
        if any(len(value) > MAX_CELL_CHARS for value in raw_headers):
            raise CommerceError(
                f"CSV header cells are limited to {MAX_CELL_CHARS} characters."
            )
        headers = [_header(value) for value in raw_headers]
        if any(not name for name in headers) or len(set(headers)) != len(headers):
            raise CommerceError("CSV headers must be named and unique after normalization.")
        columns, price_column, unknown = _column_map(headers)
        raw_rows = list(reader)
    except csv.Error as exc:
        raise CommerceError(f"CSV syntax error: {exc}.") from exc
    except StopIteration as exc:
        raise CommerceError("CSV uploads must contain a header row.") from exc
    if len(raw_rows) > MAX_ROWS:
        raise CommerceError(f"CSV uploads are limited to {MAX_ROWS} data rows.")
    if not raw_rows:
        raise CommerceError("CSV uploads must contain at least one data row.")

    parsed = []
    for row_number, row in enumerate(raw_rows, 2):
        errors = []
        if len(row) != len(headers):
            errors.append(f"expected {len(headers)} columns but found {len(row)}")
        for index, value in enumerate(row[: len(headers)]):
            if len(value) > MAX_CELL_CHARS:
                errors.append(f"{headers[index]} exceeds the {MAX_CELL_CHARS}-character cell limit")
        if sum(len(value) for value in row) > MAX_ROW_CHARS:
            errors.append(f"row exceeds the {MAX_ROW_CHARS}-character limit")
        if not row or not any(value.strip() for value in row):
            errors.append("row is blank")

        values = {}
        try:
            name = _plain(_cell(row, columns, "name"), 220)
            if not name:
                raise CommerceError("name is required")
            category = _plain(_cell(row, columns, "category"), 160)
            if not category:
                raise CommerceError("category is required")
            sku = _plain(_cell(row, columns, "sku"), 100)
            description = _plain(_cell(row, columns, "description"), MAX_CELL_CHARS)
            variant_name = _plain(_cell(row, columns, "variant_name"), 180)
            options = _parse_options(_cell(row, columns, "variant_options"))

            price_header, encoded_currency, minor_units = price_column
            currency_value = _plain(_cell(row, columns, "currency"), 3).upper()
            if currency_value and not re.fullmatch(r"[A-Z]{3}", currency_value):
                raise CommerceError("currency must be a three-letter ISO code")
            if encoded_currency and currency_value and encoded_currency != currency_value:
                raise CommerceError(
                    f"currency {currency_value} conflicts with the {price_header} header"
                )
            currency = encoded_currency or currency_value or default_currency
            if currency != default_currency:
                raise CommerceError(
                    f"currency {currency} does not match target channel currency {default_currency}; currency conversion is not performed"
                )
            price_minor = _parse_price(
                _cell(row, columns, "price"), minor_units=minor_units, currency=currency
            )

            stock = None
            if "stock" in columns:
                stock_value = _cell(row, columns, "stock").strip()
                if not re.fullmatch(r"\d+", stock_value):
                    raise CommerceError(
                        "stock must be a non-negative integer when the stock column is present"
                    )
                stock = int(stock_value)
                if stock > MAX_STOCK:
                    raise CommerceError(f"stock is limited to {MAX_STOCK}")

            image_urls = []
            image_value = _cell(row, columns, "image_urls")
            if image_value.strip():
                for raw_url in (
                    image_value.replace("\r\n", "\n")
                    .replace("\r", "\n")
                    .replace("\n", "|")
                    .split("|")
                ):
                    if not raw_url.strip():
                        continue
                    image_urls.append(content.safe_url(raw_url.strip(), media=True))
                if len(image_urls) > MAX_IMAGES:
                    raise CommerceError(f"image_urls is limited to {MAX_IMAGES} URLs")
                content.validate_media_ownership(db, site, {"images": image_urls})

            values = {
                "name": name,
                "sku": sku,
                "description": description,
                "category": category,
                "price_minor": price_minor,
                "currency": currency,
                "stock": stock,
                "variant_name": variant_name
                or (" / ".join(options.values()) if options else "Default"),
                "variant_options": options,
                "image_urls": image_urls,
            }
        except CommerceError as exc:
            errors.append(str(exc))

        external_id = ""
        if values:
            normalized = json.dumps(
                values, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            external_id = "sku:" + values["sku"].casefold() if values["sku"] else "sha256:" + digest
            values["version"] = digest
        parsed.append(_ParsedRow(row_number, values, errors, external_id))

    identities = {}
    for row in parsed:
        if row.external_id:
            identities.setdefault(row.external_id, []).append(row)
    for matches in identities.values():
        if len(matches) > 1:
            numbers = ", ".join(str(row.row_number) for row in matches)
            for row in matches:
                row.errors.append(f"duplicate CSV row identity also appears on rows {numbers}")

    payload_rows = []
    report_rows = []
    samples = []
    unmapped = []
    creates = updates = skips = 0
    for row in parsed:
        if not row.errors and row.values["stock"] is not None:
            variant_mapping = _mapping(db, site, "row_variant", row.external_id)
            warehouse = _warehouse(db, site, create=False)
            existing_stock = (
                db.scalar(
                    select(Stock).where(
                        Stock.warehouse_id == warehouse.id,
                        Stock.variant_id == variant_mapping.local_id,
                    )
                )
                if warehouse and variant_mapping
                else None
            )
            if existing_stock and row.values["stock"] < existing_stock.allocated:
                row.errors.append(
                    f"stock cannot be lower than the allocated quantity {existing_stock.allocated}"
                )
        if row.errors:
            skips += 1
            status = "error"
            unmapped.append(
                {
                    "type": "row",
                    "external_id": str(row.row_number),
                    "reason": "; ".join(row.errors),
                }
            )
        else:
            mapping = _mapping(db, site, "row", row.external_id)
            product = (
                db.scalar(
                    select(Product).where(
                        Product.id == mapping.local_id,
                        Product.tenant_id == site.tenant_id,
                    )
                )
                if mapping
                else None
            )
            status = "update" if product else "create"
            creates += status == "create"
            updates += status == "update"
            payload_rows.append(
                {
                    "row_number": row.row_number,
                    "external_id": row.external_id,
                    **row.values,
                }
            )
            if len(samples) < 12:
                samples.append(
                    {
                        "action": status,
                        "type": "product",
                        "label": row.values["name"],
                        "external_id": row.external_id,
                    }
                )
        report_rows.append(
            {
                "row_number": row.row_number,
                "status": status,
                "identity": row.external_id,
                "name": row.values.get("name", ""),
                "errors": row.errors,
            }
        )

    warnings = [
        f"Rows without an explicit currency use the target site's configured {default_currency} channel currency.",
        "The reviewed plan stores normalized rows, not the uploaded file; apply never rereads the upload.",
    ]
    if unknown:
        warnings.append("Ignored unsupported columns: " + ", ".join(unknown) + ".")
    payload = {
        "schema_version": PLAN_VERSION,
        "default_currency": default_currency,
        "rows": payload_rows,
    }
    report = {
        "counts": {
            "products": {
                "fetched": len(parsed),
                "create": creates,
                "update": updates,
                "skip": skips,
            }
        },
        "rows": report_rows,
        "samples": samples,
        "warnings": warnings,
        "unmapped_items": unmapped,
    }
    return payload, report


def _validate_payload(payload: dict):
    if not isinstance(payload, dict) or payload.get("schema_version") != PLAN_VERSION:
        raise CommerceError("The reviewed CSV plan is incomplete.")
    if not re.fullmatch(r"[A-Z]{3}", str(payload.get("default_currency", ""))):
        raise CommerceError("The reviewed CSV plan has an invalid currency.")
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise CommerceError("The reviewed CSV plan exceeds the row limit.")
    required = {
        "row_number",
        "external_id",
        "name",
        "sku",
        "description",
        "category",
        "price_minor",
        "currency",
        "stock",
        "variant_name",
        "variant_options",
        "image_urls",
        "version",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != required:
            raise CommerceError("The reviewed CSV plan contains an invalid row.")
        if not isinstance(row["row_number"], int) or row["row_number"] < 2:
            raise CommerceError("The reviewed CSV plan contains an invalid row number.")
        if not isinstance(row["external_id"], str) or not row["external_id"]:
            raise CommerceError("The reviewed CSV plan contains an invalid row identity.")
        if (
            not isinstance(row["price_minor"], int)
            or not 1 <= row["price_minor"] <= MAX_PRICE_MINOR
        ):
            raise CommerceError("The reviewed CSV plan contains an invalid price.")
        if row["currency"] != payload["default_currency"]:
            raise CommerceError("The reviewed CSV plan mixes currencies.")
        if row["stock"] is not None and (
            not isinstance(row["stock"], int) or not 0 <= row["stock"] <= MAX_STOCK
        ):
            raise CommerceError("The reviewed CSV plan contains invalid stock.")
        if not isinstance(row["image_urls"], list) or len(row["image_urls"]) > MAX_IMAGES:
            raise CommerceError("The reviewed CSV plan contains invalid image URLs.")


def _warehouse(db, site, *, create: bool):
    code = "CSV-" + site.id[:12].upper()
    warehouse = db.scalar(
        select(Warehouse).where(
            Warehouse.tenant_id == site.tenant_id,
            Warehouse.code == code,
        )
    )
    if warehouse is None and create:
        channel = db.scalar(
            select(Channel).where(
                Channel.id == site.channel_id,
                Channel.tenant_id == site.tenant_id,
            )
        )
        warehouse = Warehouse(
            tenant_id=site.tenant_id,
            code=code,
            name=f"CSV import · {site.name}"[:140],
            country_code=(channel.country_code if channel else "EE"),
        )
        db.add(warehouse)
        db.flush()
    return warehouse


def _lock_import_stocks(db, site, rows: list[dict]) -> dict[str, Stock]:
    if not any(row["stock"] is not None for row in rows):
        return {}
    warehouse = _warehouse(db, site, create=False)
    if warehouse is None:
        return {}
    product_ids = []
    for row in rows:
        mapping = _mapping(db, site, "row", row["external_id"])
        if mapping:
            product_ids.append(mapping.local_id)
    if not product_ids:
        return {}
    variants = list(
        db.scalars(
            select(ProductVariant).where(
                ProductVariant.tenant_id == site.tenant_id,
                ProductVariant.product_id.in_(product_ids),
            )
        )
    )
    variant_ids = [variant.id for variant in variants]
    if not variant_ids:
        return {}
    locked = list(
        db.scalars(
            select(Stock)
            .where(
                Stock.warehouse_id == warehouse.id,
                Stock.variant_id.in_(variant_ids),
            )
            .order_by(Stock.id)
            .with_for_update()
        )
    )
    return {stock.variant_id: stock for stock in locked}


def _append_text(document, parent, name: str, value: str):
    node = document.createElement(name)
    node.appendChild(document.createTextNode(str(value)))
    parent.appendChild(node)
    return node


def _site_base(site) -> str:
    return "https://" + site.hostname if site.hostname else f"https://{site.slug}.example.invalid"


def _absolute_url(value: str, base: str) -> str:
    safe = content.safe_url(value, media=True)
    return base + safe if safe.startswith("/") else safe


def _minor_amount(value: int, currency: str) -> str:
    scale = _currency_scale(currency)
    if scale == 0:
        return str(value)
    digits = str(value).zfill(scale + 1)
    return digits[:-scale] + "." + digits[-scale:]


def _feed_rows(db, site) -> list[dict]:
    page_rows = list(
        db.scalars(
            select(SitePage)
            .where(
                SitePage.tenant_id == site.tenant_id,
                SitePage.site_id == site.id,
                SitePage.kind == "product",
                SitePage.product_id.is_not(None),
            )
            .order_by(SitePage.path, SitePage.id)
        )
    )
    page_product_ids = {page.product_id for page in page_rows}
    listing_product_ids = set(
        db.scalars(
            select(ProductVariant.product_id)
            .join(
                VariantChannelListing,
                VariantChannelListing.variant_id == ProductVariant.id,
            )
            .where(
                ProductVariant.tenant_id == site.tenant_id,
                VariantChannelListing.channel_id == site.channel_id,
            )
        )
    )
    product_ids = page_product_ids | listing_product_ids
    if not product_ids:
        return []
    products = list(
        db.scalars(
            select(Product)
            .where(
                Product.tenant_id == site.tenant_id,
                Product.id.in_(product_ids),
            )
            .order_by(Product.id)
        )
    )
    pages = {}
    for page in page_rows:
        if page.published_json is not None:
            pages.setdefault(page.product_id, page)

    result = []
    candidate_count = 0
    base = _site_base(site)
    for product in products:
        variants = list(
            db.scalars(
                select(ProductVariant)
                .where(
                    ProductVariant.tenant_id == site.tenant_id,
                    ProductVariant.product_id == product.id,
                )
                .order_by(ProductVariant.sort_order, ProductVariant.id)
            )
        )
        candidate_count += max(1, len(variants))
        if candidate_count > MAX_FEED_ITEMS:
            raise CommerceError(
                f"Merchant Center exports are limited to {MAX_FEED_ITEMS} product variants."
            )
        if not variants:
            result.append(
                {
                    "product_id": product.id,
                    "variant_id": "",
                    "sku": "",
                    "title": product.name,
                    "status": "error",
                    "errors": ["product has no variants"],
                    "feed": None,
                }
            )
            continue
        for variant in variants:
            errors = []
            page = pages.get(product.id)
            listing = db.scalar(
                select(VariantChannelListing).where(
                    VariantChannelListing.variant_id == variant.id,
                    VariantChannelListing.channel_id == site.channel_id,
                )
            )
            title = product.name.strip()
            description = (product.description or product.subtitle).strip()
            item_id = variant.sku.strip() or variant.id
            if not product.is_published:
                errors.append("product is not published")
            if page is None:
                errors.append("published site product page is missing")
            if not variant.is_active:
                errors.append("variant is inactive")
            if not item_id:
                errors.append("id is missing")
            if not title:
                errors.append("title is missing")
            if not description:
                errors.append("description is missing")
            try:
                image_link = _absolute_url(product.image_url, base) if product.image_url else ""
                if image_link:
                    content.validate_media_ownership(db, site, {"image": product.image_url})
            except CommerceError:
                image_link = ""
            if not image_link:
                errors.append("image_link is missing or invalid")
            if listing is None:
                errors.append("site-channel price is missing")
                price = ""
            elif not re.fullmatch(r"[A-Z]{3}", listing.currency or "") or listing.price_minor <= 0:
                errors.append("site-channel price or currency is invalid")
                price = ""
            else:
                price = f"{_minor_amount(listing.price_minor, listing.currency)} {listing.currency}"
            link = base + page.path if page else ""
            available = (
                db.scalar(
                    select(func.coalesce(func.sum(Stock.quantity - Stock.allocated), 0))
                    .join(
                        Warehouse,
                        Warehouse.id == Stock.warehouse_id,
                    )
                    .where(
                        Stock.variant_id == variant.id,
                        Warehouse.tenant_id == site.tenant_id,
                    )
                )
                or 0
            )
            feed = (
                None
                if errors
                else {
                    "id": item_id,
                    "title": title,
                    "description": description,
                    "link": link,
                    "image_link": image_link,
                    "price": price,
                    "condition": "new",
                    "availability": "in_stock" if available > 0 else "out_of_stock",
                }
            )
            result.append(
                {
                    "product_id": product.id,
                    "variant_id": variant.id,
                    "sku": variant.sku,
                    "title": title,
                    "status": "ready" if feed else "error",
                    "errors": errors,
                    "feed": feed,
                }
            )
    return result


def _feed_artifact(db, site) -> ExportArtifact:
    rows = _feed_rows(db, site)
    document = minidom.Document()
    rss = document.createElement("rss")
    rss.setAttribute("version", "2.0")
    rss.setAttribute("xmlns:g", GOOGLE_NAMESPACE)
    document.appendChild(rss)
    channel = document.createElement("channel")
    rss.appendChild(channel)
    _append_text(document, channel, "title", site.name)
    _append_text(document, channel, "link", _site_base(site))
    _append_text(
        document, channel, "description", f"Google Merchant Center product feed for {site.name}"
    )
    for row in rows:
        if row["feed"] is None:
            continue
        item = document.createElement("item")
        channel.appendChild(item)
        for name in (
            "id",
            "title",
            "description",
            "link",
            "image_link",
            "price",
            "condition",
            "availability",
        ):
            _append_text(document, item, "g:" + name, row["feed"][name])
    return ExportArtifact(
        content=document.toxml(encoding="utf-8"),
        media_type="application/rss+xml",
        filename=f"fastshop-{site.slug}-merchant-center.xml",
    )


def _feed_review_artifact(db, site) -> ExportArtifact:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(("product_id", "variant_id", "sku", "title", "status", "errors"))
    for row in _feed_rows(db, site):
        writer.writerow(
            (
                row["product_id"],
                row["variant_id"],
                row["sku"],
                row["title"],
                row["status"],
                " | ".join(row["errors"]),
            )
        )
    return ExportArtifact(
        content=output.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        filename=f"fastshop-{site.slug}-merchant-center-review.csv",
    )


class CsvImportConnector:
    platform = SYSTEM
    label = "CSV catalog & Merchant Center"
    max_upload_bytes = MAX_FILE_BYTES
    capabilities = ConnectorCapabilities(
        imports=("catalog products, variants, prices, stock, images, and categories",),
        exports=("Google Merchant Center RSS 2.0 feed", "validation report"),
        max_pages=1,
        max_items=MAX_ROWS,
        timeout_seconds=0,
    )

    def credential_state(self, site):
        return CredentialState(
            True,
            "Upload a UTF-8 CSV for a stored dry run. Feed exports read this site's published real catalog and never write remotely.",
            mode="local",
        )

    def fetch(self, site, *, transport=None):
        if not isinstance(transport, bytes):
            raise CommerceError("Choose a UTF-8 CSV file to preview.")
        return {"content": transport}

    def dry_run(self, db, site, user_id, *, transport=None):
        source = self.fetch(site, transport=transport)["content"]
        return parse_catalog_csv(db, site, source)

    def apply_import(self, db, site, user_id, payload):
        _validate_payload(payload)
        rows = sorted(payload["rows"], key=lambda row: row["external_id"])
        locked_stocks = _lock_import_stocks(db, site, rows)
        product_type = db.scalar(
            select(ProductType).where(
                ProductType.tenant_id == site.tenant_id,
                ProductType.slug == "physical",
            )
        )
        if product_type is None:
            product_type = ProductType(
                tenant_id=site.tenant_id, slug="physical", name="Physical product"
            )
            db.add(product_type)
            db.flush()
        warehouse = _warehouse(db, site, create=any(row["stock"] is not None for row in rows))
        counts = {"products": {"created": 0, "updated": 0}}
        for row in rows:
            category_slug = _slug(row["category"], "csv-import")
            category = db.scalar(
                select(Category).where(
                    Category.tenant_id == site.tenant_id,
                    Category.slug == category_slug,
                )
            )
            if category is None:
                category = Category(
                    tenant_id=site.tenant_id,
                    slug=category_slug,
                    name=row["category"],
                )
                db.add(category)
                db.flush()
            mapping = _mapping(db, site, "row", row["external_id"])
            product = (
                db.scalar(
                    select(Product).where(
                        Product.id == mapping.local_id,
                        Product.tenant_id == site.tenant_id,
                    )
                )
                if mapping
                else None
            )
            action = "updated" if product else "created"
            if product is None:
                product = Product(
                    tenant_id=site.tenant_id,
                    product_type_id=product_type.id,
                    category_id=category.id,
                    slug=_unique_slug(db, site, row["name"], row["external_id"]),
                    name=row["name"],
                )
                db.add(product)
                db.flush()
            content.validate_media_ownership(db, site, {"images": row["image_urls"]})
            product.category_id = category.id
            product.name = row["name"]
            product.description = row["description"]
            product.image_url = row["image_urls"][0] if row["image_urls"] else ""
            product.is_published = True
            _map(db, site, "row", row["external_id"], product.id, row["version"])

            variant_mapping = _mapping(db, site, "row_variant", row["external_id"])
            variant = (
                db.scalar(
                    select(ProductVariant).where(
                        ProductVariant.id == variant_mapping.local_id,
                        ProductVariant.tenant_id == site.tenant_id,
                        ProductVariant.product_id == product.id,
                    )
                )
                if variant_mapping
                else None
            )
            if variant is None:
                variant = ProductVariant(
                    tenant_id=site.tenant_id,
                    product_id=product.id,
                    sku=_unique_sku(db, site, row["sku"], row["external_id"]),
                    name=row["variant_name"],
                )
                db.add(variant)
                db.flush()
            else:
                variant.sku = _unique_sku(
                    db, site, row["sku"], row["external_id"], exclude_id=variant.id
                )
            variant.name = row["variant_name"]
            variant.attributes_json = row["variant_options"]
            variant.is_active = True
            _map(db, site, "row_variant", row["external_id"], variant.id, row["version"])

            listing = db.scalar(
                select(VariantChannelListing).where(
                    VariantChannelListing.variant_id == variant.id,
                    VariantChannelListing.channel_id == site.channel_id,
                )
            )
            if listing is None:
                db.add(
                    VariantChannelListing(
                        variant_id=variant.id,
                        channel_id=site.channel_id,
                        currency=row["currency"],
                        price_minor=row["price_minor"],
                    )
                )
            else:
                listing.currency = row["currency"]
                listing.price_minor = row["price_minor"]
            if row["stock"] is not None:
                stock = locked_stocks.get(variant.id)
                if stock is None:
                    stock = db.scalar(
                        select(Stock).where(
                            Stock.warehouse_id == warehouse.id,
                            Stock.variant_id == variant.id,
                        )
                    )
                if stock is None:
                    stock = Stock(
                        warehouse_id=warehouse.id,
                        variant_id=variant.id,
                        quantity=row["stock"],
                        allocated=0,
                    )
                    db.add(stock)
                elif row["stock"] < stock.allocated:
                    raise CommerceError(
                        f"CSV row {row['row_number']} stock cannot be lower than its allocated quantity."
                    )
                else:
                    stock.quantity = row["stock"]
            counts["products"][action] += 1
        db.flush()
        return counts

    def export_bundle(self, db, site):
        return {"format": "RSS 2.0", "download": "Use the Merchant Center XML export action."}

    def export_artifact(self, db, site, *, include_drafts=False):
        if include_drafts:
            raise CommerceError("Merchant Center feeds contain published site catalog state only.")
        return _feed_artifact(db, site)

    def export_review_artifact(self, db, site):
        return _feed_review_artifact(db, site)
