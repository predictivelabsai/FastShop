"""Lucide icons for the merchant admin, rendered inline so they follow currentColor."""
import re
from functools import cache
from pathlib import Path

from fasthtml.common import NotStr

_DIRECTORY = Path(__file__).resolve().parents[1] / "static" / "admin" / "icons"


@cache
def _inner(name: str) -> str:
    svg = (_DIRECTORY / f"{name}.svg").read_text()
    body = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)
    return re.sub(r"\s+", " ", body).replace("> <", "><").strip()


def svg(name: str, size: int = 18, stroke: float = 2) -> str:
    return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" stroke="currentColor" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" '
            f'focusable="false" class="fs-icon">{_inner(name)}</svg>')


def lucide(name: str, size: int = 18, stroke: float = 2):
    return NotStr(svg(name, size, stroke))
