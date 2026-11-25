"""Shared merchant components and request-local shell context.

The ASGI context only selects presentation. Route handlers remain responsible for
authorisation; shell queries independently check active users and memberships.
"""

from contextvars import ContextVar
from urllib.parse import parse_qs

from fastcore.xml import FT
from fasthtml.common import (
    H1,
    H2,
    A,
    Aside,
    Button,
    Details,
    Dialog,
    Div,
    Form,
    Header,
    Img,
    Input,
    Label,
    Li,
    Link,
    Main,
    Meta,
    Nav,
    Ol,
    P,
    Script,
    Span,
    Summary,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Title,
    Tr,
)
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Membership, Site, User
from app.ui import static_asset

admin_scope = ContextVar("admin_scope", default=None)


class AdminContextMiddleware:
    """Carry the request scope safely through concurrent sync route rendering."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        token = admin_scope.set(scope)
        try:
            await self.app(scope, receive, send)
        finally:
            admin_scope.reset(token)


def page_header(title, back_href=None, breadcrumbs=(), primary_action=None,
                secondary_actions=(), badges=()):
    if isinstance(primary_action, FT):
        target = primary_action
        if target.tag == "form":
            target = next((child for child in target.children
                           if isinstance(child, FT) and child.tag == "button"), target)
        target.attrs["class"] = target.attrs.get("class", "") + " a-button-primary"
    return Header(
        Div(Div(A(ui_icon("back", 20),
                  href=back_href, aria_label="Back", cls="a-back-button") if back_href else None,
                H1(title), *badges, cls="a-heading")),
        Div(*secondary_actions, primary_action, cls="a-actions"), cls="a-header")


def card(*children, title=None, cls="", **kwargs):
    return Div(H2(title) if title else None, *children, cls=("a-card " + cls).strip(), **kwargs)


def card_section(*children):
    return Div(*children, cls="a-card-section")


def two_column(main, aside):
    return Div(Div(main), Aside(aside, aria_label="Details"), cls="a-columns")


def badge(text, tone="neutral"):
    if tone not in {"success", "info", "warning", "critical", "neutral"}:
        tone = "neutral"
    return Span(text, cls=f"a-badge a-badge-{tone}")


def pager(summary, previous=None, next=None, cls=""):
    """Shopify-style pagination: grouped previous/next arrows with a quiet summary underneath a list."""
    from app.admin_icons import lucide

    def arrow(href, name, label):
        if href:
            return A(lucide(name, 16), href=href, aria_label=label, title=label, cls="p-pager-arrow")
        return Span(lucide(name, 16), aria_label=label + " (unavailable)", aria_disabled="true",
                    role="link", cls="p-pager-arrow is-disabled")
    return Nav(Div(arrow(previous, "chevron-left", "Previous page"), arrow(next, "chevron-right", "Next page"),
                   cls="p-pager-arrows"),
               Span(summary, cls="p-pager-summary"), aria_label="Pagination", cls=("p-pager " + cls).strip())


def empty_state(title, description, action=None):
    if isinstance(action, FT):
        action.attrs["class"] = action.attrs.get("class", "") + " a-button-primary"
    return Div(icon("Content"), H2(title), P(description), action, cls="a-empty")


def index_table(columns, rows, tabs=None, search=None, filters=None, sort=None,
                bulk_actions=None, pagination=None):
    """Rows contain display cells; mutations are supplied as CSRF-protected forms."""
    return Div(
        Nav(*(tabs or ()), aria_label="Views", cls="a-tabs") if tabs else None,
        Div(Label("Search", Input(type="search", data_table_search="",
                                  placeholder=search)) if search else None,
            filters, sort, cls="a-table-tools"),
        Div(*(bulk_actions or ()), data_bulk_actions="", hidden=True) if bulk_actions else None,
        Div(Table(Thead(Tr(Th(Input(type="checkbox", data_select_all="",
                                   aria_label="Select all rows")) if bulk_actions else None,
                              *[Th(c, scope="col") for c in columns])),
                  Tbody(*[Tr(Td(Input(type="checkbox", data_row_select="",
                                      aria_label=f"Select row {i + 1}")) if bulk_actions else None,
                             *[Td(cell) for cell in row]) for i, row in enumerate(rows)])),
            cls="a-table-wrap"),
        pagination, cls="a-card a-index-table", data_index_table="")


DATE_PRESETS = (("1", "Today"), ("7", "Last 7 days"), ("30", "Last 30 days"), ("90", "Last 90 days"))


def date_range_picker(action, *, preset="30", start="", end="", name="preset", custom=True, label=None,
                      presets=DATE_PRESETS, hidden=()):
    """Shopify-style date picker: a calendar button that opens preset ranges and a custom range."""
    from fasthtml.common import Details, Summary

    from app.admin_icons import lucide
    names = dict(presets)
    text = label or names.get(preset) or (f"{start} to {end}" if start and end else "Custom range")
    return Details(
        Summary(lucide("calendar", 16), Span(text), cls="a-button p-datepicker-toggle",
                aria_label=f"Date range: {text}"),
        Form(
            *[Input(type="hidden", name=key, value=value) for key, value in hidden],
            Div(*[Button(title, type="submit", name=name, value=value, aria_pressed=str(value == preset).lower(),
                         cls="p-datepicker-preset" + (" is-active" if value == preset else ""))
                  for value, title in presets], cls="p-datepicker-presets", role="group", aria_label="Preset ranges"),
            Div(Span("Custom range", cls="p-datepicker-heading"),
                Div(Label("Start", Input(type="date", name="start", value=start)),
                    Label("End", Input(type="date", name="end", value=end)), cls="p-datepicker-dates"),
                Div(Button("Apply", type="submit", name=name, value="custom", cls="a-button a-button-primary"),
                    cls="p-datepicker-foot"),
                cls="p-datepicker-custom") if custom else None,
            method="get", action=action, cls="p-datepicker-panel" + ("" if custom else " is-compact")),
        cls="p-datepicker p-popover")


def delta_badge(current, previous):
    """Change against the previous period, coloured only when it means something."""
    from app.admin_icons import lucide
    if not previous:
        return Span("New", cls="p-delta") if current else None
    change = (current - previous) * 100 / previous
    if abs(change) < 0.05:
        return Span("0%", cls="p-delta")
    up = change > 0
    return Span(lucide("arrow-up" if up else "arrow-down", 12), f"{abs(change):.0f}%",
                cls="p-delta " + ("is-up" if up else "is-down"),
                aria_label=f"{'Up' if up else 'Down'} {abs(change):.0f}% on the previous period")


def form_modal(title, *fields, submit, action, id, csrf=None, method="post", description=None):
    """Polaris modal: titled header with a close button, a form body, and Cancel plus a primary action."""
    from app.admin_icons import lucide
    return Dialog(
        Form(
            Div(H2(title, id=f"{id}-title"),
                Button(lucide("x", 18), type="button", data_modal_close="", aria_label="Close", cls="p-modal-close"),
                cls="p-modal-head"),
            Div(P(description, cls="p-modal-description") if description else None, csrf, *fields,
                cls="p-modal-body"),
            Div(Button("Cancel", type="button", data_modal_close="", cls="a-button"),
                Button(submit, type="submit", cls="a-button a-button-primary"), cls="p-modal-foot"),
            method=method, action=action),
        id=id, aria_labelledby=f"{id}-title", cls="p-modal")


def modal(title, *children, id="admin-modal"):
    return Dialog(H2(title), *children,
                  Form(Button("Close"), method="dialog"), id=id, aria_label=title, cls="a-modal")


def key_value_list(items):
    from fasthtml.common import Dd, Dl, Dt
    return Dl(*[Div(Dt(key), Dd(value)) for key, value in items], cls="a-kv")


def timeline(items):
    return Ol(*[Li(item) for item in items], cls="a-timeline")


def resource_picker(id="resource-picker", title="Choose a resource"):
    return modal(title, empty_state("Resource picker", "Resource selection is coming in this release."), id=id)


# Navigation and interface icons come from the vendored Lucide set in static/admin/icons.
_ICONS = {
    "Assistant": "sparkles", "Home": "house", "Orders": "inbox", "Products": "tag", "Customers": "user",
    "Discounts": "badge-percent", "Content": "file-text", "Analytics": "chart-column", "Marketing": "target",
    "Online Store": "store", "Integrations": "blocks", "Settings": "settings",
}

_UI_ICONS = {
    "menu": "menu", "chevron": "chevron-down", "chevron-left": "chevron-left", "close": "x",
    "search": "search", "external": "external-link", "back": "arrow-left",
}


def _initials(name: str) -> str:
    """Up to two initials from the words of a name that start with a letter or digit."""
    words = [word for word in name.split() if word[:1].isalnum()]
    return "".join(word[0] for word in words[:2]).upper() or "?"


def ui_icon(name, size=20):
    from app.admin_icons import lucide
    return lucide(_UI_ICONS.get(name, name), size)


def icon(name, size=20):
    from app.admin_icons import lucide
    return lucide(_ICONS.get(name, name), size, stroke=1.75)


def _has_page_header(child):
    if not isinstance(child, FT):
        return False
    return "a-header" in child.attrs.get("class", "").split() or any(
        _has_page_header(item) for item in child.children)


def _legacy_back_link(children):
    """Move the older pages' leading back link into the shared page header."""
    for child in children:
        if not isinstance(child, FT) or child.tag in {"form", "dialog", "details"}:
            continue
        if {"e-card", "a-card"} & set(child.attrs.get("class", "").split()):
            continue
        label = "".join(str(item) for item in child.children if isinstance(item, str)).strip()
        if child.tag == "a" and (label.startswith("←") or label.startswith("Back to ")):
            child.attrs["hidden"] = True
            return child.attrs.get("href")
        found = _legacy_back_link(child.children)
        if found:
            return found
    return None


def nav_section(path):
    """Map existing detail and utility routes to their primary navigation section."""
    path = path.split("?", 1)[0]
    part = path.split("/admin/sites/", 1)[-1].split("/")[1:]
    key = part[0] if part else ""
    if key == "coming-soon" and len(part) > 1:
        key = part[1]
    if key == "settings" and len(part) > 1 and part[1] == "seo":
        return "Online Store"
    if key == "settings" and len(part) > 1 and part[1] == "integrations":
        return "Integrations"
    return {
        "": "Home", "orders": "Orders", "drafts": "Orders", "abandoned-checkouts": "Orders",
        "deliveries": "Orders", "products": "Products", "collections": "Products",
        "inventory": "Products", "gift-cards": "Products", "customers": "Customers",
        "discounts": "Discounts",
        "pages": "Content", "blog-posts": "Content", "menus": "Content", "media": "Content", "content": "Content",
        "snippets": "Content", "redirects": "Online Store", "reviews": "Content", "placeholders": "Content", "inbox": "Content",
        "revenue": "Analytics", "analytics": "Analytics", "integrations": "Integrations",
        "marketing": "Marketing", "subscriptions": "Products",
        "build": "Online Store", "samples": "Online Store", "editor": "Online Store",
        "preferences": "Online Store", "settings": "Settings", "commerce": "Settings",
        "golive": "Settings", "live-credentials": "Settings", "billing": "Settings",
        "users": "Settings", "policies": "Settings", "notifications": "Settings",
        "search": "Home", "activity": "Settings",
    }.get(key, "Online Store")


# Settings routes that live outside /settings but belong to a directory entry.
_SETTINGS_ALIASES = {"/commerce/payments": "/settings/payments", "/commerce": "/settings/payments",
                     "/live-credentials": "/settings/payments", "/golive": "/settings/domains",
                     "/billing": "/settings/billing", "/users": "/settings/users"}


def _settings_nav(base: str, path: str):
    """The Shopify-style list of settings shown beside a settings page."""
    from app.admin_icons import lucide
    from app.settings_directory import SETTINGS_GROUPS, SETTINGS_ICONS

    local = path.split("?", 1)[0].removeprefix(base).rstrip("/")
    for prefix, target in _SETTINGS_ALIASES.items():
        if local == prefix or local.startswith(prefix + "/"):
            local = target
            break
    links = []
    for _group, _summary, entries in SETTINGS_GROUPS:
        for label, href, *_rest in entries:
            current = local == href or local.startswith(href + "/")
            links.append(A(lucide(SETTINGS_ICONS.get(href, "settings"), 18, 1.75), Span(label), href=base + href,
                           cls="p-settings-link" + (" is-active" if current else ""),
                           aria_current="page" if current else None))
    return Nav(A(ui_icon("chevron-left", 16), Span("All settings"), href=base + "/settings",
                 cls="p-settings-all"), *links, aria_label="Settings sections", cls="p-settings-nav")


def admin_page(title, *children, site=None, sites=(), user=None, path="/admin/sites",
               notice="", csrf_token=""):
    base = f"/admin/sites/{site.id}" if site else "/admin/sites"
    active = nav_section(path) if site else "Stores"
    def navlink(label, href, primary=False, selected=False):
        return A(icon(label) if primary else None, Span(label), href=href,
                 cls="a-nav-link" + (" is-active" if selected else ""),
                 aria_current="page" if selected else None)
    groups = [
        ("Home", base, []),
        ("Orders", base + "/orders", [("Drafts", base + "/drafts"), ("Abandoned checkouts", base + "/abandoned-checkouts")]),
        ("Products", base + "/products", [("Collections", base + "/collections"), ("Inventory", base + "/inventory"), ("Subscriptions", base + "/subscriptions"), ("Gift cards", base + "/gift-cards")]),
        ("Customers", base + "/customers", []),
        ("Discounts", base + "/discounts", []),
        ("Content", base + "/content/pages", [("Pages", base + "/content/pages"), ("Blog posts", base + "/content/blog-posts"), ("Menus", base + "/content/menus"), ("Files", base + "/content/files")]),
        ("Analytics", base + "/analytics", []),
        ("Online Store", base + "/editor", [("Editor", base + "/editor"), ("Guided builder", base + "/build"), ("Design", base + "/build?view=design"), ("Themes", base + "/themes"), ("Preferences", base + "/preferences"), ("URL redirects", base + "/redirects")]),
        ("Integrations", base + "/integrations", []),
        ("Settings", base + "/settings", []),
    ]
    navigation, pinned = [], []
    for label, href, subs in groups if site else []:
        target = pinned if label == "Settings" else navigation
        target.append(navlink(label, href, True, active == label))
        if active == label and subs:
            target.append(Div(*[navlink(text, url, selected=path == url)
                                for text, url in subs], cls="a-subnav"))
    name = site.name if site else "FastShop"
    initials = _initials(name)
    has_header = any(_has_page_header(child) for child in children)
    in_settings = bool(site) and active == "Settings"
    settings_nav = (_settings_nav(base, path) if in_settings
                    and path.split("?", 1)[0].rstrip("/") != base + "/settings" else None)
    legacy_back = None if has_header else _legacy_back_link(children)
    display_title = title.replace("—", "·").removesuffix(" · " + name)
    storefront = (f"https://{site.hostname}" if site and getattr(site, "hostname", None)
                  else f"/sites/{site.slug}" if site and getattr(site, "slug", None) else None)
    account = Details(
        Summary(Span(initials, cls="a-avatar"), Span(name, cls="p-account-name"),
                ui_icon("chevron", 16), aria_label="Store and account menu"),
        Div(
            Nav(Span("Stores", cls="p-menu-label"),
                *[A(Span(_initials(s.name), cls="a-avatar"),
                    Span(s.name), href=f"/admin/sites/{s.id}",
                    cls="p-menu-item" + (" is-current" if site and s.id == site.id else ""),
                    aria_current="page" if site and s.id == site.id else None) for s in sites],
                A("All stores", href="/admin/sites", cls="p-menu-item"), aria_label="Switch store"),
            Div(Span(user.email if user else "Account", cls="p-menu-label"),
                Form(Input(type="hidden", name="csrf_token", value=csrf_token),
                     Button("Sign out", type="submit", cls="p-menu-item"), action="/logout",
                     method="post"), cls="p-menu-section"),
            cls="p-menu"),
        cls="a-store-switcher a-user-menu p-account")
    return (
        Title(title.replace("\u2014", "·") + " · FastShop"),
        Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Meta(name="robots", content="noindex,nofollow"),
        Meta(name="referrer", content="no-referrer"),
        Link(rel="icon", href=static_asset("favicon.svg")),
        *[Link(rel="stylesheet", href=static_asset(asset))
          for asset in ("fonts.css", "site-editor.css", "admin/admin.css", "platform.css",
                        "admin/polaris.css")],
        Script(src=static_asset("admin/icons.js")),
        Script(src=static_asset("site-editor.js"), defer=True),
        Script(src=static_asset("admin/admin.js"), defer=True),
        Script(src=static_asset("admin/polish.js"), defer=True),
        Script(src=static_asset("admin/polaris.js"), defer=True),
        Div(A("Skip to content", href="#admin-main", cls="a-skip-link"),
            Header(
                Button(ui_icon("menu"), type="button", data_sidebar_toggle="",
                       aria_controls="admin-sidebar", aria_expanded="false",
                       aria_label="Open navigation", cls="p-menu-button"),
                A(Img(src=static_asset("favicon.svg"), alt="", width=24, height=24),
                  Span("FastShop"), href="/admin/sites", cls="p-brand", aria_label="FastShop home"),
                Form(ui_icon("search", 16),
                     Label(Span("Search", cls="a-sr-only"), Input(
                         id="admin-search", name="q", type="search", placeholder="Search",
                         autocomplete="off", role="combobox", aria_expanded="false",
                         aria_controls="admin-search-results", aria_autocomplete="list",
                         data_search_url=base + "/search")),
                     Span("Ctrl K", cls="p-kbd", aria_hidden="true"),
                     Div(id="admin-search-results", role="listbox", hidden=True),
                     action=base + "/search", method="get", cls="a-search p-search") if site else Div(),
                Div(A(ui_icon("external", 16), Span("View store"), href=storefront,
                      target="_blank", rel="noopener", cls="p-view-store") if storefront else None,
                    account, cls="p-topbar-end"),
                cls="p-topbar", aria_label="FastShop"),
            Aside(
                Nav(*navigation, aria_label="Main navigation") if site else Nav(
                    A("Stores", href="/admin/sites", cls="a-nav-link is-active", aria_current="page"),
                    aria_label="Main navigation"),
                Nav(*pinned, aria_label="Settings", cls="p-nav-pinned") if pinned else None,
                id="admin-sidebar", cls="a-sidebar", aria_label="Store administration"),
            Main(Div(*_with_settings_nav(
                         settings_nav,
                         None if has_header else page_header(display_title, back_href=legacy_back),
                         Div(Span(notice[:300]), Button(ui_icon("close", 16), type="button",
                             data_toast_dismiss="", aria_label="Dismiss notification"),
                             role="status", cls="a-toast") if notice else None,
                         *children), cls="a-content"), id="admin-main", cls="a-canvas"),
            Div(Span("Unsaved changes"), Div(
                Button("Discard", type="button", data_discard=""),
                Button("Save", type="button", data_save=""), cls="p-save-actions"),
                id="admin-save-bar", role="region", aria_label="Unsaved changes", hidden=True),
            cls="a-shell p-shell"),
    )


def _with_settings_nav(settings_nav, *content):
    if settings_nav is None:
        return content
    return (Div(settings_nav, Div(*content, cls="p-settings-main"), cls="p-settings-layout"),)


def contextual_page(title, *children, subnavigation=None):
    scope = admin_scope.get() or {}
    path = scope.get("path", "")
    if not path.startswith(("/admin/sites", "/admin/onboarding/")):
        from app.platform_ui import platform_page
        return platform_page(title, *children, subnavigation=subnavigation)
    session = scope.get("session", {})
    with SessionLocal() as db:
        user = db.get(User, session.get("user_id", ""))
        sites = list(db.scalars(select(Site).join(Membership, Membership.tenant_id == Site.tenant_id)
            .where(Membership.user_id == session.get("user_id", ""),
                   Membership.role.in_(["admin", "merchant", "editor"])).order_by(Site.name))) if user and user.is_active else []
        parts = path.strip("/").split("/")
        site_id = parts[2] if len(parts) > 2 else ""
        site = next((s for s in sites if s.id == site_id), None)
        query = parse_qs(scope.get("query_string", b"").decode())
        notice = query.get("notice", [""])[0]
        nav_path = path + "?view=design" if path.endswith("/build") and query.get("view") == ["design"] else path
        if site and user:
            from app.admin_gap_ui import activity_card, view_controls
            from app.admin_views import COLUMNS
            if len(parts) == 4 and parts[3] in COLUMNS:
                controls = view_controls(db, site, user.id, parts[3],
                    {k: values[-1] for k, values in query.items()},
                    Input(type="hidden", name="csrf_token", value=session.get("csrf_token", "")))
                children = (controls, *children)
            objects = {"products": "product", "collections": "collection", "customers": "customer", "orders": "order", "pages": "page", "drafts": "draft", "discounts": "discount"}
            if len(parts) == 5 and parts[3] in objects and parts[4] not in {"new", "import", "export", "bulk", "bulk-edit"}:
                identifier = parts[4]
                if parts[3] == "orders":
                    from app.models import SiteOrder
                    link = db.get(SiteOrder, identifier)
                    identifier = link.order_id if link and link.site_id == site.id else identifier
                children = (*children, activity_card(db, site, objects[parts[3]], identifier))
            if len(parts) == 6 and parts[3] == "content" and parts[4] in {"pages", "blog-posts"} and parts[5] != "new":
                children = (*children, activity_card(db, site, "page", parts[5]))
        return admin_page(title, *children, site=site, sites=sites, user=user,
                          path=nav_path, notice=notice, csrf_token=session.get("csrf_token", ""))
