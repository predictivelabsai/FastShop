"""Shared per-site gate for customer account surfaces."""

from fasthtml.common import A, Div, P
from fasthtml.core import FtResponse

from app import commerce
from app.platform_ui import platform_page
from app.services import CommerceError
from app.storefront_ui import is_liquid, liquid_page


class CustomerAccountsUnavailable(CommerceError):
    def __init__(self, site):
        super().__init__("Customer accounts are not available for this store")
        self.site = site


def require_customer_accounts(db, site):
    config = commerce.settings_for(db, site)
    if not config or not config.customer_accounts_enabled:
        raise CustomerAccountsUnavailable(site)


def unavailable_response(exc, request):
    site = exc.site
    root = request.scope.get("site_base", "/sites/" + site.slug)
    if is_liquid(site):
        return FtResponse(liquid_page(site, request, "Customer accounts", P(str(exc))),
            status_code=404, headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"})
    return FtResponse(platform_page(
        "Customer accounts", P(site.name + " · Powered by FastShop"),
        Div(P(str(exc)), cls="e-card"),
        navigation=[A("Back to store", href=root + "/")], customer=True,
    ), status_code=404, headers={
        "Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer",
    })
