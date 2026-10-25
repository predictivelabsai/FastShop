import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, text


@pytest.mark.parametrize("populated", [False, True])
def test_customer_accounts_migration_defaults_off(tmp_path, populated):
    url = "sqlite:///" + (tmp_path / "customer-accounts.db").as_posix()
    environment = os.environ | {"DB_URL": url, "FASTSHOP_ENV": "development"}

    def alembic(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args], env=environment,
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    alembic("upgrade", "20261009_0028")
    engine = create_engine(url)
    with engine.begin() as connection:
        # Historical create-table migrations import current models, so restore
        # the pre-flag schema to exercise a real upgrade of an existing store.
        connection.execute(text(
            "ALTER TABLE site_commerce_settings DROP COLUMN customer_accounts_enabled"
        ))
        if populated:
            connection.execute(text(
                "INSERT INTO site_commerce_settings "
                "(id, tenant_id, site_id, mode, created_at, updated_at, origin_json, "
                "free_shipping_threshold_minor, allowed_states_json, product_tax_codes_json, "
                "subscription_product_ids_json, tax_registration_reviewed, version) "
                "VALUES ('fixture', 'tenant', 'site', 'sandbox', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '{}', 7500, '[]', '{}', '[]', 0, 1)"
            ))
    alembic("upgrade", "head")
    with engine.begin() as connection:
        column = next(c for c in inspect(connection).get_columns("site_commerce_settings")
                      if c["name"] == "customer_accounts_enabled")
        assert column["nullable"] is False
        if populated:
            assert connection.scalar(text(
                "SELECT customer_accounts_enabled FROM site_commerce_settings WHERE id='fixture'"
            )) == 0
        connection.execute(text(
            "INSERT INTO site_commerce_settings "
            "(id, tenant_id, site_id, mode, created_at, updated_at, origin_json, "
                "free_shipping_threshold_minor, allowed_states_json, product_tax_codes_json, "
                "subscription_product_ids_json, tax_registration_reviewed, version) "
            "VALUES ('new', 'tenant', 'new-site', 'sandbox', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '{}', 7500, '[]', '{}', '[]', 0, 1)"
        ))
        assert connection.scalar(text(
            "SELECT customer_accounts_enabled FROM site_commerce_settings WHERE id='new'"
        )) == 0
    alembic("check")
    alembic("downgrade", "20261009_0028")
    alembic("upgrade", "head")
    engine.dispose()
