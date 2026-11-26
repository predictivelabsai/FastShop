"""Which AI model a store's assistant uses, and how a merchant connects their own.

Each store brings its own OpenAI-compatible API key. It is stored encrypted per site through
``site_integrations``. Resolution order:

1. the store's own connection, saved under Settings, AI assistant;
2. the platform key from the server environment (``MODEL_API_KEY``/``XAI_API_KEY``), kept only
   as an operator fallback;
3. otherwise the assistant is not configured for the store.

Keys never leave this module except as a request header to the chosen provider.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from app import config, site_integrations
from app.services import CommerceError

# The SiteIntegrationCredential.provider value for the store's model connection.
CREDENTIAL = "assistant_model"
CUSTOM = "custom"

# Merchant-selectable OpenAI-compatible providers. Default models are only suggestions.
PROVIDERS = {
    "xai": {"label": "xAI", "base_url": "https://api.x.ai/v1",
            "model": "grok-4-1-fast-reasoning", "keys": "https://console.x.ai"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1",
               "model": "gpt-4.1-mini", "keys": "https://platform.openai.com/api-keys"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "model": "openai/gpt-4.1-mini", "keys": "https://openrouter.ai/settings/keys"},
    CUSTOM: {"label": "Custom OpenAI-compatible endpoint", "base_url": "", "model": "", "keys": ""},
}

CHAT_TIMEOUT = httpx.Timeout(90.0, connect=10.0)
TEST_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
MAX_KEY_LENGTH = 400
MAX_MODEL_LENGTH = 120
MAX_URL_LENGTH = 300
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".lan")


@dataclass(frozen=True)
class ModelConfig:
    """A resolved, ready-to-call model connection. ``api_key`` is never rendered."""

    provider: str
    base_url: str
    api_key: str
    model: str
    source: str  # "store" or "platform"

    @property
    def label(self) -> str:
        return PROVIDERS.get(self.provider, {}).get("label", "Custom endpoint")

    def __repr__(self) -> str:  # Keep keys out of logs and tracebacks.
        return (f"ModelConfig(provider={self.provider!r}, base_url={self.base_url!r}, "
                f"model={self.model!r}, source={self.source!r})")


def _blocked_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_link_local


def validate_base_url(value: str, *, resolve: bool = True) -> str:
    """Return a normalised HTTPS base URL, refusing private and local destinations (SSRF)."""
    raw = str(value or "").strip()
    if not raw or len(raw) > MAX_URL_LENGTH:
        raise CommerceError("Enter the endpoint's https address, such as https://api.example.com/v1.")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise CommerceError("Enter a valid https address for the endpoint.") from exc
    host = (parts.hostname or "").rstrip(".").lower()
    if parts.scheme != "https":
        raise CommerceError("The endpoint must use https.")
    if not host or parts.username or parts.password or parts.query or parts.fragment:
        raise CommerceError("Enter the endpoint address without a username, query or fragment.")
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES) or "." not in host and ":" not in host:
        raise CommerceError("The endpoint must be a public address, not a local or private one.")
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal and _blocked_address(host):
        raise CommerceError("The endpoint must be a public address, not a local or private one.")
    if resolve and not literal:
        try:
            addresses = {info[4][0] for info in socket.getaddrinfo(host, port or 443,
                                                                  proto=socket.IPPROTO_TCP)}
        except (OSError, UnicodeError) as exc:
            raise CommerceError("We couldn't find that endpoint address. Check it and try again.") from exc
        if not addresses or any(_blocked_address(address) for address in addresses):
            raise CommerceError("The endpoint must be a public address, not a local or private one.")
    netloc = host if port in (None, 443) else f"{host}:{port}"
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]" if port in (None, 443) else f"[{host}]:{port}"
    return f"https://{netloc}{parts.path.rstrip('/')}"


def clean_connection(provider: str, api_key: str, model: str, base_url: str = "", *,
                     resolve: bool = True) -> dict:
    """Validate a merchant's form values and return the payload to store."""
    provider = str(provider or "").strip().lower()
    if provider not in PROVIDERS:
        raise CommerceError("Choose xAI, OpenAI, OpenRouter or a custom endpoint.")
    api_key = str(api_key or "").strip()
    if not api_key or len(api_key) > MAX_KEY_LENGTH or any(ch.isspace() for ch in api_key):
        raise CommerceError("Enter the API key exactly as your provider shows it.")
    if provider == CUSTOM:
        base_url = validate_base_url(base_url, resolve=resolve)
    else:
        base_url = PROVIDERS[provider]["base_url"]
    model = str(model or "").strip() or PROVIDERS[provider]["model"]
    if not model or len(model) > MAX_MODEL_LENGTH or any(ch.isspace() for ch in model):
        raise CommerceError("Enter a model name, such as gpt-4.1-mini.")
    return {"provider": provider, "base_url": base_url, "api_key": api_key, "model": model}


def hints_for(payload: dict) -> dict:
    """Display-safe facts about a saved connection; the key is masked."""
    hints = {"provider": PROVIDERS[payload["provider"]]["label"], "model": payload["model"],
             "api_key": site_integrations.mask(payload["api_key"])}
    if payload["provider"] == CUSTOM:
        hints["endpoint"] = urlsplit(payload["base_url"]).hostname or ""
    return hints


def stored(db, site) -> dict | None:
    """The store's own decrypted connection, or None if it has none or it is unreadable."""
    try:
        payload = site_integrations.payload_for(site, CREDENTIAL, db)
    except Exception:  # noqa: BLE001 - an unreadable key behaves like no key
        return None
    if not payload or not payload.get("api_key") or payload.get("provider") not in PROVIDERS:
        return None
    return payload


def platform_config() -> ModelConfig | None:
    settings = config.settings
    if not settings.xai_api_key:
        return None
    return ModelConfig("platform", settings.xai_base_url.rstrip("/"), settings.xai_api_key,
                       settings.model_name, "platform")


def resolve(db, site) -> ModelConfig | None:
    """The model connection the store's assistant should use, or None when not configured."""
    payload = stored(db, site) if site is not None else None
    if payload:
        return ModelConfig(payload["provider"], payload["base_url"].rstrip("/"), payload["api_key"],
                           payload.get("model") or PROVIDERS[payload["provider"]]["model"], "store")
    return platform_config()


def is_configured(site, db=None) -> bool:
    """True when the store can use the assistant. Opens its own session if none is given."""
    if site is None:
        return False
    if db is not None:
        return resolve(db, site) is not None
    from app.db import SessionLocal

    try:
        with SessionLocal() as session:
            return resolve(session, site) is not None
    except Exception:  # noqa: BLE001 - an unreachable store record must not break page rendering
        return platform_config() is not None


def settings_href(site_id: str) -> str:
    return f"/admin/sites/{site_id}/settings/assistant"


def status(db, site) -> dict:
    """What the settings page and Integrations show. Never includes the key itself."""
    row = site_integrations.credential_row(db, site, CREDENTIAL)
    if row and row.status == "connected" and stored(db, site):
        return {"state": "store", "label": "Connected", "hints": dict(row.masked_hints_json or {}),
                "verified_at": row.verified_at}
    if row:
        return {"state": "unreadable", "label": "Needs attention", "hints": {}, "verified_at": None}
    if platform_config():
        return {"state": "platform", "label": "Platform key", "hints": {}, "verified_at": None}
    return {"state": "none", "label": "Not connected", "hints": {}, "verified_at": None}


def request_options(model_config: ModelConfig) -> dict:
    """Per-provider request fields. OpenAI's newer models reject temperature and max_tokens."""
    if model_config.provider == "openai":
        return {"max_completion_tokens": 4000}
    return {"temperature": 0.2, "max_tokens": 4000}


def endpoint(model_config: ModelConfig) -> str:
    """The chat completions URL, re-checking a custom endpoint just before each request."""
    base = model_config.base_url
    if model_config.provider == CUSTOM:
        base = validate_base_url(base)
    return base.rstrip("/") + "/chat/completions"


def check_connection(model_config: ModelConfig) -> tuple[bool, str]:
    """Send one tiny request. Returns (ok, merchant-readable message) without provider detail."""
    try:
        url = endpoint(model_config)
    except CommerceError as exc:
        return False, str(exc)
    try:
        response = httpx.post(url, headers={"Authorization": "Bearer " + model_config.api_key},
                              json={"model": model_config.model,
                                    "messages": [{"role": "user", "content": "Reply with OK."}]},
                              timeout=TEST_TIMEOUT, follow_redirects=False)
    except httpx.TimeoutException:
        return False, "The provider didn't answer in time. Try again in a moment."
    except httpx.HTTPError:
        return False, "We couldn't reach the provider. Check the endpoint and try again."
    code = response.status_code
    if code in (401, 403):
        return False, "The provider rejected this API key. Check it and save it again."
    if code == 404:
        return False, "The provider couldn't find that model or address. Check the model name."
    if code == 429:
        return False, "The provider accepted the key but is limiting requests or is out of credit."
    if code >= 300:
        return False, f"The provider returned an error (HTTP {code}). Check the model name and try again."
    try:
        payload = response.json()
        ok = isinstance(payload, dict) and isinstance(payload.get("choices"), list)
    except ValueError:
        ok = False
    if not ok:
        return False, "The endpoint answered, but not like an OpenAI-compatible chat API."
    return True, f"Connected to {model_config.label} using {model_config.model}."


def mark_verified(db, site) -> None:
    row = site_integrations.credential_row(db, site, CREDENTIAL)
    if row:
        row.verified_at = datetime.now(UTC)
        db.flush()
