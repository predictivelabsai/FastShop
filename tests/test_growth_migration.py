"""The independent P4 head can be upgraded, removed and restored."""
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


def test_growth_migration_round_trip(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    url = "sqlite:///" + (tmp_path / "growth-migration.db").as_posix()
    env = {**os.environ, "DB_URL": url, "PYTHON_DOTENV_DISABLED": "1",
           "FASTSHOP_ENV": "development", "FASTSHOP_DATA_DIR": str(tmp_path),
           "FASTSHOP_AUTO_CREATE_SCHEMA": "0"}
    def run(*args):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=repo,
            env=env, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
    run("upgrade", "head")
    engine = create_engine(url)
    tables = {"discounts", "discount_codes", "discount_redemptions", "shipping_zones",
              "shipping_rates", "tax_rates", "staff_invites", "notification_templates"}
    assert tables <= set(inspect(engine).get_table_names())
    with engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "20261025_0032_growth_settings"
    run("downgrade", "-1")
    assert not tables & set(inspect(engine).get_table_names())
    assert "products" in inspect(engine).get_table_names()
    run("upgrade", "head")
    assert tables <= set(inspect(engine).get_table_names())
    engine.dispose()
