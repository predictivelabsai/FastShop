"""Draft-only catalogue preparation from a private Assistant attachment."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.services import CommerceError

MAX_PREVIEW_ROWS = 50
MAX_REPORT_ERRORS = 30
_ALIASES = {
    "name": {"name", "title", "product_name", "product"},
    "sku": {"sku", "variant_sku"},
    "price": {"price", "variant_price", "sale_price"},
    "stock": {"stock", "inventory", "quantity", "variant_inventory_qty"},
    "category": {"category", "collection", "product_type", "type"},
    "description": {"description", "body", "body_html", "product_description"},
}


def _header(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.strip().casefold())).strip("_")


def _columns(headers: list[str]) -> dict[str, int]:
    mapped = {}
    for field, aliases in _ALIASES.items():
        matches = [index for index, value in enumerate(headers) if _header(value) in aliases]
        if len(matches) > 1:
            raise CommerceError(f"The attachment has more than one {field} column.")
        if matches:
            mapped[field] = matches[0]
    if "name" not in mapped:
        raise CommerceError("The attachment needs a Name or Title column to prepare catalogue rows.")
    return mapped


def _value(values: list[str], columns: dict[str, int], name: str) -> str:
    index = columns.get(name)
    return values[index].strip() if index is not None and index < len(values) else ""


def _validate(row: dict[str, str]) -> list[str]:
    errors = []
    if not row["name"]:
        errors.append("Name is blank.")
    if len(row["name"]) > 220:
        errors.append("Name exceeds 220 characters.")
    if row["price"]:
        try:
            amount = Decimal(row["price"])
        except InvalidOperation:
            errors.append("Price is not a plain decimal.")
        else:
            if amount < 0 or amount.as_tuple().exponent < -2:
                errors.append("Price must be a non-negative value with at most two decimal places.")
    if row["stock"] and (not row["stock"].isdigit() or int(row["stock"]) > 10_000_000):
        errors.append("Stock must be a whole number up to 10,000,000.")
    return errors


def prepare_catalogue(text: str, content_type: str) -> dict:
    """Validate a bounded extracted CSV/XLSX table without creating catalogue data."""
    if content_type not in {
        "text/csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }:
        raise CommerceError("Attach a CSV or XLSX spreadsheet to prepare a catalogue import.")
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    lines = [line for line in lines if not line.startswith("Sheet: ")]
    if len(lines) < 2:
        raise CommerceError("The attachment needs a header row and at least one catalogue row.")
    headers = [value.strip() for value in lines[0].split(" | ")]
    columns = _columns(headers)
    rows, errors = [], []
    for number, line in enumerate(lines[1:MAX_PREVIEW_ROWS + 1], 2):
        values = [value.strip() for value in line.split(" | ")]
        if len(values) > len(headers):
            errors.append({"row": number, "error": "Row has more values than its header."})
            continue
        item = {field: _value(values, columns, field) for field in _ALIASES}
        item_errors = _validate(item)
        if item_errors:
            errors.append({"row": number, "error": " ".join(item_errors)})
        rows.append({key: value for key, value in item.items() if value})
    if not rows:
        raise CommerceError("The attachment did not contain usable catalogue rows.")
    return {
        "state": "draft_review",
        "published_catalogue_changed": False,
        "columns": sorted(columns),
        "rows_scanned": len(lines) - 1,
        "rows_previewed": len(rows),
        "preview": rows,
        "errors": errors[:MAX_REPORT_ERRORS],
        "truncated": len(lines) - 1 > MAX_PREVIEW_ROWS or len(errors) > MAX_REPORT_ERRORS,
        "next_step": "Review this plan, then use the Products import screen to apply a confirmed CSV import.",
    }
