"""The merchant settings directory, shared by the Settings index and the settings list
shown beside every settings page."""

# The directory deliberately points to the existing route surface. Keep these
# paths here rather than creating another layer of settings routing.
SETTINGS_GROUPS = (
    (
        "Store foundation",
        "The business, account and operating details that anchor your store.",
        (
            ("General", "/settings/general", "Store details and preferences", "Store setup",
             "business information preferences"),
            ("Locations", "/settings/locations", "Inventory and fulfilment locations",
             "Fulfilment", "warehouses collection locations stock"),
            ("Domains", "/settings/domains", "Domain and go-live checks", "Go-live checks",
             "hostname storefront launch"),
            ("Plan and billing", "/settings/billing", "Your plan, usage and billing", "Account",
             "subscription usage invoices"),
            ("Users and permissions", "/settings/users", "Staff access and roles",
             "Admins only", "team members staff roles"),
            ("Store activity log", "/activity", "Changes made by you and your team", "History",
             "audit log store changes"),
        ),
    ),
    (
        "Data and compliance",
        "Structured product data and the policies customers read.",
        (
            ("Custom data", "/settings/custom-data", "Metafield definitions and validation",
             "Structured content", "metafields catalogue product fields validation"),
            ("Policies", "/settings/policies", "Store and customer policies", "Customer-facing",
             "legal returns privacy terms"),
        ),
    ),
    (
        "Checkout and fulfilment",
        "Set the rules customers see as they pay, receive orders and calculate tax.",
        (
            ("Payments", "/settings/payments", "Payment providers and commerce mode",
             "Payment setup", "providers live mode checkout"),
            ("Checkout", "/settings/checkout", "Checkout and customer accounts",
             "Customer-facing", "accounts cart order confirmation"),
            ("Shipping and delivery", "/settings/shipping", "Destinations and delivery settings",
             "Delivery rules", "shipping rates fulfilment destinations"),
            ("Taxes and duties", "/settings/taxes", "Tax configuration and previews",
             "Tax configuration", "tax rates duties registrations"),
        ),
    ),
    (
        "Customer communication",
        "Manage the messages customers receive as their orders move through your store.",
        (
            ("Notifications", "/settings/notifications", "Customer email and delivery status",
             "Customer communications", "email delivery order updates"),
        ),
    ),
    (
        "Connections",
        "Connect payment, email, shipping and other services.",
        (
            ("Integrations", "/integrations", "Connect services to your store",
             "Admins only", "apps connectors services providers"),
        ),
    ),
)

SETTINGS_ICONS = {
    "/settings/general": "store", "/settings/locations": "map-pin", "/settings/domains": "globe",
    "/settings/billing": "receipt", "/settings/users": "users", "/activity": "activity",
    "/settings/seo": "search", "/settings/custom-data": "layers", "/settings/policies": "scroll-text",
    "/settings/payments": "credit-card", "/settings/checkout": "shopping-cart", "/settings/shipping": "truck",
    "/settings/taxes": "landmark", "/settings/notifications": "bell", "/integrations": "blocks",
    "/settings/channels": "share-2", "/settings/developers": "code",
}
