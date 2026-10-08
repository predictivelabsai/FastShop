"""The Docker image installs requirements.txt, which must never drift from pyproject."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _split(entry: str) -> tuple[str, list[str]]:
    match = re.match(r"^([A-Za-z0-9_.\-]+(?:\[[^\]]+\])?)\s*(.*)$", entry.strip())
    assert match, f"Unparseable dependency entry: {entry}"
    name = match.group(1).lower()
    specifiers = [part.strip() for part in match.group(2).split(",") if part.strip()]
    return name, specifiers


def test_requirements_dot_txt_matches_pyproject_runtime_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    expected = dict(_split(entry) for entry in project["dependencies"])
    actual = dict(
        _split(line)
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    assert actual == expected, (
        "requirements.txt (installed by the Docker image) no longer matches the "
        "runtime dependencies in pyproject.toml — regenerate requirements.txt "
        "whenever project dependencies change, or the deploy image will be "
        "missing packages while local tests pass."
    )