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


def theme_pack(config):
    value = config.get("theme_pack")
    return value if isinstance(value, str) and value in THEME_PACKS else ""


def typography_defaults():
    """Return an independent default payload for the persisted theme JSON."""
    return {"display_font": "theme", "body_font": "quicksand", "ui_font": "quicksand",
            "roles": copy.deepcopy(TYPOGRAPHY_ROLE_DEFAULTS)}


def theme_defaults():
    return DEFAULTS | {"typography": typography_defaults()}


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


def theme_form_values(form):
    """Read the classical form into the same bounded JSON shape as the editor API."""
    typography = typography_defaults()
    for key in ("display_font", "body_font", "ui_font"):
        typography[key] = str(form.get(f"typography.{key}", typography[key]))
    for role, defaults in typography["roles"].items():
        for field, default in defaults.items():
            name = f"typography.roles.{role}.{field}"
            typography["roles"][role][field] = str(form.get(name, default))
    return {key: str(form.get(key, "")) for key in DEFAULTS} | {"typography": typography}


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


def validate_theme(value):
    if not isinstance(value, dict) or set(value) - (set(DEFAULTS) | {"typography"}):
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
    result["typography"] = validate_typography(value.get("typography"))
    return result


# Presets use the same fully-resolved settings shape that draft saves and previews use.
PRESETS = {name: validate_theme(values) for name, values in _PRESET_VALUES.items()}


def _font_stack(value, fallback):
    return fallback if value == "theme" else FONT_STACKS[value]


def typography_style(typography):
    """Serialize validated values as individual custom properties, never raw CSS."""
    values = validate_typography(typography)
    result = [
        "--liquid-font-display:" + _font_stack(
            values["display_font"], DEFAULT_FONT_STACKS["display"]),
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


def theme_style(config):
    if not isinstance(config, dict):
        return ""
    liquid = theme_pack(config) == "liquid"
    if not config.get("design") and not liquid:
        return ""  # Existing sites retain their exact legacy theme.
    try:
        theme = validate_theme(config.get("design", {}))
    except CommerceError:
        return ";".join(typography_style(typography_defaults())) if liquid else ""
    result = [
        f"--store-{key}:{CHOICES[key][value] if key in CHOICES else value}"
        for key, value in theme.items() if key in DEFAULTS
    ] if config.get("design") else []
    if liquid:
        result.extend(typography_style(theme["typography"]))
    return ";".join(result)
