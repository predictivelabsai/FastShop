"""The single admin-gap revision is reversible and has no metadata drift."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect


def test_admin_gap_upgrade_downgrade_upgrade(tmp_path):
    root = Path(__file__).resolve().parents[1]
    url = "sqlite:///" + (tmp_path / "gaps.db").as_posix()
    env = {**os.environ, "PYTHON_DOTENV_DISABLED": "1", "PYTHONUTF8": "1", "DB_URL": url,
           "FASTSHOP_ENV": "development", "FASTSHOP_AUTO_CREATE_SCHEMA": "0",
           "FASTSHOP_DATA_DIR": str(tmp_path), "FASTSHOP_ENABLE_SCHEDULER": "0"}
    def migrate(*args):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=root,
                                env=env, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout
    assert migrate("heads").strip() == "20261025_0034_admin_gaps (head)"
    migrate("upgrade", "head")
    engine = create_engine(url)
    tables = {"saved_admin_views", "admin_table_preferences", "admin_activities", "custom_data_definitions",
              "custom_data_values", "site_preferences", "url_redirects", "order_edits", "gift_cards",
              "gift_card_entries", "theme_snapshots"}
    assert tables <= set(inspect(engine).get_table_names())
    migrate("check")
    migrate("downgrade", "20261025_0033_merge_admin_v2")
    assert not tables & set(inspect(engine).get_table_names())
    assert not {"is_active", "is_default", "fulfils_online_orders", "address_json"} & {
        c["name"] for c in inspect(engine).get_columns("warehouses")}
    migrate("upgrade", "head")
    assert tables <= set(inspect(engine).get_table_names())
    migrate("check")
    engine.dispose()
