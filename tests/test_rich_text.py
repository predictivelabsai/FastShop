from app import catalog_admin, marketing_email, site_widgets
from app.rich_text import sanitise_rich_text


def test_rich_text_allow_list_keeps_supported_structure_and_cleans_xss():
    dirty = (
        '<div class="merchant" style="color:red"><h2 onclick="run()">Privacy</h2>'
        '<p style="display:none">Read <b>bold</b>, <i>italic</i>, <u>underlined</u>.'
        '<script>alert(1)</script><img src=x onerror="run()"></p>'
        '<ul><li>First</li><li>Second</li></ul><blockquote>A quotation</blockquote><hr>'
        '<p><a href="javascript:alert(1)">Unsafe link</a></p></div>'
    )

    cleaned = sanitise_rich_text(dirty)

    assert cleaned == (
        "<h2>Privacy</h2><p>Read <b>bold</b>, <i>italic</i>, <u>underlined</u>."
        "</p><ul><li>First</li><li>Second</li></ul><blockquote>A quotation</blockquote>"
        "<hr><p><a>Unsafe link</a></p>"
    )
    assert all(token not in cleaned for token in ("script", "alert(1)", "onclick", "onerror", "style=", "class="))


def test_rich_text_links_keep_safe_destinations_and_force_blank_rel():
    cleaned = sanitise_rich_text(
        '<a href="https://example.test/path" title="Read more" target="_blank" rel="nofollow">Web</a>'
        '<a href="mailto:hello@example.test">Mail</a>'
        '<a href="tel:+441234567890">Phone</a>'
        '<a href="../returns?from=policy">Relative</a>'
        '<a href="java&#x0a;script:alert(1)" target="_blank">Bad</a>'
    )

    assert '<a href="https://example.test/path" title="Read more" target="_blank" rel="nofollow noopener noreferrer">Web</a>' in cleaned
    assert '<a href="mailto:hello@example.test">Mail</a>' in cleaned
    assert '<a href="tel:+441234567890">Phone</a>' in cleaned
    assert '<a href="../returns?from=policy">Relative</a>' in cleaned
    assert '<a target="_blank" rel="noopener noreferrer">Bad</a>' in cleaned
    assert "javascript:" not in cleaned


def test_widget_catalogue_and_email_sanitizers_share_the_allow_list():
    dirty = '<h4>Heading</h4><p><a href="/terms" target="_blank">Terms</a></p>'
    expected = sanitise_rich_text(dirty)

    assert site_widgets.sanitise_html(dirty) == expected
    assert catalog_admin.sanitise_html(dirty) == expected
    assert marketing_email.sanitize_email_html(dirty) == expected
