import base64
import hashlib
import hmac
from types import SimpleNamespace

import httpx
import pytest

from app.integrations.stripe_commerce import StripeGateway
from app.integrations.woocommerce import WooCommerceGateway, usd_minor, verify_webhook
from app.services import CommerceError


@pytest.fixture
def woo(monkeypatch):
    site = SimpleNamespace(id="woo-test", tenant_id="tenant-a", slug="woo-test")
    prefix = "FASTSHOP_WOOCOMMERCE_WOO-TEST_"
    for key, value in {"ENABLED": "true", "URL": "https://store.example.com",
                       "CONSUMER_KEY": "ck_fixture", "CONSUMER_SECRET": "cs_fixture"}.items():
        monkeypatch.setenv(prefix + key, value)
    return site, prefix


def test_woo_read_pagination_auth_and_stubs(woo):
    site, _ = woo
    calls = []
    def respond(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["authorization"].startswith("Basic ")
        assert "ck_fixture" not in str(request.url)
        assert request.url.path == "/wp-json/wc/v3/products"
        assert request.url.params["page"] == "2"
        return httpx.Response(200, json=[{"id": 1, "price": "29.95"}])
    gateway = WooCommerceGateway(site, tenant_id=site.tenant_id, transport=httpx.MockTransport(respond))
    assert gateway.list_resources("products", page=2)[0]["id"] == 1
    for method in (gateway.create_order, gateway.create_coupon, gateway.manage_subscription):
        with pytest.raises(CommerceError):
            method({})
    assert len(calls) == 1
    for resource in ["../orders", "https://evil.example/orders", "subscriptions"]:
        with pytest.raises(CommerceError):
            gateway.list_resources(resource)


def test_woo_tenant_and_disabled_boundary(woo, monkeypatch):
    site, prefix = woo
    with pytest.raises(CommerceError, match="Store not found"):
        WooCommerceGateway(site, tenant_id="tenant-b")
    monkeypatch.delenv(prefix + "ENABLED")
    with pytest.raises(CommerceError, match="disabled"):
        WooCommerceGateway(site, tenant_id=site.tenant_id)


def test_woo_missing_keys_do_not_inherit_another_store(woo, monkeypatch):
    site, prefix = woo
    monkeypatch.delenv(prefix + "CONSUMER_SECRET")
    monkeypatch.setenv("WOOCOMMERCE_CONSUMER_SECRET", "cs_other_merchant")
    with pytest.raises(CommerceError, match="credentials"):
        WooCommerceGateway(site, tenant_id=site.tenant_id)


def test_woo_status_order_and_response_validation(woo):
    site, _ = woo
    def respond(request):
        if request.url.path.endswith("/orders/12"):
            return httpx.Response(200, json={"id": 12})
        if request.url.path.endswith("/orders/13"):
            return httpx.Response(200, json={"id": 999})
        if request.url.path.endswith("/customers"):
            return httpx.Response(200, json={"unexpected": True})
        return httpx.Response(200, json=[])
    gateway = WooCommerceGateway(site, tenant_id=site.tenant_id, transport=httpx.MockTransport(respond))
    assert gateway.connection_status()["sync_enabled"] is False
    assert gateway.order(12) == {"id": 12}
    for invalid in [True, 0, "../12", 13]:
        with pytest.raises(CommerceError):
            gateway.order(invalid)
    with pytest.raises(CommerceError):
        gateway.list_resources("customers")
    for invalid in [0, 101, True]:
        with pytest.raises(CommerceError):
            gateway.list_resources("products", per_page=invalid)


@pytest.mark.parametrize("url", ["http://store.example.com", "https://user:pass@store.example.com",
    "https://127.0.0.1", "https://localhost", "https://store.example.com?token=x",
    "https://store.example.com/wp-json", "https://10.0.0.1", "https://store.local"])
def test_woo_origin_restrictions(woo, monkeypatch, url):
    site, prefix = woo
    monkeypatch.setenv(prefix + "URL", url)
    with pytest.raises(CommerceError):
        WooCommerceGateway(site, tenant_id=site.tenant_id)


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_woo_errors_redacted_no_redirect_or_retry(woo, status):
    site, _ = woo
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://evil.example"}, text="cs_fixture private customer")
    gateway = WooCommerceGateway(site, tenant_id=site.tenant_id, transport=httpx.MockTransport(respond))
    with pytest.raises(CommerceError) as error:
        gateway.list_resources("orders")
    assert "cs_fixture" not in str(error.value) and error.value.__suppress_context__
    assert len(calls) == 1


@pytest.mark.parametrize("value", ["", "NaN", "-1", "1.001", "1e2", 29.95, True])
def test_woo_invalid_money(value):
    with pytest.raises(CommerceError):
        usd_minor(value)


def test_woo_money_webhook():
    assert usd_minor("29.95") == 2995
    assert usd_minor("0") == 0
    payload = b'{"id":123}'
    signature = base64.b64encode(hmac.new(b"fixture", payload, hashlib.sha256).digest()).decode()
    assert verify_webhook(payload, signature, "fixture") == {"id": 123}
    with pytest.raises(CommerceError):
        verify_webhook(payload + b" ", signature, "fixture")


@pytest.fixture
def stripe_site(monkeypatch):
    monkeypatch.setenv("FASTSHOP_STRIPE_PROVIDERTEST_SECRET_KEY", "sk_test_fixture")
    return SimpleNamespace(id="providertest", slug="isolated-provider")


def test_stripe_readiness_read_only(stripe_site):
    paths = []
    def respond(request):
        paths.append(request.url.path)
        assert request.method == "GET"
        return httpx.Response(200, json={"id": "acct_fixture", "charges_enabled": True}
            if request.url.path.endswith("/account") else
            {"object": "tax.settings", "livemode": False, "status": "active"})
    result = StripeGateway(stripe_site, transport=httpx.MockTransport(respond)).connection_status()
    assert result["tax_settings_active"] and not result["provider_acceptance_complete"]
    assert "acct_fixture" not in str(result)
    assert paths == ["/v1/account", "/v1/tax/settings"]


def test_stripe_uppercase_provider_ids_and_live_response(stripe_site):
    calls = []
    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"livemode": True})
    gateway = StripeGateway(stripe_site, transport=httpx.MockTransport(respond))
    with pytest.raises(CommerceError, match="live Stripe response"):
        gateway.payment_intent_status("pi_TestABC")
    assert calls == ["/v1/payment_intents/pi_TestABC"]


def test_stripe_transport_retry_preserves_command(stripe_site):
    calls = []
    def respond(request):
        calls.append((request.content, request.headers["idempotency-key"]))
        if len(calls) == 1:
            raise httpx.ReadTimeout("private provider details")
        return httpx.Response(200, json={"id": "pi_fixture", "livemode": False})
    gateway = StripeGateway(stripe_site, transport=httpx.MockTransport(respond))
    gateway.create_renewal("cycle-a", {"amount": 2995, "currency": "usd"})
    assert len(calls) == 2 and calls[0] == calls[1]


def test_stripe_fail_closed_and_no_http_retries(stripe_site):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(402, text="secret account details")
    gateway = StripeGateway(stripe_site, transport=httpx.MockTransport(respond))
    with pytest.raises(CommerceError, match="idempotency"):
        gateway.request("POST", "/customers", {})
    with pytest.raises(CommerceError, match="Invalid Stripe"):
        gateway.request("GET", "https://evil.example")
    assert not calls
    with pytest.raises(CommerceError) as error:
        gateway.create_renewal("cycle-b", {})
    assert len(calls) == 1 and "secret account" not in str(error.value)


def test_stripe_retry_bound_and_live_key_rejection(stripe_site, monkeypatch):
    calls = []
    def respond(request):
        calls.append(request)
        raise httpx.ConnectError("private")
    gateway = StripeGateway(stripe_site, transport=httpx.MockTransport(respond))
    with pytest.raises(CommerceError):
        gateway.request("GET", "/account")
    assert len(calls) == 2
    monkeypatch.setenv("FASTSHOP_STRIPE_PROVIDERTEST_SECRET_KEY", "sk_live_fixture")
    with pytest.raises(CommerceError, match="Live keys"):
        StripeGateway(stripe_site)
