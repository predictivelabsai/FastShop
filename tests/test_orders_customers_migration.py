"""P3 upgrade/downgrade preserves existing commerce rows and PostgreSQL DDL."""
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa


def test_orders_customers_migration_existing_rows_and_roundtrip(tmp_path):
    root = Path(__file__).resolve().parents[1]
    url='sqlite:///'+(tmp_path/'p3.db').as_posix()
    env={**os.environ,'PYTHON_DOTENV_DISABLED':'1','DB_URL':url,
         'FASTSHOP_AUTO_CREATE_SCHEMA':'0','FASTSHOP_DATA_DIR':str(tmp_path)}
    def migrate(*args):
        result=subprocess.run([sys.executable,'-m','alembic',*args],cwd=root,env=env,
            capture_output=True,text=True,timeout=90)
        assert result.returncode==0,result.stdout+result.stderr
    migrate('upgrade','head')
    migrate('downgrade','20261025_0030')
    engine=sa.create_engine(url)
    metadata=sa.MetaData()
    metadata.reflect(engine)
    def insert(connection, table_name, **values):
        table=metadata.tables[table_name]
        for column in table.columns:
            if column.name in values or column.nullable or column.server_default is not None:
                continue
            if isinstance(column.type,sa.Boolean):
                values[column.name]=False
            elif isinstance(column.type,sa.Integer):
                values[column.name]=0
            elif isinstance(column.type,sa.DateTime):
                values[column.name]=datetime.now(UTC)
            elif isinstance(column.type,sa.JSON):
                values[column.name]={}
            else:
                values[column.name]=''
        connection.execute(table.insert().values(**values))
    with engine.begin() as c:
        insert(c,'tenants',id='t',slug='t',name='Test')
        insert(c,'channels',id='ch',tenant_id='t',slug='ch',name='Online',currency='USD')
        insert(c,'sites',id='s',tenant_id='t',channel_id='ch',slug='s',name='Test')
        insert(c,'shop_customers',id='cu',tenant_id='t',site_id='s',email='buyer@example.test',is_active=True)
        insert(c,'marketing_consents',id='mc',tenant_id='t',site_id='s',customer_id='cu',status='subscribed',confirmed_at=datetime.now(UTC))
        insert(c,'orders',id='o',tenant_id='t',channel_id='ch',number='OLD-001',token='tok',
            idempotency_key='old',email='buyer@example.test',currency='USD',status='fulfilled',
            subtotal_minor=9000,total_minor=9000)
        insert(c,'order_lines',id='ol',order_id='o',sku='OLD',product_name='Original item',quantity=3,
            unit_price_minor=3000,total_minor=9000)
        insert(c,'site_orders',id='so',tenant_id='t',site_id='s',customer_id='cu',order_id='o')
    migrate('upgrade','head')
    with engine.connect() as c:
        assert c.execute(sa.text('SELECT fulfilled_quantity,refunded_quantity FROM order_lines')).one()==(3,0)
        customer=c.execute(sa.text('SELECT email_marketing_consent,email_consent_source,account_state FROM shop_customers')).one()
        assert tuple(customer)==(1,'legacy','active')
        assert c.scalar(sa.text('SELECT tags_json FROM site_orders'))=='[]'
    assert 'return_requests' in sa.inspect(engine).get_table_names()
    migrate('downgrade','-1')
    assert 'return_requests' not in sa.inspect(engine).get_table_names()
    assert 'phone' not in {c['name'] for c in sa.inspect(engine).get_columns('shop_customers')}
    with engine.connect() as c:
        assert c.scalar(sa.text('SELECT number FROM orders'))=='OLD-001'
    migrate('upgrade','head')
    engine.dispose()


def test_p3_metadata_compiles_for_postgresql():
    from app.models import Base
    statements=[]
    engine=sa.create_mock_engine('postgresql+psycopg://',lambda sql,*args,**kwargs:
        statements.append(str(sql.compile(dialect=engine.dialect))))
    Base.metadata.create_all(engine)
    assert any('CREATE TABLE return_requests' in s for s in statements)
    assert any('CREATE TABLE customer_addresses' in s for s in statements)
