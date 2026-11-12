"""Bounded Liquid typography tokens keep merchant input out of raw CSS."""

import pytest

from app.services import CommerceError
from app.site_theme import DEFAULTS, theme_style, validate_liquid_theme, validate_theme


def _liquid_style(theme=None):
    config = {"theme_pack": "liquid"}
    if theme is not None:
        config["design"] = theme
    return theme_style(config)


def test_liquid_typography_defaults_are_an_explicit_css_variable_snapshot():
    """The emitted defaults reproduce the Liquid pack's established type scale."""
    defaults = validate_liquid_theme({})["typography"]

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

    expected_style = [
        '--liquid-font-display:"Liquid Italiana",Georgia,serif',
        '--liquid-font-heading-content:"Liquid Quicksand",system-ui,sans-serif',
        '--liquid-font-body:"Liquid Quicksand",system-ui,sans-serif',
        '--liquid-font-ui:"Liquid Quicksand",system-ui,sans-serif',
        '--liquid-font-footer-heading:"Liquid Fraunces",Georgia,serif',
    ]
    for role, values in defaults["roles"].items():
        name = role.replace("_", "-")
        expected_style.extend((
            f"--liquid-type-{name}-size:{values['size']}",
            f"--liquid-type-{name}-size-mobile:{values['mobile_size']}",
            f"--liquid-type-{name}-weight:{values['weight']}",
            f"--liquid-type-{name}-line-height:{values['line_height']}",
            f"--liquid-type-{name}-letter-spacing:{values['letter_spacing']}",
        ))
    assert _liquid_style().split(";")[:len(expected_style)] == expected_style


def test_liquid_typography_uses_only_validated_values_in_css_variables():
    theme = validate_liquid_theme({
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
    assert '--liquid-font-heading-content:"Liquid Fraunces",Georgia,serif' in style
    assert "--liquid-font-body:system-ui,-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif" in style
    assert '--liquid-font-ui:"Liquid Italiana",Georgia,serif' in style
    assert '--liquid-font-footer-heading:"Liquid Fraunces",Georgia,serif' in style
    assert "--liquid-type-footer-heading-size:28px" in style
    assert "--liquid-type-footer-heading-size-mobile:28px" in style
    assert "--liquid-type-footer-heading-weight:500" in style
    assert "--liquid-type-footer-heading-line-height:1.2" in style
    assert "--liquid-type-footer-heading-letter-spacing:0.01em" in style


def test_liquid_header_footer_defaults_are_an_explicit_css_variable_snapshot():
    """The chrome controls retain the established Liquid shell when left untouched."""
    defaults = validate_liquid_theme({})["header_footer"]

    assert defaults == {
        "header": {
            "background": "#13131c", "text": "#eff2f7", "link": "#eff2f7",
            "link_hover": "#eff2f7", "border": "#534c5d", "padding_y": "16px",
            "logo_size": "144px", "sticky": True,
        },
        "announcement": {"background": "#241f2b", "text": "#eff2f7"},
        "footer": {
            "background": "#0a0a13", "text": "#eff2f7", "link": "#eff2f7",
            "link_hover": "#eff2f7", "divider": "#2d2e38", "padding_y": "64px",
            "logo_size": "144px", "column_gap": "64px",
        },
        "newsletter": {
            "background": "#0a0a13", "input_background": "#0a0a13",
            "input_text": "#eff2f7", "input_border": "#534c5d",
            "button_background": "#eff2f7", "button_text": "#0b121d",
        },
        "bottom": {
            "background": "#0a0a13", "legal_text": "#eff2f7", "legal_link": "#eff2f7",
            "disclaimer": "#eff2f7", "divider": "#2d2e38",
        },
    }

    expected = {
        "--liquid-header-background:var(--background)",
        "--liquid-header-text:var(--foreground)",
        "--liquid-header-link:var(--foreground)",
        "--liquid-header-link-hover:var(--foreground)",
        "--liquid-header-border:var(--border)",
        "--liquid-header-padding-y:var(--liquid-header-padding-y-default)",
        "--liquid-header-logo-size:144px",
        "--liquid-header-sticky:1",
        "--liquid-announcement-background:var(--card)",
        "--liquid-announcement-text:var(--foreground)",
        "--liquid-footer-background:var(--footer-surface)",
        "--liquid-footer-text:var(--foreground)",
        "--liquid-footer-link:var(--foreground)",
        "--liquid-footer-link-hover:var(--foreground)",
        "--liquid-footer-divider:color-mix(in oklch,var(--foreground) 15%,transparent)",
        "--liquid-footer-padding-y:64px",
        "--liquid-footer-logo-size:144px",
        "--liquid-footer-column-gap:64px",
        "--liquid-footer-newsletter-background:transparent",
        "--liquid-footer-newsletter-input-background:transparent",
        "--liquid-footer-newsletter-input-text:var(--foreground)",
        "--liquid-footer-newsletter-input-border:var(--border)",
        "--liquid-footer-newsletter-button-background:var(--metal-sheen)",
        "--liquid-footer-newsletter-button-text:var(--primary-foreground)",
        "--liquid-footer-bottom-background:transparent",
        "--liquid-footer-bottom-legal-text:var(--foreground)",
        "--liquid-footer-bottom-legal-link:var(--foreground)",
        "--liquid-footer-bottom-disclaimer:var(--foreground)",
        "--liquid-footer-bottom-divider:var(--liquid-footer-divider)",
    }
    style = set(_liquid_style().split(";"))
    assert expected <= style


def test_liquid_header_footer_uses_only_validated_values_in_css_variables():
    theme = validate_liquid_theme({
        "header_footer": {
            "header": {
                "background": "rgb(12, 34, 56)", "text": "#edf0f2", "link": "#d6dde5",
                "link_hover": "#ffffff", "border": "#45505c", "padding_y": "1.5rem",
                "logo_size": "168px", "sticky": False,
            },
            "announcement": {"background": "#2b3c4d", "text": "#ffffff"},
            "footer": {
                "background": "#142332", "text": "#e5edf5", "link": "#c9d7e4",
                "link_hover": "#ffffff", "divider": "#3a4a5a", "padding_y": "72px",
                "logo_size": "9rem", "column_gap": "3rem",
            },
            "newsletter": {
                "background": "#203040", "input_background": "#102030",
                "input_text": "#f0f4f8", "input_border": "#456789",
                "button_background": "#dbeafe", "button_text": "#102030",
            },
            "bottom": {
                "background": "#09111a", "legal_text": "#d8e1ea", "legal_link": "#ffffff",
                "disclaimer": "#b9c6d2", "divider": "#2f4050",
            },
        },
    })

    assert theme["header_footer"]["header"]["sticky"] is False
    assert theme["header_footer"]["header"]["background"] == "rgb(12, 34, 56)"
    assert theme["header_footer"]["footer"]["logo_size"] == "9rem"

    style = _liquid_style(theme)
    for expected in (
        "--liquid-header-background:rgb(12, 34, 56)",
        "--liquid-header-padding-y:1.5rem",
        "--liquid-header-logo-size:168px",
        "--liquid-header-sticky:0",
        "--liquid-announcement-background:#2b3c4d",
        "--liquid-footer-background:#142332",
        "--liquid-footer-padding-y:72px",
        "--liquid-footer-logo-size:9rem",
        "--liquid-footer-column-gap:3rem",
        "--liquid-footer-newsletter-input-background:#102030",
        "--liquid-footer-newsletter-button-background:#dbeafe",
        "--liquid-footer-bottom-legal-link:#ffffff",
    ):
        assert expected in style


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
        validate_liquid_theme({"typography": typography})


@pytest.mark.parametrize("header_footer", [
    {"header": {"background": "#ffffff;--unsafe:1"}},
    {"header": {"background": "url(https://bad.test/colour)"}},
    {"header": {"background": "rgb(12, 34, 256)"}},
    {"header": {"background": "rgb(1, 2 3)"}},
    {"header": {"background": "rgb(1 2, 3)"}},
    {"header": {"padding_y": "calc(1px + 1rem)"}},
    {"header": {"padding_y": "97px"}},
    {"header": {"logo_size": "361px"}},
    {"footer": {"column_gap": "10.1rem"}},
    {"header": {"sticky": "false"}},
    {"header": {"unexpected": "#ffffff"}},
    {"unexpected": {"background": "#ffffff"}},
])
def test_liquid_header_footer_rejects_untrusted_css_and_out_of_range_values(header_footer):
    with pytest.raises(CommerceError):
        validate_liquid_theme({"header_footer": header_footer})


def test_generic_theme_validation_retains_the_legacy_shape():
    assert set(validate_theme({})) == set(DEFAULTS)
    with pytest.raises(CommerceError):
        validate_theme({"typography": {}})


def test_non_liquid_legacy_theme_style_remains_empty_without_design_settings():
    assert theme_style({}) == ""
