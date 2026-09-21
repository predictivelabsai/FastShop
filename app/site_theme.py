"""Bounded storefront design tokens; never arbitrary merchant CSS."""

import re

from app.services import CommerceError

DEFAULTS = {"accent": "#1554cc", "background": "#fafaf7", "surface": "#e8f4fb",
            "text": "#101922", "font": "modern", "headings": "editorial",
            "spacing": "comfortable", "radius": "subtle", "width": "wide", "hero": "left"}
CHOICES = {"font": {"modern": "Arial, sans-serif", "humanist": "Verdana, sans-serif"},
           "headings": {"editorial": "Georgia, serif", "modern": "Arial, sans-serif"},
           "spacing": {"compact": "55px", "comfortable": "90px", "airy": "120px"},
           "radius": {"square": "0px", "subtle": "3px", "rounded": "18px"},
           "width": {"narrow": "960px", "wide": "1280px"},
           "hero": {"left": "left", "center": "center"}}
PRESETS = {
    "original": DEFAULTS,
    "warm": DEFAULTS | {"accent": "#26543d", "background": "#fff9ee", "surface": "#eee7d8", "text": "#27382e", "radius": "rounded"},
    "minimal": DEFAULTS | {"accent": "#202020", "background": "#ffffff", "surface": "#f1f1f1", "text": "#202020", "headings": "modern", "spacing": "airy"},
    "bold": DEFAULTS | {"accent": "#7e22ce", "background": "#faf5ff", "surface": "#eee0fb", "text": "#29163b", "headings": "modern", "hero": "center"},
}


def validate_theme(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise CommerceError("Choose supported design settings.")
    result = DEFAULTS | value
    for key, item in result.items():
        if not isinstance(item, str):
            raise CommerceError("Design values must be text.")
        if key in CHOICES:
            if item not in CHOICES[key]:
                raise CommerceError("Choose a supported " + key + ".")
        elif not re.fullmatch(r"#[0-9a-fA-F]{6}", item):
            raise CommerceError("Colors must use six-digit hex values.")
    return result


def theme_style(config):
    if not config.get("design"):
        return ""  # Existing sites retain their exact legacy theme.
    try:
        theme = validate_theme(config["design"])
    except CommerceError:
        return ""
    return ";".join(f"--store-{key}:{CHOICES[key][value] if key in CHOICES else value}" for key, value in theme.items())
