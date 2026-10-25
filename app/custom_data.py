"""Validated custom data. Callers own transactions and membership checks."""
from __future__ import annotations

import copy
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_, select

from app import content
from app.content_gap_models import CustomDataDefinition, CustomDataValue
from app.models import (
    Collection,
    Product,
    ProductVariant,
    ShopCustomer,
    SiteOrder,
    SitePage,
    VariantChannelListing,
)
from app.services import CommerceError

OWNER_TYPES = ("product", "variant", "collection", "customer", "order", "page")
VALUE_TYPES = ("single_line_text", "multi_line_text", "rich_text", "integer", "decimal",
               "boolean", "date", "url", "colour", "json", "product_reference", "file_reference",
               "image_reference")
TEXT_TYPES = set(VALUE_TYPES) - {"file_reference", "image_reference", "json", "product_reference"}


def owner(db, site, owner_type, owner_id):
    models = {"product": Product, "variant": ProductVariant, "collection": Collection,
              "customer": ShopCustomer, "order": SiteOrder, "page": SitePage}
    model = models.get(owner_type)
    if model is None:
        raise CommerceError("Choose a supported custom data owner.")
    query = select(model).where(model.id == owner_id, model.tenant_id == site.tenant_id)
    if hasattr(model, "site_id"):
        query = query.where(model.site_id == site.id)
    else:
        product_id = owner_id
        if owner_type == "variant":
            product_id = db.scalar(select(ProductVariant.product_id).where(
                ProductVariant.id == owner_id, ProductVariant.tenant_id == site.tenant_id))
        if owner_type in {"product", "variant"}:
            query = query.where(or_(select(SitePage.id).where(
                SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
                SitePage.product_id == product_id).exists(), select(VariantChannelListing.id).join(
                    ProductVariant, ProductVariant.id == VariantChannelListing.variant_id).where(
                    VariantChannelListing.channel_id == site.channel_id,
                    ProductVariant.tenant_id == site.tenant_id,
                    ProductVariant.product_id == product_id).exists()))
        elif owner_type == "collection":
            query = query.where(select(SitePage.id).where(
                SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
                SitePage.collection_id == owner_id).exists())
    row = db.scalar(query)
    if not row:
        raise CommerceError("Custom data owner not found in this site.")
    return row


def definitions(db, site, owner_type=""):
    query = select(CustomDataDefinition).where(CustomDataDefinition.tenant_id == site.tenant_id,
                                              CustomDataDefinition.site_id == site.id)
    if owner_type:
        query = query.where(CustomDataDefinition.owner_type == owner_type)
    return list(db.scalars(query.order_by(CustomDataDefinition.pinned_order,
                                         CustomDataDefinition.name, CustomDataDefinition.id)))


def save_definition(db, site, data, definition_id=""):
    owner_type = str(data.get("owner_type", ""))
    value_type = str(data.get("value_type", ""))
    if owner_type not in OWNER_TYPES or value_type not in VALUE_TYPES:
        raise CommerceError("Choose a supported owner and value type.")
    namespace, key = str(data.get("namespace", "custom")), str(data.get("key", ""))
    if not all(re.fullmatch(r"[a-z][a-z0-9_]{0,79}", v) for v in (namespace, key)):
        raise CommerceError("Use lowercase letters, digits and underscores in namespace.key.")
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 160:
        raise CommerceError("Give the definition a name of up to 160 characters.")
    rules = data.get("validation", {})
    if not isinstance(rules, dict) or set(rules) - {"required", "min", "max", "choices"}:
        raise CommerceError("Use required, min, max and choices validation rules.")
    if type(rules.get("required", False)) is not bool:
        raise CommerceError("Required must be true or false.")
    if "choices" in rules and (not isinstance(rules["choices"], list)
                               or len(rules["choices"]) > 100):
        raise CommerceError("Choices must be a list of at most 100 values.")
    try:
        bounds = {k: Decimal(str(rules[k])) for k in ("min", "max") if k in rules}
        if any(not n.is_finite() for n in bounds.values()):
            raise ValueError
        if "min" in bounds and "max" in bounds and bounds["min"] > bounds["max"]:
            raise ValueError
        pinned = int(data.get("pinned_order", 0))
        if abs(pinned) > 100000:
            raise ValueError
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise CommerceError("Enter valid validation bounds and pinned order.") from exc
    duplicate = db.scalar(select(CustomDataDefinition.id).where(
        CustomDataDefinition.site_id == site.id, CustomDataDefinition.tenant_id == site.tenant_id,
        CustomDataDefinition.owner_type == owner_type, CustomDataDefinition.namespace == namespace,
        CustomDataDefinition.key == key, CustomDataDefinition.id != definition_id))
    if duplicate:
        raise CommerceError("That namespace.key is already defined for this owner.")
    row = next((d for d in definitions(db, site) if d.id == definition_id), None)
    if definition_id and not row:
        raise CommerceError("Definition not found.")
    if row:
        saved = list(db.scalars(select(CustomDataValue).where(
            CustomDataValue.definition_id == row.id, CustomDataValue.site_id == site.id,
            CustomDataValue.tenant_id == site.tenant_id)))
        if saved and (row.owner_type, row.namespace, row.key, row.value_type) != (
                owner_type, namespace, key, value_type):
            raise CommerceError("A definition with saved values cannot change owner, key or type.")
        from types import SimpleNamespace
        candidate = SimpleNamespace(value_type=value_type, validation_json=rules, name=name)
        for value in saved:
            validate_value(db, site, candidate, value.value_json)
    if not row:
        row = CustomDataDefinition(tenant_id=site.tenant_id, site_id=site.id)
        db.add(row)
    row.owner_type, row.namespace, row.key = owner_type, namespace, key
    row.name, row.value_type = name, value_type
    row.validation_json, row.pinned_order = rules, pinned
    db.flush()
    return row


def validate_value(db, site, definition, value):
    rules = definition.validation_json or {}
    if value is None or value == "":
        if rules.get("required"):
            raise CommerceError(f"{definition.name} is required.")
        return None
    kind = definition.value_type
    try:
        if kind == "integer":
            if isinstance(value, bool) or not re.fullmatch(r"-?\d+", str(value)):
                raise ValueError
            result = int(value)
        elif kind == "decimal":
            number = Decimal(str(value))
            if not number.is_finite() or len(str(value)) > 100:
                raise ValueError
            result = str(number)
        elif kind == "boolean":
            if type(value) is not bool and value not in ("true", "false", "1", "0"):
                raise ValueError
            result = value is True or value in ("true", "1")
        elif kind == "date":
            result = date.fromisoformat(str(value)).isoformat()
        elif kind == "json":
            result = json.loads(value) if isinstance(value, str) else value
            if len(json.dumps(result, allow_nan=False)) > 50000:
                raise ValueError
        elif kind == "rich_text":
            from app.catalog_admin import sanitise_html
            result = sanitise_html(value)
        elif kind == "url":
            result = content.safe_url(str(value))
        elif kind == "colour":
            if not re.fullmatch(r"#[a-fA-F0-9]{6}", str(value)):
                raise ValueError
            result = str(value).lower()
        elif kind == "product_reference":
            owner(db, site, "product", str(value))
            result = str(value)
        elif kind in {"file_reference", "image_reference"}:
            from app.site_media import owned_media
            media = owned_media(db, site, str(value))
            if kind == "image_reference" and not media.content_type.startswith("image/"):
                raise ValueError
            result = media.id
        else:
            result = str(value)
            if len(result) > 50000 or (kind == "single_line_text" and "\n" in result):
                raise ValueError
        if rules.get("required") and (result is None or result == ""):
            raise ValueError
        measure = (Decimal(str(result)) if kind in {"integer", "decimal"} else
                   Decimal(len(result)) if isinstance(result, (str, list, dict)) else None)
        if measure is not None:
            if "min" in rules and measure < Decimal(str(rules["min"])):
                raise ValueError
            if "max" in rules and measure > Decimal(str(rules["max"])):
                raise ValueError
        if rules.get("choices") and result not in rules["choices"]:
            raise ValueError
        return result
    except (ValueError, TypeError, InvalidOperation, OverflowError) as exc:
        raise CommerceError(f"Enter a valid {definition.name} value matching its validation.") from exc


def values(db, site, owner_type, owner_id, *, check_owner=True):
    if check_owner:
        owner(db, site, owner_type, owner_id)
    rows = db.execute(select(CustomDataDefinition, CustomDataValue).join(
        CustomDataValue, CustomDataValue.definition_id == CustomDataDefinition.id).where(
        CustomDataDefinition.site_id == site.id, CustomDataDefinition.tenant_id == site.tenant_id,
        CustomDataDefinition.owner_type == owner_type, CustomDataValue.site_id == site.id,
        CustomDataValue.tenant_id == site.tenant_id, CustomDataValue.owner_id == owner_id))
    return {d.namespace + "." + d.key: v.value_json for d, v in rows}


def save_values(db, site, owner_type, owner_id, data):
    owner(db, site, owner_type, owner_id)
    defs = definitions(db, site, owner_type)
    known = {d.namespace + "." + d.key: d for d in defs}
    if set(data) - set(known):
        raise CommerceError("Unknown custom data definition.")
    existing = values(db, site, owner_type, owner_id)
    cleaned = {key: validate_value(db, site, d, data.get(key, existing.get(key)))
               for key, d in known.items()}
    for key, result in cleaned.items():
        definition = known[key]
        row = db.scalar(select(CustomDataValue).where(
            CustomDataValue.definition_id == definition.id, CustomDataValue.owner_id == owner_id,
            CustomDataValue.site_id == site.id, CustomDataValue.tenant_id == site.tenant_id))
        if result is None:
            if row:
                db.delete(row)
        elif row:
            row.value_json = result
        else:
            db.add(CustomDataValue(tenant_id=site.tenant_id, site_id=site.id,
                                  definition_id=definition.id, owner_id=owner_id, value_json=result))
    db.flush()


def compatible_sources(db, site):
    result = {"text": [], "image": []}
    for d in definitions(db, site, "product"):
        target = "text" if d.value_type in TEXT_TYPES else "image" if d.value_type == "image_reference" else ""
        if target:
            result[target].append({"id": d.id, "name": d.name,
                                   "source": "metafield:" + d.namespace + "." + d.key})
    return result


def product_id_for_page(db, site, page):
    """Older product documents identify their catalogue entry by a scoped handle."""
    product_id = getattr(page, "product_id", None)
    if product_id:
        return product_id
    path = getattr(page, "path", "")
    match = re.fullmatch(r"/products/([a-z0-9-]+)", path)
    if not match:
        return None
    return db.scalar(select(Product.id).where(Product.tenant_id == site.tenant_id,
        Product.slug == match[1], select(VariantChannelListing.id).join(ProductVariant,
            ProductVariant.id == VariantChannelListing.variant_id).where(
            ProductVariant.product_id == Product.id, ProductVariant.tenant_id == site.tenant_id,
            VariantChannelListing.channel_id == site.channel_id).exists()))


def resolve_dynamic_document(db, site, page, document):
    """Resolve pinned product sources in a detached document, never in stored JSON."""
    document = copy.deepcopy(document)
    product_id = product_id_for_page(db, site, page)
    if not product_id:
        return document
    data = values(db, site, "product", product_id)
    image_keys = {d.namespace + "." + d.key for d in definitions(db, site, "product")
                  if d.value_type == "image_reference"}
    def visit(value):
        if isinstance(value, dict):
            return {k: visit(v) for k, v in value.items()}
        if isinstance(value, list):
            return [visit(v) for v in value]
        if isinstance(value, str) and value.startswith("metafield:"):
            key = value.removeprefix("metafield:")
            result = data.get(key, "")
            if key in image_keys and result:
                return f"/site-media/{site.id}/{result}"
            if isinstance(result, bool):
                return "Yes" if result else "No"
            return str(result)
        return value
    return visit(document)
