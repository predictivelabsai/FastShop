"""Store SEO metadata and crawler controls are shared by both themes."""

import pytest
from test_site_seed_liquid import html, liquid  # noqa: F401

from app.services import CommerceError
from app.site_seo import metadata, robots_content, validate


@pytest.mark.parametrize("theme", ["generic", "liquid"])
def test_seo_head_output_for_public_theme(liquid, theme):
    db, site, _ = liquid
    site.hostname = "seo.example.test"
    site.status = "published"
    site.status = "published"
    site.published_settings_json = site.published_settings_json | {
        "theme_pack": theme, "seo_indexing": True,
        "seo_default_description": "The default description",
        "seo_social_title": "Store social title",
        "seo_google_verification": "verify_123",
        "seo_bing_verification": "bing-123",
    }
    db.commit()
    result = html(db, site)
    for marker in ('name="description"', 'property="og:title"', 'property="og:description"',
                   'property="og:type"', 'property="og:url"', 'name="twitter:card"', 'name="google-site-verification"',
                   'name="msvalidate.01"', 'rel="canonical" href="https://seo.example.test/"'):
        assert marker in result
    assert "verify_123" in result and "bing-123" in result


@pytest.mark.parametrize("theme", ["generic", "liquid"])
def test_seo_noindex_robots_custom_and_verification_validation(liquid, theme):
    db, site, _ = liquid
    site.hostname = "seo.example.test"
    site.status = "published"
    config = validate({"seo_indexing": False})
    site.published_settings_json = site.published_settings_json | config
    site.published_settings_json["theme_pack"] = theme
    output = html(db, site)
    assert 'name="robots" content="noindex"' in output
    robots = robots_content(site, config)
    assert "Disallow: /" in robots
    assert "Sitemap: https://seo.example.test/sitemap.xml" in robots
    custom = robots_content(site, validate({"seo_robots_override": "User-agent: *\nAllow: /catalog"}))
    assert "Allow: /catalog" in custom and "Sitemap:" in custom
    with pytest.raises(CommerceError):
        validate({"seo_google_verification": "<script>bad"})
    with pytest.raises(CommerceError):
        validate({"seo_bing_verification": "bad token"})
    with pytest.raises(CommerceError):
        validate({"seo_robots_override": "<script>bad</script>"})
    with pytest.raises(CommerceError):
        validate({"seo_indexing": "false"})


def test_canonical_host_and_page_metadata_precedence(liquid):
    _, site, _ = liquid
    site.hostname = "canonical.example.test"
    site.published_settings_json = {"seo_title_format": "{page} | {store}",
                                    "seo_default_description": "Default", "seo_indexing": True}
    page = type("Page", (), {"path": "/about", "kind": "content"})()
    values = metadata(site, page, {
        "title": "About",
        "seo_title": "Search title",
        "description": "Page description",
        "seo_description": "Search description",
        "seo_social_title": "Share title",
        "seo_social_image": "/site-media/store/share.png",
        "seo_noindex": True,
    }, "https://wrong.test/about")
    assert values["title"] == "Search title | " + site.name
    assert values["description"] == "Search description"
    assert values["social_title"] == "Share title"
    assert values["image"] == "/site-media/store/share.png"
    assert values["noindex"] is True
    assert values["og_type"] == "website"
    assert values["canonical"] == "https://canonical.example.test/about"
