"""Alembic stores revision ids in a VARCHAR(32) column on PostgreSQL; SQLite doesn't enforce it."""

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def test_every_revision_id_fits_postgres_alembic_version_column():
    too_long = []
    for path in VERSIONS.glob("*.py"):
        match = re.search(r'^revision\s*=\s*["\']([^"\']+)["\']', path.read_text(), re.M)
        if match and len(match.group(1)) > 32:
            too_long.append(f"{path.name}: {match.group(1)} ({len(match.group(1))} chars)")
    assert not too_long, "Revision ids over 32 characters break deploys on PostgreSQL: " + "; ".join(too_long)
