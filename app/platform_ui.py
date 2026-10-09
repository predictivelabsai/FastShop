"""Shared FastShop HTML/CSS identity for merchant and commerce screens."""

from fasthtml.common import H1, A, Div, Header, Link, Meta, Nav, Script, Span, Title

from app.ui import brand


def platform_subnav(*groups, aria_label="Site workspace"):
    return Nav(
        *[
            Div(
                Span(label, cls="e-subnav-label"),
                Div(*links, cls="e-subnav-links"),
                cls="e-subnav-group",
            )
            for label, links in groups
        ],
        aria_label=aria_label,
        cls="e-subnav",
    )


def platform_page(title, *children, navigation=None, customer=False, subnavigation=None):
    navigation = navigation if navigation is not None else [
        A("Dashboard", href="/admin"), A("Sites & content", href="/admin/sites")]
    return (Title(title + " — FastShop"), Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Meta(name="robots", content="noindex,nofollow"), Meta(name="referrer", content="no-referrer"),
        Link(rel="icon", href="/static/favicon.svg"), Link(rel="stylesheet", href="/static/fonts.css"),
        Link(rel="stylesheet", href="/static/site.css"), Link(rel="stylesheet", href="/static/platform.css"),
        Link(rel="stylesheet", href="/static/site-editor.css"),
        Script(src="/static/customer.js" if customer else "/static/site-editor.js", defer=True),
        Header(brand(), Nav(*navigation, aria_label="Platform", cls="e-nav"), cls="e-top"),
        Div(H1(title), subnavigation, *children, cls="e-main"))
