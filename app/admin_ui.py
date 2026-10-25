"""Shared merchant components and request-local shell context.

The ASGI context only selects presentation. Route handlers remain responsible for
authorisation; shell queries independently check active users and memberships.
"""

from contextvars import ContextVar
from urllib.parse import parse_qs

from fastcore.xml import FT, NotStr
from fasthtml.common import (
    H1,
    H2,
    A,
    Aside,
    Button,
    Details,
    Dialog,
    Div,
    Footer,
    Form,
    Header,
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
        Div(Div(A(NotStr('<svg viewBox="0 0 20 20" width="20" height="20" '
                        'fill="none" stroke="currentColor" stroke-width="1.7" '
                        'aria-hidden="true"><path d="m11 4-6 6 6 6M5 10h12"/></svg>'),
                  href=back_href, aria_label="Back", cls="a-back-button") if back_href else None,
                H1(title), *badges, cls="a-heading")),
        Div(*secondary_actions, primary_action, cls="a-actions"), cls="a-header")


def card(*children, title=None, **kwargs):
    return Div(H2(title) if title else None, *children, cls="a-card", **kwargs)


def card_section(*children):
    return Div(*children, cls="a-card-section")


def two_column(main, aside):
    return Div(Div(main), Aside(aside, aria_label="Details"), cls="a-columns")


def badge(text, tone="neutral"):
    if tone not in {"success", "info", "warning", "critical", "neutral"}:
        tone = "neutral"
    return Span(text, cls=f"a-badge a-badge-{tone}")


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


_ICONS = {
    "Home": '<path d="m3 10 9-7 9 7v11h-6v-7H9v7H3z"/>',
    "Orders": '<path d="M5 7h14l2 14H3L5 7Z"/><path d="M8 8V6a4 4 0 0 1 8 0v2"/>',
    "Products": '<path d="m3 3 9 1 9 9-8 8-9-9z"/><circle cx="8" cy="8" r="1"/>',
    "Customers": '<circle cx="12" cy="7" r="4"/><path d="M4 21v-3a8 8 0 0 1 16 0v3"/>',
    "Discounts": '<path d="m3 12 3-3V5h4l2-2 3 2h4v4l2 3-2 3v4h-4l-3 2-2-2H6v-4zM8 16l8-8"/><path d="M8 8h.01M16 16h.01"/>',
    "Content": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "Analytics": '<path d="M4 3v18h17M8 17v-5M13 17V7M18 17V4"/>',
    "Marketing/Integrations": '<path d="m3 11 17-7v16L3 13zM7 14l2 7h4l-3-6"/>',
    "Online Store": '<path d="M3 10V4h18v6M4 10v11h16V10M9 21v-7h6v7M2 10h20"/>',
    "Settings": '<path d="m9 3-1 3-3 1-2 4 2 2v4l4 2 3-1 3 1 4-2v-4l2-2-2-4-3-1-1-3z"/><circle cx="12" cy="12" r="3"/>',
}


def icon(name):
    return NotStr('<svg viewBox="0 0 24 24" width="18" height="18" fill="none" '
                  'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
                  'stroke-linejoin="round" aria-hidden="true">' + _ICONS[name] + '</svg>')


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
    return {
        "": "Home", "orders": "Orders", "drafts": "Orders", "abandoned-checkouts": "Orders",
        "deliveries": "Orders", "products": "Products", "collections": "Products",
        "inventory": "Products", "gift-cards": "Products", "customers": "Customers", "discounts": "Discounts",
        "pages": "Content", "blog-posts": "Content", "menus": "Content", "media": "Content", "content": "Content",
        "snippets": "Content", "redirects": "Content", "reviews": "Content", "placeholders": "Content", "inbox": "Content",
        "revenue": "Analytics", "analytics": "Analytics", "integrations": "Marketing/Integrations", "subscriptions": "Products",
        "build": "Online Store", "samples": "Online Store", "editor": "Online Store",
        "preferences": "Online Store", "settings": "Settings", "commerce": "Settings",
        "golive": "Settings", "live-credentials": "Settings", "billing": "Settings",
        "users": "Settings", "policies": "Settings", "notifications": "Settings",
        "search": "Home", "activity": "Settings",
    }.get(key, "Online Store")


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
        ("Content", base + "/content/pages", [("Pages", base + "/content/pages"), ("Blog posts", base + "/content/blog-posts"), ("Menus", base + "/content/menus"), ("Files", base + "/content/files"), ("URL redirects", base + "/redirects"), ("Activity", base + "/activity")]),
        ("Analytics", base + "/analytics", []),
        ("Marketing/Integrations", base + "/integrations", []),
        ("Online Store", base + "/editor", [("Editor", base + "/editor"), ("Guided builder", base + "/build"), ("Design", base + "/build?view=design"), ("Themes", base + "/themes"), ("Preferences", base + "/preferences")]),
    ]
    navigation = []
    for label, href, subs in groups if site else []:
        navigation.append(navlink(label, href, True, active == label))
        if active == label and subs:
            navigation.append(Div(*[navlink(text, url, selected=path == url)
                                    for text, url in subs], cls="a-subnav"))
    name = site.name if site else "FastShop"
    initials = "".join(word[0] for word in name.split()[:2]).upper()
    has_header = any(_has_page_header(child) for child in children)
    legacy_back = None if has_header else _legacy_back_link(children)
    display_title = title.replace("—", "·").removesuffix(" · " + name)
    return (
        Title(title.replace("—", "·") + " · FastShop"),
        Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Meta(name="robots", content="noindex,nofollow"),
        Meta(name="referrer", content="no-referrer"),
        Link(rel="icon", href=static_asset("favicon.svg")),
        *[Link(rel="stylesheet", href=static_asset(asset))
          for asset in ("fonts.css", "site-editor.css", "admin/admin.css")],
        Script(src=static_asset("site-editor.js"), defer=True),
        Script(src=static_asset("admin/admin.js"), defer=True),
        Script(src=static_asset("admin/polish.js"), defer=True),
        Div(A("Skip to content", href="#admin-main", cls="a-skip-link"),
            Aside(
                Details(Summary(Span(initials, cls="a-avatar"), Span(name), Span("⌄")),
                        Nav(A("All stores", href="/admin/sites"),
                            *[A(s.name, href=f"/admin/sites/{s.id}") for s in sites],
                            aria_label="Switch store"), cls="a-store-switcher"),
                Form(Label("Search store", Input(id="admin-search", name="q", type="search",
                    placeholder="Search  Ctrl K", autocomplete="off", role="combobox",
                    aria_expanded="false", aria_controls="admin-search-results",
                    aria_autocomplete="list", data_search_url=base + "/search")),
                    Div(id="admin-search-results", role="listbox", hidden=True),
                    action=base + "/search", method="get", cls="a-search") if site else None,
                Nav(*navigation, aria_label="Main navigation") if site else Nav(
                    A("Stores", href="/admin/sites", cls="a-nav-link is-active", aria_current="page"),
                    aria_label="Main navigation"),
                Footer(navlink("Settings", base + "/settings", True, active == "Settings") if site else None,
                       Details(Summary(Span((user.email[0] if user else "U").upper(), cls="a-avatar"),
                                       Span(user.email if user else "Account")),
                               Form(Input(type="hidden", name="csrf_token", value=csrf_token),
                                    Button("Sign out", type="submit"), action="/logout", method="post"),
                               cls="a-user-menu"),
                       cls="a-sidebar-footer"),
                id="admin-sidebar", cls="a-sidebar", aria_label="Store administration"),
            Main(Header(Button("☰", type="button", data_sidebar_toggle="",
                          aria_controls="admin-sidebar", aria_expanded="false", aria_label="Open navigation"),
                   Nav(A(name, href=base), Span("/", aria_hidden="true"),
                       Span(display_title), aria_label="Page breadcrumb"), cls="a-topbar"),
                 Div(None if has_header else page_header(display_title, back_href=legacy_back),
                     Div(Span(notice[:300]), Button("×", type="button", data_toast_dismiss="",
                         aria_label="Dismiss notification"), role="status", cls="a-toast") if notice else None,
                     *children, cls="a-content"), id="admin-main", cls="a-canvas"),
            Div(Span("Unsaved changes"), Button("Discard", type="button", data_discard=""),
                Button("Save", type="button", data_save=""), id="admin-save-bar",
                role="region", aria_label="Unsaved changes", hidden=True), cls="a-shell"),
    )


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
