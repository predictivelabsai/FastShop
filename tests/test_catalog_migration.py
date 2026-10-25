"""Exercise catalogue upgrade against an existing schema and preserve old data."""
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


def test_catalogue_migration_upgrade_downgrade_backfills(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    db_path = tmp_path / 'migration.db'
    url = 'sqlite:///' + db_path.as_posix()
    env = {**os.environ, 'DB_URL': url, 'FASTSHOP_ENV': 'development',
           'FASTSHOP_DATA_DIR': str(tmp_path), 'FASTSHOP_AUTO_CREATE_SCHEMA': '0'}

    def alembic(*args):
        result = subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=repo,
                                env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr

    alembic('upgrade', 'head')
    alembic('downgrade', '-1')
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO tenants (id,slug,name,status,plan) "
                                "VALUES ('t','test','Test','active','free')"))
        connection.execute(text("INSERT INTO categories (id,tenant_id,slug,name,description,sort_order) "
                                "VALUES ('c','t','general','General','',0)"))
        connection.execute(text("INSERT INTO product_types "
                                "(id,tenant_id,slug,name,is_shipping_required) "
                                "VALUES ('pt','t','physical','Physical',1)"))
        connection.execute(text("INSERT INTO products "
            "(id,tenant_id,product_type_id,category_id,slug,name,subtitle,description,seo_title,"
            "seo_description,image_url,is_published,is_featured) VALUES "
            "('p','t','pt','c','test','Test','','<script>old</script>','','','https://example.com/a.png',1,0)"))
    alembic('upgrade', 'head')
    with engine.connect() as connection:
        product = connection.execute(text('SELECT status,description_html FROM products')).one()
        assert product.status == 'active'
        assert product.description_html == '&lt;script&gt;old&lt;/script&gt;'
        image = connection.execute(text('SELECT product_id,image_url FROM product_images')).one()
        assert image.product_id == 'p' and image.image_url == 'https://example.com/a.png'
    assert 'collection_id' in {c['name'] for c in inspect(engine).get_columns('site_pages')}
    engine.dispose()
