"""Bounded storefront design tokens; never arbitrary merchant CSS."""

import copy
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
_PRESET_VALUES = {
    "original": DEFAULTS,
    "warm": DEFAULTS | {"accent": "#26543d", "background": "#fff9ee", "surface": "#eee7d8", "text": "#27382e", "radius": "rounded"},
    "minimal": DEFAULTS | {"accent": "#202020", "background": "#ffffff", "surface": "#f1f1f1", "text": "#202020", "headings": "modern", "spacing": "airy"},
    "bold": DEFAULTS | {"accent": "#7e22ce", "background": "#faf5ff", "surface": "#eee0fb", "text": "#29163b", "headings": "modern", "hero": "center"},
}

# Packs are application-owned assets, never a merchant stylesheet URL.
THEME_PACKS = frozenset({"liquid"})

# The values saved to a site are identifiers.  CSS font stacks stay application-owned.
FONT_CHOICES = {
    "theme": "Liquid theme default",
    "italiana": "Liquid Italiana",
    "fraunces": "Liquid Fraunces",
    "quicksand": "Liquid Quicksand",
    "system_sans": "System sans-serif",
    "system_serif": "System serif",
}
FONT_STACKS = {
    "italiana": '"Liquid Italiana",Georgia,serif',
    "fraunces": '"Liquid Fraunces",Georgia,serif',
    "quicksand": '"Liquid Quicksand",system-ui,sans-serif',
    "system_sans": 'system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
    "system_serif": 'Georgia,"Times New Roman",serif',
}
DEFAULT_FONT_STACKS = {
    "display": FONT_STACKS["italiana"],
    # Some existing content headings use the body face. Keep that legacy default
    # while making them follow a merchant's deliberate display-font choice.
    "heading_content": FONT_STACKS["quicksand"],
    "body": FONT_STACKS["quicksand"],
    "ui": FONT_STACKS["quicksand"],
    "footer_heading": FONT_STACKS["fraunces"],
}
TYPOGRAPHY_ROLE_LABELS = {
    "h1": "H1 / hero",
    "h2": "H2 / section title",
    "h3": "H3 / card title",
    "body": "Body",
    "small": "Small / fine print",
    "eyebrow": "Eyebrow",
    "button": "Button",
    "nav": "Navigation links",
    "footer_heading": "Footer headings",
    "footer_body": "Footer body",
}
TYPOGRAPHY_ROLE_DEFAULTS = {
    "h1": {"size": "80px", "mobile_size": "56px", "weight": "400",
           "line_height": "1.08", "letter_spacing": "0em"},
    "h2": {"size": "48px", "mobile_size": "40px", "weight": "400",
           "line_height": "1", "letter_spacing": "0em"},
    "h3": {"size": "24px", "mobile_size": "24px", "weight": "400",
           "line_height": "1.2", "letter_spacing": "0em"},
    "body": {"size": "16px", "mobile_size": "16px", "weight": "400",
             "line_height": "1.5", "letter_spacing": "0em"},
    "small": {"size": "12px", "mobile_size": "12px", "weight": "400",
              "line_height": "1.5", "letter_spacing": "0em"},
    "eyebrow": {"size": "11px", "mobile_size": "11px", "weight": "600",
                 "line_height": "1.5", "letter_spacing": "0.2em"},
    "button": {"size": "14px", "mobile_size": "14px", "weight": "600",
               "line_height": "1.5", "letter_spacing": "0em"},
    "nav": {"size": "14px", "mobile_size": "30px", "weight": "400",
            "line_height": "1.5", "letter_spacing": "0em"},
    "footer_heading": {"size": "36px", "mobile_size": "36px", "weight": "400",
                       "line_height": "1.1", "letter_spacing": "0em"},
    "footer_body": {"size": "16px", "mobile_size": "16px", "weight": "400",
                    "line_height": "1.5", "letter_spacing": "0em"},
}
TYPOGRAPHY_FIELDS = ("size", "mobile_size", "weight", "line_height", "letter_spacing")
_SIZE_RE = re.compile(r"(?P<number>(?:0|[1-9]\d*)(?:\.\d+)?)(?P<unit>px|rem)")
_TRACKING_RE = re.compile(r"(?P<number>-?(?:0|[1-9]\d*)(?:\.\d+)?)(?P<unit>px|em)")
_LINE_HEIGHT_RE = re.compile(r"(?:0|[1-9]\d*)(?:\.\d+)?")
_WEIGHTS = frozenset(str(value) for value in range(100, 1000, 100))

# These values are deliberately literal, so the editor can present a familiar colour
# swatch. The serializer maps unchanged defaults back to the application's existing
# Liquid tokens. That keeps the current tone-aware palette and metallic CTA gradient
# intact while allowing merchants to choose only bounded colour and length values.
HEADER_FOOTER_DEFAULTS = {
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
HEADER_FOOTER_COLOUR_FIELDS = {
    "header": ("background", "text", "link", "link_hover", "border"),
    "announcement": ("background", "text"),
    "footer": ("background", "text", "link", "link_hover", "divider"),
    "newsletter": (
        "background", "input_background", "input_text", "input_border", "button_background",
        "button_text",
    ),
    "bottom": ("background", "legal_text", "legal_link", "disclaimer", "divider"),
}
HEADER_FOOTER_LENGTH_BOUNDS = {
    ("header", "padding_y"): ((0, 96), (0, 6)),
    ("header", "logo_size"): ((32, 360), (2, 22.5)),
    ("footer", "padding_y"): ((0, 160), (0, 10)),
    ("footer", "logo_size"): ((32, 360), (2, 22.5)),
    ("footer", "column_gap"): ((0, 160), (0, 10)),
}
HEADER_FOOTER_CSS_VARIABLES = {
    ("header", "background"): "--liquid-header-background",
    ("header", "text"): "--liquid-header-text",
    ("header", "link"): "--liquid-header-link",
    ("header", "link_hover"): "--liquid-header-link-hover",
    ("header", "border"): "--liquid-header-border",
    ("header", "padding_y"): "--liquid-header-padding-y",
    ("header", "logo_size"): "--liquid-header-logo-size",
    ("header", "sticky"): "--liquid-header-sticky",
    ("announcement", "background"): "--liquid-announcement-background",
    ("announcement", "text"): "--liquid-announcement-text",
    ("footer", "background"): "--liquid-footer-background",
    ("footer", "text"): "--liquid-footer-text",
    ("footer", "link"): "--liquid-footer-link",
    ("footer", "link_hover"): "--liquid-footer-link-hover",
    ("footer", "divider"): "--liquid-footer-divider",
    ("footer", "padding_y"): "--liquid-footer-padding-y",
    ("footer", "logo_size"): "--liquid-footer-logo-size",
    ("footer", "column_gap"): "--liquid-footer-column-gap",
    ("newsletter", "background"): "--liquid-footer-newsletter-background",
    ("newsletter", "input_background"): "--liquid-footer-newsletter-input-background",
    ("newsletter", "input_text"): "--liquid-footer-newsletter-input-text",
    ("newsletter", "input_border"): "--liquid-footer-newsletter-input-border",
    ("newsletter", "button_background"): "--liquid-footer-newsletter-button-background",
    ("newsletter", "button_text"): "--liquid-footer-newsletter-button-text",
    ("bottom", "background"): "--liquid-footer-bottom-background",
    ("bottom", "legal_text"): "--liquid-footer-bottom-legal-text",
    ("bottom", "legal_link"): "--liquid-footer-bottom-legal-link",
    ("bottom", "disclaimer"): "--liquid-footer-bottom-disclaimer",
    ("bottom", "divider"): "--liquid-footer-bottom-divider",
}
# These are application-owned CSS snippets, selected only for the matching default.
# Merchant input is always validated as a literal hex or rgb colour before serialisation.
HEADER_FOOTER_STYLE_DEFAULTS = {
    ("header", "background"): "var(--background)",
    ("header", "text"): "var(--foreground)",
    ("header", "link"): "var(--foreground)",
    ("header", "link_hover"): "var(--foreground)",
    ("header", "border"): "var(--border)",
    ("header", "padding_y"): "var(--liquid-header-padding-y-default)",
    ("announcement", "background"): "var(--card)",
    ("announcement", "text"): "var(--foreground)",
    ("footer", "background"): "var(--footer-surface)",
    ("footer", "text"): "var(--foreground)",
    ("footer", "link"): "var(--foreground)",
    ("footer", "link_hover"): "var(--foreground)",
    ("footer", "divider"): "color-mix(in oklch,var(--foreground) 15%,transparent)",
    ("newsletter", "background"): "transparent",
    ("newsletter", "input_background"): "transparent",
    ("newsletter", "input_text"): "var(--foreground)",
    ("newsletter", "input_border"): "var(--border)",
    ("newsletter", "button_background"): "var(--metal-sheen)",
    ("newsletter", "button_text"): "var(--primary-foreground)",
    ("bottom", "background"): "transparent",
    ("bottom", "legal_text"): "var(--foreground)",
    ("bottom", "legal_link"): "var(--foreground)",
    ("bottom", "disclaimer"): "var(--foreground)",
    ("bottom", "divider"): "var(--liquid-footer-divider)",
}
HEADER_FOOTER_SCHEMA = {
    "sections": [
        {
            "id": "header", "label": "Header", "fields": [
                {"id": "background", "label": "Background colour", "type": "colour"},
                {"id": "text", "label": "Text colour", "type": "colour"},
                {"id": "link", "label": "Link colour", "type": "colour"},
                {"id": "link_hover", "label": "Link hover colour", "type": "colour"},
                {"id": "border", "label": "Border and divider colour", "type": "colour"},
                {"id": "padding_y", "label": "Vertical padding", "type": "text",
                 "help": "Use px or rem, from 0px to 96px."},
                {"id": "logo_size", "label": "Logo width", "type": "text",
                 "help": "Use px or rem, from 32px to 360px."},
                {"id": "sticky", "label": "Keep header sticky", "type": "toggle"},
            ],
        },
        {
            "id": "announcement", "label": "Announcement bar", "fields": [
                {"id": "background", "label": "Background colour", "type": "colour"},
                {"id": "text", "label": "Text colour", "type": "colour"},
            ],
        },
        {
            "id": "footer", "label": "Footer", "fields": [
                {"id": "background", "label": "Background colour", "type": "colour"},
                {"id": "text", "label": "Text colour", "type": "colour"},
                {"id": "link", "label": "Link colour", "type": "colour"},
                {"id": "link_hover", "label": "Link hover colour", "type": "colour"},
                {"id": "divider", "label": "Border and divider colour", "type": "colour"},
                {"id": "padding_y", "label": "Vertical padding", "type": "text",
                 "help": "Use px or rem, from 0px to 160px."},
                {"id": "logo_size", "label": "Logo width", "type": "text",
                 "help": "Use px or rem, from 32px to 360px."},
                {"id": "column_gap", "label": "Column gap", "type": "text",
                 "help": "Use px or rem, from 0px to 160px."},
            ],
        },
        {
            "id": "newsletter", "label": "Newsletter block", "fields": [
                {"id": "background", "label": "Block background colour", "type": "colour"},
                {"id": "input_background", "label": "Input background colour", "type": "colour"},
                {"id": "input_text", "label": "Input text colour", "type": "colour"},
                {"id": "input_border", "label": "Input border colour", "type": "colour"},
                {"id": "button_background", "label": "Button background colour", "type": "colour",
                 "help": "The default retains the existing metallic gradient."},
                {"id": "button_text", "label": "Button text colour", "type": "colour"},
            ],
        },
        {
            "id": "bottom", "label": "Footer bottom bar", "fields": [
                {"id": "background", "label": "Background colour", "type": "colour"},
                {"id": "legal_text", "label": "Legal line colour", "type": "colour"},
                {"id": "legal_link", "label": "Legal link colour", "type": "colour"},
                {"id": "disclaimer", "label": "Disclaimer colour", "type": "colour"},
                {"id": "divider", "label": "Divider colour", "type": "colour"},
            ],
        },
    ],
}
_COLOUR_RE = re.compile(r"#[0-9a-fA-F]{6}")
_RGB_RE = re.compile(
    r"rgb\(\s*(\d{1,3})(?:\s*,\s*|\s+)(\d{1,3})(?:\s*,\s*|\s+)(\d{1,3})\s*\)",
    re.IGNORECASE,
)


def theme_pack(config):
    value = config.get("theme_pack")
    return value if isinstance(value, str) and value in THEME_PACKS else ""


def typography_defaults():
    """Return an independent default payload for the persisted theme JSON."""
    return {"display_font": "theme", "body_font": "quicksand", "ui_font": "quicksand",
            "roles": copy.deepcopy(TYPOGRAPHY_ROLE_DEFAULTS)}


def header_footer_defaults():
    """Return an independent, bounded header and footer style payload."""
    return copy.deepcopy(HEADER_FOOTER_DEFAULTS)


def theme_defaults():
    return DEFAULTS | {
        "typography": typography_defaults(), "header_footer": header_footer_defaults(),
    }


def typography_schema():
    """Editor metadata for bounded controls, never values interpreted as CSS."""
    fields = [
        {"id": "size", "label": "Desktop size", "type": "text",
         "help": "Use px or rem, for example 36px or 2.25rem."},
        {"id": "mobile_size", "label": "Mobile size (optional)", "type": "text",
         "help": "Use px or rem. Leave blank to use the desktop size."},
        {"id": "weight", "label": "Weight", "type": "select",
         "options": {value: value for value in sorted(_WEIGHTS)}},
        {"id": "line_height", "label": "Line height", "type": "text",
         "help": "Use a unitless number from 0.8 to 3."},
        {"id": "letter_spacing", "label": "Letter spacing", "type": "text",
         "help": "Use em or px, for example 0.02em."},
    ]
    return {
        "font_fields": [
            {"id": "display_font", "label": "Display and headings font", "type": "select",
             "options": FONT_CHOICES},
            {"id": "body_font", "label": "Body font", "type": "select", "options": FONT_CHOICES},
            {"id": "ui_font", "label": "UI and button font", "type": "select",
             "options": FONT_CHOICES},
        ],
        "roles": [
            {"id": role, "label": label, "fields": copy.deepcopy(fields)}
            for role, label in TYPOGRAPHY_ROLE_LABELS.items()
        ],
    }


def header_footer_schema():
    """Editor metadata for the validated Liquid chrome controls."""
    return copy.deepcopy(HEADER_FOOTER_SCHEMA)


def theme_form_values(form, *, include_typography=False, include_header_footer=False):
    """Read the classical form into the same bounded JSON shape as the editor API."""
    result = {key: str(form.get(key, "")) for key in DEFAULTS}
    if not include_typography:
        return result
    typography = typography_defaults()
    for key in ("display_font", "body_font", "ui_font"):
        typography[key] = str(form.get(f"typography.{key}", typography[key]))
    for role, defaults in typography["roles"].items():
        for field, default in defaults.items():
            name = f"typography.roles.{role}.{field}"
            typography["roles"][role][field] = str(form.get(name, default))
    result |= {"typography": typography}
    if not include_header_footer:
        return result
    header_footer = header_footer_defaults()
    for section, fields in header_footer.items():
        for field, default in fields.items():
            name = f"header_footer.{section}.{field}"
            header_footer[section][field] = (
                name in form if field == "sticky" else str(form.get(name, default))
            )
    return result | {"header_footer": header_footer}


def _validated_size(value, *, optional=False):
    if optional and value == "":
        return value
    if not isinstance(value, str):
        raise CommerceError("Typography sizes must be text with px or rem units.")
    match = _SIZE_RE.fullmatch(value)
    if not match:
        raise CommerceError("Typography sizes must use px or rem units.")
    number = float(match["number"])
    lower, upper = (8, 160) if match["unit"] == "px" else (0.5, 10)
    if not lower <= number <= upper:
        raise CommerceError("Typography sizes must stay within the supported range.")
    return value


def _validated_line_height(value):
    if not isinstance(value, str) or not _LINE_HEIGHT_RE.fullmatch(value):
        raise CommerceError("Typography line heights must be unitless numbers.")
    if not 0.8 <= float(value) <= 3:
        raise CommerceError("Typography line heights must stay within the supported range.")
    return value


def _validated_tracking(value):
    if not isinstance(value, str):
        raise CommerceError("Typography letter spacing must use em or px units.")
    match = _TRACKING_RE.fullmatch(value)
    if not match:
        raise CommerceError("Typography letter spacing must use em or px units.")
    number = float(match["number"])
    lower, upper = (-4, 12) if match["unit"] == "px" else (-0.1, 0.5)
    if not lower <= number <= upper:
        raise CommerceError("Typography letter spacing must stay within the supported range.")
    return value


def validate_typography(value):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"display_font", "body_font", "ui_font", "roles"}:
        raise CommerceError("Choose supported typography settings.")
    result = typography_defaults()
    for key in ("display_font", "body_font", "ui_font"):
        if key not in value:
            continue
        item = value[key]
        if not isinstance(item, str) or item not in FONT_CHOICES:
            raise CommerceError("Choose a supported typography font.")
        result[key] = item
    roles = value.get("roles", {})
    if not isinstance(roles, dict) or set(roles) - set(TYPOGRAPHY_ROLE_DEFAULTS):
        raise CommerceError("Choose supported typography roles.")
    for role, values in roles.items():
        if not isinstance(values, dict) or set(values) - set(TYPOGRAPHY_FIELDS):
            raise CommerceError("Choose supported typography role settings.")
        target = result["roles"][role]
        for field, item in values.items():
            if field == "size":
                target[field] = _validated_size(item)
            elif field == "mobile_size":
                target[field] = _validated_size(item, optional=True)
            elif field == "weight":
                if not isinstance(item, str) or item not in _WEIGHTS:
                    raise CommerceError("Typography weights must be between 100 and 900.")
                target[field] = item
            elif field == "line_height":
                target[field] = _validated_line_height(item)
            else:
                target[field] = _validated_tracking(item)
    return result


def _validated_colour(value):
    if not isinstance(value, str):
        raise CommerceError("Header and footer colours must use hex or rgb values.")
    if _COLOUR_RE.fullmatch(value):
        return value
    match = _RGB_RE.fullmatch(value)
    if match and all(int(component) <= 255 for component in match.groups()):
        return value
    raise CommerceError("Header and footer colours must use six-digit hex or rgb values.")


def _validated_header_footer_length(value, bounds):
    if not isinstance(value, str):
        raise CommerceError("Header and footer lengths must use px or rem units.")
    match = _SIZE_RE.fullmatch(value)
    if not match:
        raise CommerceError("Header and footer lengths must use px or rem units.")
    minimum, maximum = bounds[0] if match["unit"] == "px" else bounds[1]
    if not minimum <= float(match["number"]) <= maximum:
        raise CommerceError("Header and footer lengths must stay within the supported range.")
    return value


def validate_header_footer(value):
    """Validate Liquid chrome tokens without allowing a merchant stylesheet."""
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(HEADER_FOOTER_DEFAULTS):
        raise CommerceError("Choose supported header and footer settings.")
    result = header_footer_defaults()
    for section, values in value.items():
        if not isinstance(values, dict) or set(values) - set(HEADER_FOOTER_DEFAULTS[section]):
            raise CommerceError("Choose supported header and footer settings.")
        for field, item in values.items():
            if field == "sticky":
                if not isinstance(item, bool):
                    raise CommerceError("Choose whether the header stays sticky.")
                result[section][field] = item
            elif field in HEADER_FOOTER_COLOUR_FIELDS[section]:
                result[section][field] = _validated_colour(item)
            else:
                result[section][field] = _validated_header_footer_length(
                    item, HEADER_FOOTER_LENGTH_BOUNDS[(section, field)]
                )
    return result


def _validate_design(value, *, allow_typography=False, allow_header_footer=False):
    allowed = set(DEFAULTS)
    if allow_typography:
        allowed.add("typography")
    if allow_header_footer:
        allowed.add("header_footer")
    if not isinstance(value, dict) or set(value) - allowed:
        raise CommerceError("Choose supported design settings.")
    result = DEFAULTS | {key: item for key, item in value.items() if key in DEFAULTS}
    for key, item in result.items():
        if not isinstance(item, str):
            raise CommerceError("Design values must be text.")
        if key in CHOICES:
            if item not in CHOICES[key]:
                raise CommerceError("Choose a supported " + key + ".")
        elif not re.fullmatch(r"#[0-9a-fA-F]{6}", item):
            raise CommerceError("Colors must use six-digit hex values.")
    return result


def validate_theme(value):
    """Validate the legacy, generic design token shape."""
    return _validate_design(value)


def validate_liquid_theme(value):
    """Validate Liquid design tokens plus the bounded typography payload."""
    result = _validate_design(value, allow_typography=True, allow_header_footer=True)
    result["typography"] = validate_typography(value.get("typography"))
    result["header_footer"] = validate_header_footer(value.get("header_footer"))
    return result


# Generic presets retain their historical settings shape for generated site plans.
PRESETS = {name: validate_theme(values) for name, values in _PRESET_VALUES.items()}


def _font_stack(value, fallback):
    return fallback if value == "theme" else FONT_STACKS[value]


def typography_style(typography):
    """Serialize validated values as individual custom properties, never raw CSS."""
    values = validate_typography(typography)
    result = [
        "--liquid-font-display:" + _font_stack(
            values["display_font"], DEFAULT_FONT_STACKS["display"]),
        "--liquid-font-heading-content:" + _font_stack(
            values["display_font"], DEFAULT_FONT_STACKS["heading_content"]),
        "--liquid-font-body:" + _font_stack(values["body_font"], DEFAULT_FONT_STACKS["body"]),
        "--liquid-font-ui:" + _font_stack(values["ui_font"], DEFAULT_FONT_STACKS["ui"]),
        "--liquid-font-footer-heading:" + _font_stack(
            values["display_font"], DEFAULT_FONT_STACKS["footer_heading"]),
    ]
    for role, settings in values["roles"].items():
        name = role.replace("_", "-")
        result.extend((
            f"--liquid-type-{name}-size:{settings['size']}",
            f"--liquid-type-{name}-size-mobile:{settings['mobile_size'] or settings['size']}",
            f"--liquid-type-{name}-weight:{settings['weight']}",
            f"--liquid-type-{name}-line-height:{settings['line_height']}",
            f"--liquid-type-{name}-letter-spacing:{settings['letter_spacing']}",
        ))
    return result


def header_footer_style(header_footer):
    """Serialize bounded Liquid chrome settings as application-owned CSS variables."""
    values = validate_header_footer(header_footer)
    result = []
    for section, fields in HEADER_FOOTER_DEFAULTS.items():
        for field, default in fields.items():
            value = values[section][field]
            variable = HEADER_FOOTER_CSS_VARIABLES[(section, field)]
            if field == "sticky":
                result.append(f"{variable}:{1 if value else 0}")
                continue
            trusted_default = HEADER_FOOTER_STYLE_DEFAULTS.get((section, field))
            result.append(f"{variable}:{trusted_default if value == default and trusted_default else value}")
    announcement_text = values["announcement"]["text"]
    result.append(
        "--liquid-announcement-title-colour:" + (
            "var(--metal-sheen)"
            if announcement_text == HEADER_FOOTER_DEFAULTS["announcement"]["text"]
            else announcement_text
        )
    )
    result.append(
        "--liquid-announcement-request-colour:" + (
            "var(--muted-foreground)"
            if announcement_text == HEADER_FOOTER_DEFAULTS["announcement"]["text"]
            else announcement_text
        )
    )
    return result


def liquid_header_sticky(config):
    """Return a safe data attribute value for the Liquid sticky-header switch."""
    try:
        design = config.get("design", {}) if isinstance(config, dict) else {}
        settings = design.get("header_footer") if isinstance(design, dict) else None
        return validate_header_footer(settings)["header"]["sticky"]
    except CommerceError:
        return True


def theme_style(config):
    if not isinstance(config, dict):
        return ""
    liquid = theme_pack(config) == "liquid"
    if not config.get("design") and not liquid:
        return ""  # Existing sites retain their exact legacy theme.
    try:
        theme = (validate_liquid_theme if liquid else validate_theme)(config.get("design", {}))
    except CommerceError:
        return ";".join(
            typography_style(typography_defaults()) + header_footer_style(header_footer_defaults())
        ) if liquid else ""
    result = [
        f"--store-{key}:{CHOICES[key][value] if key in CHOICES else value}"
        for key, value in theme.items() if key in DEFAULTS
    ] if config.get("design") else []
    if liquid:
        result.extend(typography_style(theme["typography"]))
        result.extend(header_footer_style(theme["header_footer"]))
    return ";".join(result)
