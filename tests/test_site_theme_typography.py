"""Bounded Liquid typography tokens keep merchant input out of raw CSS."""

import pytest

from app.services import CommerceError
from app.site_theme import theme_style, validate_theme


def _liquid_style(theme=None):
    config = {"theme_pack": "liquid"}
    if theme is not None:
        config["design"] = theme
    return theme_style(config)


def test_liquid_typography_defaults_are_an_explicit_css_variable_snapshot():
    """The emitted defaults reproduce the Liquid pack's established type scale."""
    defaults = validate_theme({})["typography"]

    assert defaults == {
        "display_font": "theme",
        "body_font": "quicksand",
        "ui_font": "quicksand",
        "roles": {
            "h1": {"size": "80px", "mobile_size": "56px", "weight": "400", "line_height": "1.08", "letter_spacing": "0em"},
            "h2": {"size": "48px", "mobile_size": "40px", "weight": "400", "line_height": "1", "letter_spacing": "0em"},
            "h3": {"size": "24px", "mobile_size": "24px", "weight": "400", "line_height": "1.2", "letter_spacing": "0em"},
            "body": {"size": "16px", "mobile_size": "16px", "weight": "400", "line_height": "1.5", "letter_spacing": "0em"},
            "small": {"size": "12px", "mobile_size": "12px", "weight": "400", "line_height": "1.5", "letter_spacing": "0em"},
            "eyebrow": {"size": "11px", "mobile_size": "11px", "weight": "600", "line_height": "1.5", "letter_spacing": "0.2em"},
            "button": {"size": "14px", "mobile_size": "14px", "weight": "600", "line_height": "1.5", "letter_spacing": "0em"},
            "nav": {"size": "14px", "mobile_size": "30px", "weight": "400", "line_height": "1.5", "letter_spacing": "0em"},
            "footer_heading": {"size": "36px", "mobile_size": "36px", "weight": "400", "line_height": "1.1", "letter_spacing": "0em"},
            "footer_body": {"size": "16px", "mobile_size": "16px", "weight": "400", "line_height": "1.5", "letter_spacing": "0em"},
        },
    }

    style = _liquid_style()
    for expected in (
        '--liquid-font-display:"Liquid Italiana",Georgia,serif',
        '--liquid-font-body:"Liquid Quicksand",system-ui,sans-serif',
        '--liquid-font-ui:"Liquid Quicksand",system-ui,sans-serif',
        '--liquid-font-footer-heading:"Liquid Fraunces",Georgia,serif',
        "--liquid-type-h1-size:80px",
        "--liquid-type-h1-size-mobile:56px",
        "--liquid-type-footer-heading-size:36px",
        "--liquid-type-footer-heading-line-height:1.1",
    ):
        assert expected in style


def test_liquid_typography_uses_only_validated_values_in_css_variables():
    theme = validate_theme({
        "typography": {
            "display_font": "fraunces",
            "body_font": "system_sans",
            "ui_font": "italiana",
            "roles": {
                "footer_heading": {
                    "size": "28px",
                    "mobile_size": "",
                    "weight": "500",
                    "line_height": "1.2",
                    "letter_spacing": "0.01em",
                },
            },
        },
    })

    assert theme["typography"]["roles"]["h1"]["size"] == "80px"
    assert theme["typography"]["roles"]["footer_heading"] == {
        "size": "28px", "mobile_size": "", "weight": "500", "line_height": "1.2",
        "letter_spacing": "0.01em",
    }

    style = _liquid_style(theme)
    assert '--liquid-font-display:"Liquid Fraunces",Georgia,serif' in style
    assert "--liquid-font-body:system-ui,-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif" in style
    assert '--liquid-font-ui:"Liquid Italiana",Georgia,serif' in style
    assert '--liquid-font-footer-heading:"Liquid Fraunces",Georgia,serif' in style
    assert "--liquid-type-footer-heading-size:28px" in style
    assert "--liquid-type-footer-heading-size-mobile:28px" in style
    assert "--liquid-type-footer-heading-weight:500" in style
    assert "--liquid-type-footer-heading-line-height:1.2" in style
    assert "--liquid-type-footer-heading-letter-spacing:0.01em" in style


@pytest.mark.parametrize("typography", [
    {"display_font": "url(https://bad.test/font.woff2)"},
    {"roles": {"footer_heading": {"size": "1vh"}}},
    {"roles": {"footer_heading": {"size": "7px"}}},
    {"roles": {"footer_heading": {"size": "1200px"}}},
    {"roles": {"footer_heading": {"weight": "950"}}},
    {"roles": {"footer_heading": {"line_height": "calc(1; color:red)"}}},
    {"roles": {"footer_heading": {"letter_spacing": "1rem"}}},
    {"roles": {"unknown": {"size": "20px"}}},
    {"unexpected": "value"},
])
def test_liquid_typography_rejects_untrusted_css_and_out_of_range_values(typography):
    with pytest.raises(CommerceError):
        validate_theme({"typography": typography})


def test_non_liquid_legacy_theme_style_remains_empty_without_design_settings():
    assert theme_style({}) == ""
