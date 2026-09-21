"""Explicit synthetic merchant fixtures; never credentials or live configuration."""

import copy
from datetime import UTC, datetime

from sqlalchemy import select

from app import commerce, content
from app.models import SiteCommerceSettings
from app.services import CommerceError

FIELDS = {"company": "Company name", "address": "Legal company address", "email": "Contact email",
          "origin_line1": "Warehouse street", "origin_city": "Warehouse city", "origin_postal_code": "Warehouse postal code",
          "origin_country": "Warehouse EU country", "shipping_minor": "Shipping below threshold (USD cents)",
          "free_shipping_threshold_minor": "Free shipping threshold (USD cents)"}
SAMPLES = {"company": "Example Studio — DEMO", "address": "Example street 1, Demo city, Estonia (synthetic)",
           "email": "merchant@example.test", "origin_line1": "Example warehouse 1 — DEMO", "origin_city": "Tallinn",
           "origin_postal_code": "10111", "origin_country": "EE", "shipping_minor": 1000, "free_shipping_threshold_minor": 7500}


def invalidate_reviews(settings, previous, current, *, source="merchant_entered"):
    """A review applies to a value, not permanently to a field's name."""
    result = copy.deepcopy(settings)
    provenance = result.get("sample_fields", {})
    for key in FIELDS:
        if key in current and current[key] != previous.get(key):
            provenance[key] = {"source": source, "reviewed": False,
                               "updated_at": datetime.now(UTC).isoformat()}
    if provenance:
        result["sample_fields"] = provenance
    return result


def pending_reviews(settings):
    return [FIELDS[key] for key, value in settings.get("sample_fields", {}).items()
            if key in FIELDS and not value.get("reviewed")]


def values_for(site, config):
    return {key: config.origin_json.get(key.removeprefix("origin_"), "") if key.startswith("origin_")
            else getattr(config, key) if key.endswith("_minor") else site.settings_json.get(key, "") for key in FIELDS}


def locked_config(db, site):
    config = commerce.settings_for(db, site, create=True)
    return db.scalar(select(SiteCommerceSettings).where(SiteCommerceSettings.id == config.id,
        SiteCommerceSettings.site_id == site.id, SiteCommerceSettings.tenant_id == site.tenant_id)
        .with_for_update().execution_options(populate_existing=True))


def seed_samples(db, site, user_id):
    content.owned_site(db, site.id, user_id, publish=True)
    if site.status != "draft":
        raise CommerceError("Sample setup is only available on a private draft site.")
    config = locked_config(db, site)
    if config.mode != "disabled":
        raise CommerceError("Disable commerce before loading sample merchant details.")
    settings = copy.deepcopy(site.settings_json)
    provenance = settings.setdefault("sample_fields", {})
    current = values_for(site, config)
    origin = dict(config.origin_json)
    for key, sample in SAMPLES.items():
        if key in provenance:
            continue  # Idempotent; never overwrite previously edited/reviewed values.
        # The generic EU country and threshold defaults can be marked synthetic;
        # non-empty merchant-entered identity and fee values are preserved.
        use_sample = current[key] in (None, "")
        value = sample if use_sample else current[key]
        provenance[key] = {"source": "synthetic" if use_sample or (key in {"origin_country", "free_shipping_threshold_minor"} and current[key] == sample) else "merchant_entered",
                           "reviewed": False, "updated_at": datetime.now(UTC).isoformat()}
        if key.startswith("origin_"):
            origin[key.removeprefix("origin_")] = value
        elif key.endswith("_minor"):
            setattr(config, key, value)
        else:
            settings[key] = value
    config.origin_json = origin
    config.version += 1
    site.settings_json = settings
    site.version += 1
    db.flush()


def save_samples(db, site, user_id, values, reviewed, config_version):
    content.owned_site(db, site.id, user_id, publish=True)
    config = locked_config(db, site)
    if config.version != config_version:
        raise CommerceError("Commerce settings changed. Reload before saving.")
    if config.mode != "disabled":
        raise CommerceError("Use commerce settings for an enabled store; sample review requires commerce disabled.")
    settings = copy.deepcopy(site.settings_json)
    provenance = settings.setdefault("sample_fields", {})
    previous_values = values_for(site, config)
    origin = dict(config.origin_json)
    for key in FIELDS:
        value = str(values.get(key, "")).strip()
        if len(value) > 300:
            raise CommerceError("Keep merchant fields under 300 characters.")
        if key.endswith("_minor"):
            try:
                value = int(value)
            except ValueError as exc:
                raise CommerceError("Shipping amounts must be whole USD cents.") from exc
            if not 0 <= value <= 1_000_000:
                raise CommerceError("Shipping amount is out of range.")
            setattr(config, key, value)
        elif key.startswith("origin_"):
            origin[key.removeprefix("origin_")] = value.upper() if key == "origin_country" else value
        else:
            settings[key] = value
        prior = provenance.get(key, {"source": "merchant_entered", "reviewed": False})
        changed = value != previous_values.get(key)
        confirmed = key in reviewed
        provenance[key] = {"source": "merchant_entered" if changed or confirmed else prior["source"],
                           "reviewed": confirmed, "updated_at": datetime.now(UTC).isoformat()}
    if origin.get("country") not in commerce.EU_COUNTRIES:
        raise CommerceError("Choose a supported EU warehouse country.")
    config.origin_json = origin
    config.version += 1
    site.settings_json = settings
    site.version += 1
    db.flush()
