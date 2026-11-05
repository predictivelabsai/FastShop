import ast
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def test_revision_ids_fit_the_postgres_alembic_version_column():
    # alembic_version.version_num is VARCHAR(32) on PostgreSQL; a longer id fails the upgrade.
    for path in VERSIONS.glob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "revision":
                assert len(node.value.value) <= 32, path.name
