import pytest
from fasthtml.common import A, Div, Img, Input, NotStr, Script, Span, Style, Title, to_xml

from app.services import CommerceError
from app.site_styled_words import (
    PRIMARY_COLOUR,
    apply_styled_words,
    styled_words_for,
    validate_styled_words,
)

RULES = [
    {"word": "One", "italic": True, "bold": False, "colour": PRIMARY_COLOUR},
    {"word": "Fizz", "italic": True, "bold": True, "colour": "#1e6fe0"},
]


def markup(node):
    return str(to_xml(apply_styled_words(node, RULES)))


def test_styled_words_only_replace_case_sensitive_whole_visible_words():
    result = markup(
        Div(
            "One one Someone Phone OnePlus Fizz",
            A("One", href="/One?word=Fizz", title="Fizz"),
            Img(src="/Fizz.png", alt="One Fizz"),
            Input(value="One", placeholder="Fizz"),
            Title("One Fizz"),
            Script(NotStr('{"name":"One Fizz"}'), type="application/ld+json"),
            Style(NotStr(".One { color: blue; }")),
            Span("One", cls="manual-emphasis"),
        )
    )

    assert result.count('class="fs-styled-word"') == 3
    assert '<span data-styled-word="true"' in result
    assert "one Someone Phone OnePlus" in result
    assert 'href="/One?word=Fizz"' in result and 'title="Fizz"' in result
    assert 'src="/Fizz.png" alt="One Fizz"' in result
    assert 'value="One" placeholder="Fizz"' in result
    assert "<title>One Fizz</title>" in result
    assert '{"name":"One Fizz"}' in result and ".One { color: blue; }" in result
    assert '<span class="manual-emphasis">One</span>' in result


def test_styled_words_transform_rich_text_without_touching_attributes_or_double_wrapping():
    node = Div(
        NotStr(
            '<p title="One">One <a href="/Fizz">Fizz</a></p><span class="fs-styled-word">One</span><span class="bottle-one">Fizz</span><span style="color:red">One</span>'
        ),
        cls="fs-rich-content",
    )

    once = markup(node)
    twice = str(to_xml(apply_styled_words(node, RULES)))

    assert once == twice
    assert once.count('class="fs-styled-word"') == 3
    assert 'title="One"' in once and 'href="/Fizz"' in once
    assert '<span class="fs-styled-word">One</span>' in once
    assert '<span class="bottle-one">Fizz</span>' in once
    assert '<span style="color:red">One</span>' in once


def test_styled_words_reject_unbounded_or_unsafe_configuration():
    assert validate_styled_words(RULES)[1]["colour"] == "#1e6fe0"
    for invalid in (
        [{"word": "One two", "italic": True, "bold": False, "colour": PRIMARY_COLOUR}],
        [{"word": "One", "italic": "yes", "bold": False, "colour": PRIMARY_COLOUR}],
        [{"word": "One", "italic": True, "bold": False, "colour": "url(javascript:)"}],
    ):
        with pytest.raises(CommerceError, match="Styled word"):
            validate_styled_words(invalid)


def test_styled_words_fail_closed_for_malformed_legacy_site_settings():
    assert styled_words_for([]) == []
    assert styled_words_for({"styled_words": "One"}) == []
