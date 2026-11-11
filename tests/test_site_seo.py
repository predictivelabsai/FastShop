"""SEO metadata and crawler controls use generic site settings."""

from types import SimpleNamespace

import pytest
from fastcore.xml import to_xml

from app.models import Site, SitePage
from app.services import CommerceError
from app.site_seo import metadata, robots_content, validate
from app.site_ui import storefront
from tests.test_site_editor_routes import workspace  # noqa: F401


def configure_public_site(w, settings, *, description=None):
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        page = db.get(SitePage, w.page.id)
        site.hostname = "seo.example.test"
        site.status = "published"
        site.published_settings_json = (site.published_settings_json or {}) | settings
        document = dict(page.draft_json or {})
        if description is not None:
            document["description"] = description
            page.draft_json = document
        page.published_json = document
        db.commit()


def render_home(w):
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        page = db.get(SitePage, w.page.id)
        nodes = storefront(
            db,
            site,
            page,
            f"/sites/{site.slug}",
            "fixed-csrf",
            "https://seo.example.test/",
        )
        return "".join(str(to_xml(node)) for node in nodes if node is not None)


def test_seo_head_output_for_generic_storefront(workspace):
    w = workspace
    configure_public_site(w, {
        "seo_indexing": True,
        "seo_default_description": "The default description",
        "seo_social_title": "Store social title",
        "seo_google_verification": "verify_123",
        "seo_bing_verification": "bing-123",
    }, description="")
    result = render_home(w)
    for marker in (
        'name="description"',
        'property="og:title"',
        'property="og:description"',
        'property="og:type"',
        'property="og:url"',
        'name="twitter:card"',
        'name="google-site-verification"',
        'name="msvalidate.01"',
        'rel="canonical" href="https://seo.example.test/"',
    ):
        assert marker in result
    assert "The default description" in result
    assert "Store social title" in result
    assert "verify_123" in result and "bing-123" in result


def test_seo_noindex_robots_custom_and_verification_validation(workspace):
    w = workspace
    config = validate({"seo_indexing": False})
    configure_public_site(w, config)
    output = render_home(w)
    assert 'name="robots" content="noindex"' in output
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        robots = robots_content(site, config)
        assert "Disallow: /" in robots
        assert "Sitemap: https://seo.example.test/sitemap.xml" in robots
        custom = robots_content(
            site, validate({"seo_robots_override": "User-agent: *\nAllow: /catalog"})
        )
    assert "Allow: /catalog" in custom and "Sitemap:" in custom
    with pytest.raises(CommerceError):
        validate({"seo_google_verification": "<script>bad"})
    with pytest.raises(CommerceError):
        validate({"seo_bing_verification": "bad token"})
    with pytest.raises(CommerceError):
        validate({"seo_robots_override": "<script>bad</script>"})
    with pytest.raises(CommerceError):
        validate({"seo_indexing": "false"})


def test_canonical_host_and_page_metadata_precedence():
    site = SimpleNamespace(
        name="Editor store",
        hostname="canonical.example.test",
        published_settings_json={
            "seo_title_format": "{page} | {store}",
            "seo_default_description": "Default",
            "seo_indexing": True,
        },
    )
    page = SimpleNamespace(path="/about")
    values = metadata(
        site,
        page,
        {"title": "About", "description": "Page description"},
        "https://wrong.example/about",
    )
    assert values["title"] == "About | Editor store"
    assert values["description"] == "Page description"
    assert values["canonical"] == "https://canonical.example.test/about"
