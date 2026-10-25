"""The independent admin revisions form one reversible migration graph."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


def test_admin_merge_single_head_and_round_trip(tmp_path):
    root = Path(__file__).resolve().parents[1]
    url = "sqlite:///" + (tmp_path / "merged.db").as_posix()
    env = {**os.environ, "PYTHON_DOTENV_DISABLED": "1", "DB_URL": url,
           "FASTSHOP_ENV": "development", "FASTSHOP_AUTO_CREATE_SCHEMA": "0",
           "FASTSHOP_DATA_DIR": str(tmp_path)}

    def migrate(*args):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=root,
                                env=env, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    head = "20261025_0033_merge_admin_v2"
    assert migrate("heads").strip() == head + " (head)"
    migrate("upgrade", "20261011_0029")
    migrate("upgrade", "head")
    engine = create_engine(url)
    tables = {"draft_orders", "return_requests", "customer_addresses", "discounts",
              "shipping_zones", "tax_rates", "notification_templates", "staff_invites"}
    assert tables <= set(inspect(engine).get_table_names())
    with engine.connect() as db:
        assert list(db.scalars(text("SELECT version_num FROM alembic_version"))) == [head]
    migrate("downgrade", "20261025_0030")
    assert not tables & set(inspect(engine).get_table_names())
    migrate("upgrade", "head")
    assert tables <= set(inspect(engine).get_table_names())
    engine.dispose()
