"""Shared FastShop HTML/CSS identity for merchant and commerce screens."""

from fasthtml.common import H1, A, Div, Link, Meta, Script, Title

from app.ui import brand


def platform_page(title, *children, navigation=None, customer=False):
    navigation = navigation if navigation is not None else [
        A("Dashboard", href="/admin"), A("Sites & content", href="/admin/sites")]
    return (Title(title + " — FastShop"), Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Meta(name="robots", content="noindex,nofollow"), Meta(name="referrer", content="no-referrer"),
        Link(rel="icon", href="/static/favicon.svg"), Link(rel="stylesheet", href="/static/site.css"),
        Link(rel="stylesheet", href="/static/site-editor.css"),
        Script(src="/static/customer.js" if customer else "/static/site-editor.js", defer=True),
        Div(brand(), *navigation, cls="e-top"), Div(H1(title), *children, cls="e-main"))
