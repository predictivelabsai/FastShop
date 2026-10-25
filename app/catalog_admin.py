"""Tenant-scoped merchant catalogue commands. Callers own transactions."""
from __future__ import annotations

import copy
import csv
import io
import itertools
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from html import escape
from html.parser import HTMLParser

from sqlalchemy import delete, func, or_, select, update

from app import content
from app.models import (
    Category,
    Channel,
    Collection,
    CollectionProduct,
    InventoryAdjustment,
    Product,
    ProductImage,
    ProductOption,
    ProductOptionValue,
    ProductTag,
    ProductType,
    ProductVariant,
    SiteMedia,
    SitePage,
    Stock,
    VariantChannelListing,
    Warehouse,
    new_id,
)
from app.services import CommerceError


def _atomic(command):
    """Keep a rejected reusable command from leaving partial pending mutations."""
    @wraps(command)
    def execute(db, *args, **kwargs):
        _ensure_transaction(db)
        with db.begin_nested():
            return command(db, *args, **kwargs)
    return execute


def _ensure_transaction(db):
    # sqlite3's legacy transaction control does not BEGIN for SAVEPOINT. Without
    # this, releasing the first savepoint would commit before the caller does.
    connection = db.connection()
    if connection.dialect.name == 'sqlite':
        driver = connection.connection.driver_connection
        if not driver.in_transaction:
            connection.exec_driver_sql('BEGIN')


def _lock_stocks(db, site, product_ids=None):
    query = select(Stock).join(ProductVariant).where(
        ProductVariant.tenant_id == site.tenant_id,
    )
    if product_ids is not None:
        query = query.where(ProductVariant.product_id.in_(product_ids))
    return list(db.scalars(query.order_by(Stock.id).with_for_update()
                          .execution_options(populate_existing=True)))


def _bounded(model, field, value):
    value = str(value).strip()
    limit = getattr(model.__table__.c[field].type, 'length', None)
    if limit and len(value) > limit:
        raise CommerceError(f'{field.replace("_", " ").capitalize()} must be at most {limit} characters.')
    return value


class _Sanitiser(HTMLParser):
    tags = {'p', 'br', 'h2', 'h3', 'h4', 'strong', 'b', 'em', 'i', 'u', 'ul',
            'ol', 'li', 'a', 'blockquote', 'div', 'span'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.result = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'iframe', 'object', 'svg', 'math'}:
            self.suppressed += 1
        if self.suppressed or tag not in self.tags:
            return
        safe = []
        for key, value in attrs:
            if tag == 'a' and key == 'href' and value:
                if re.match(r'^(https?://|mailto:|/(?!/)|#)', value, re.I):
                    safe.append(f' href="{escape(value, quote=True)}"')
            if key == 'style' and value:
                match = re.fullmatch(r'\s*text-align:\s*(left|right|center|justify);?\s*', value)
                if match:
                    safe.append(f' style="text-align: {match[1]}"')
        self.result.append(f'<{tag}{"".join(safe)}>')

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'iframe', 'object', 'svg', 'math'}:
            self.suppressed = max(0, self.suppressed - 1)
            return
        if not self.suppressed and tag in self.tags and tag != 'br':
            self.result.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.suppressed:
            self.result.append(escape(data))


def sanitise_html(value):
    parser = _Sanitiser()
    parser.feed(str(value or ''))
    parser.close()
    return ''.join(parser.result)


sanitize_html = sanitise_html


def _integer(value, label='Amount', *, signed=False):
    if isinstance(value, bool) or not re.fullmatch(r'-?\d+', str(value)):
        raise CommerceError(f'{label} must be a whole number.')
    amount = int(value)
    if (not signed and amount < 0) or abs(amount) > 1_000_000_000:
        raise CommerceError(f'{label} is outside the supported range.')
    return amount


def _handle(value):
    value = str(value).strip().lower()
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', value) or len(value) > 180:
        raise CommerceError('Use lowercase letters, numbers and hyphens in the handle.')
    return value


def get_product(db, site, product_id):
    product = db.scalar(select(Product).where(
        Product.id == product_id, Product.tenant_id == site.tenant_id,
    ))
    if product is None:
        raise CommerceError('Product not found.')
    return product


def list_products(db, site, filters=None):
    filters = filters or {}
    query = select(Product).where(Product.tenant_id == site.tenant_id)
    if filters.get('q') or filters.get('search'):
        term = '%' + str(filters.get('q') or filters['search']) + '%'
        query = query.where(or_(Product.name.ilike(term), Product.slug.ilike(term),
                               Product.vendor.ilike(term)))
    for key in ('status', 'vendor', 'product_type'):
        if filters.get(key) and filters[key] != 'all':
            query = query.where(getattr(Product, key) == filters[key])
    if filters.get('tag'):
        query = query.where(Product.id.in_(select(ProductTag.product_id).where(
            ProductTag.tenant_id == site.tenant_id, ProductTag.tag == filters['tag'],
        )))
    result = list(db.scalars(query.order_by(Product.created_at.desc(), Product.id)))
    if filters.get('collection'):
        ids = {p.id for p in collection_products(db, site, _collection(db, site, filters['collection']))}
        result = [p for p in result if p.id in ids]
    if filters.get('stock'):
        result = [p for p in result if (inventory_quantity(db, site, p) <= 0)
                  == (filters['stock'] in {'out', 'out_of_stock', 'low'})]
    if filters.get('sort') in {'title', 'title_asc', 'name'}:
        result.sort(key=lambda p: p.name.casefold())
    return result


@_atomic
def bulk_products(db, site, product_ids, action, *, user_id=None, value=None):
    products = [get_product(db, site, i) for i in dict.fromkeys(product_ids)]
    _lock_stocks(db, site, [p.id for p in products])
    if action not in {'status', 'tags_add', 'tags_remove', 'add_to_collection', 'delete',
                      'archive', 'price_adjust', 'active', 'draft', 'archived'}:
        raise CommerceError('Choose a supported bulk action.')
    for product in products:
        if action in {'status', 'archive', 'active', 'draft', 'archived'}:
            status = value if action == 'status' else ('archived' if action == 'archive' else action)
            update_product(db, site, product.id, {'status': status, 'is_published': status == 'active'},
                           expected_version=product.version, user_id=user_id)
        elif action in {'tags_add', 'tags_remove'}:
            values = value.split(',') if isinstance(value, str) else (value or [])
            values = [str(v).strip() for v in values if str(v).strip()]
            tags = list(product.tags_json)
            tags = tags + values if action == 'tags_add' else [t for t in tags if t not in values]
            update_product(db, site, product.id, {'tags': tags}, expected_version=product.version,
                           user_id=user_id)
        elif action == 'add_to_collection':
            collection = _collection(db, site, value)
            if collection.kind != 'manual':
                raise CommerceError('Automated collection membership comes from its rules.')
            if not db.scalar(select(CollectionProduct.id).where(
                CollectionProduct.collection_id == collection.id,
                CollectionProduct.product_id == product.id)):
                db.add(CollectionProduct(tenant_id=site.tenant_id, collection_id=collection.id,
                    product_id=product.id, position=(db.scalar(select(func.max(
                        CollectionProduct.position)).where(
                        CollectionProduct.collection_id == collection.id,
                        CollectionProduct.tenant_id == site.tenant_id)) or 0) + 1))
        elif action == 'price_adjust':
            amount = _integer(value, 'Price adjustment', signed=True)
            for listing in db.scalars(select(VariantChannelListing).join(ProductVariant).where(
                ProductVariant.product_id == product.id, ProductVariant.tenant_id == site.tenant_id,
                VariantChannelListing.channel_id == site.channel_id)):
                listing.price_minor = _integer(listing.price_minor + amount, 'Price')
            update_product(db, site, product.id, {}, expected_version=product.version,
                           user_id=user_id)
        else:
            delete_product(db, site, product.id, expected_version=product.version)
    db.flush()
    return len(products)


def archive_product(db, site, product_id, *, expected_version, user_id=None):
    return update_product(db, site, product_id, {'status': 'archived'},
                          expected_version=expected_version, user_id=user_id)


@_atomic
def delete_product(db, site, product_id, *, expected_version, user_id=None):
    product = get_product(db, site, product_id)
    changed = db.execute(update(Product).where(Product.id == product_id,
        Product.tenant_id == site.tenant_id, Product.version == int(expected_version))
        .values(version=Product.version + 1).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise CommerceError('This product changed. Reload before deleting.')
    from app.models import CartLine, OrderLine
    variant_ids = [v.id for v in product.variants]
    for model in (CartLine, OrderLine, InventoryAdjustment):
        if db.scalar(select(model.id).where(model.variant_id.in_(variant_ids)).limit(1)):
            raise CommerceError('This product has history. Archive it instead.')
    try:
        with db.begin_nested():
            for page in db.scalars(select(SitePage).where(SitePage.product_id == product.id,
                                                         SitePage.tenant_id == site.tenant_id)):
                page.product_id = None
                page.published_json = None
            for model in (ProductTag, CollectionProduct):
                db.execute(delete(model).where(model.product_id == product.id,
                                                model.tenant_id == site.tenant_id))
            db.execute(delete(VariantChannelListing).where(
                VariantChannelListing.variant_id.in_(variant_ids)))
            db.execute(delete(Stock).where(Stock.variant_id.in_(variant_ids)))
            db.delete(product)
            db.flush()
    except Exception as exc:
        raise CommerceError('This product has history. Archive it instead.') from exc


@_atomic
def duplicate_product(db, site, product_id, *, user_id=None):
    source = get_product(db, site, product_id)
    fields = ('vendor', 'product_type', 'tags_json', 'description_html', 'short_description',
              'seo_title', 'seo_description', 'template', 'requires_shipping', 'weight_grams',
              'dimensions_json', 'country_of_origin', 'hs_code', 'charge_tax', 'tax_code')
    data = {field: getattr(source, field) for field in fields}
    data.update(name=f'{source.name[:210]} (copy)', handle=f'{source.slug[:165]}-{new_id()[:8]}',
                status='draft', is_published=False,
                options=[{'name': o.name, 'values': [v.value for v in o.values]} for o in source.options],
                images=[{'image_url': i.image_url, 'alt': i.alt, 'media_id': i.media_id}
                        for i in source.images])
    if not data['images'] and source.image_url:
        data['images'] = [{'image_url': source.image_url, 'alt': source.name}]
    product = create_product(db, site, data, user_id=user_id)
    image_ids = {old.id: new.id for old, new in zip(source.images, product.images, strict=False)}
    product.variants.clear()
    db.flush()
    for original in source.variants:
        if not original.is_active:
            continue
        variant = ProductVariant(tenant_id=site.tenant_id,
            sku=f'{product.slug[:60]}-{new_id()[:10]}', name=original.name,
            attributes_json=copy.deepcopy(original.attributes_json), weight_grams=original.weight_grams,
            sort_order=original.sort_order, barcode=original.barcode,
            track_inventory=original.track_inventory,
            continue_selling_when_out_of_stock=original.continue_selling_when_out_of_stock,
            image_id=image_ids.get(original.image_id))
        product.variants.append(variant)
        db.flush()
        for listing in db.scalars(select(VariantChannelListing).join(Channel).where(
                VariantChannelListing.variant_id == original.id,
                Channel.tenant_id == site.tenant_id)):
            db.add(VariantChannelListing(variant_id=variant.id, channel_id=listing.channel_id,
                currency=listing.currency, price_minor=listing.price_minor,
                compare_at_minor=listing.compare_at_minor, cost_minor=listing.cost_minor))
    db.flush()
    return product


CSV_FIELDS = ['Handle', 'Title', 'Body (HTML)', 'Vendor', 'Type', 'Tags', 'Published',
    'Option1 Name', 'Option1 Value', 'Option2 Name', 'Option2 Value', 'Option3 Name', 'Option3 Value',
    'Variant SKU', 'Variant Price', 'Variant Compare At Price', 'Variant Inventory Qty',
    'Variant Barcode', 'Image Src', 'Image Alt Text', 'SEO Title', 'SEO Description', 'Status']


def _csv_money(value):
    if value in ('', None):
        return None
    try:
        decimal = Decimal(str(value)) * 100
        if decimal != decimal.to_integral_value():
            raise CommerceError('Prices support at most two decimal places.')
        return _integer(str(int(decimal)), 'Price')
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise CommerceError('Enter a valid decimal price.') from exc


def _csv_decimal(minor):
    return f'{minor // 100}.{minor % 100:02d}'


def export_csv(db, site, products=None):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for product in products if products is not None else list_products(db, site):
        get_product(db, site, product.id)
        variants = [v for v in product.variants if v.is_active]
        images = product.images
        for index in range(max(len(variants), len(images), 1)):
            row = dict(Handle=product.slug)
            if index == 0:
                row.update({'Title': product.name, 'Body (HTML)': product.description_html,
                    'Vendor': product.vendor, 'Type': product.product_type,
                    'Tags': ', '.join(product.tags_json), 'Published': str(product.is_published).upper(),
                    'SEO Title': product.seo_title, 'SEO Description': product.seo_description,
                    'Status': product.status})
            if index < len(variants):
                variant = variants[index]
                listing = db.scalar(select(VariantChannelListing).where(
                    VariantChannelListing.variant_id == variant.id,
                    VariantChannelListing.channel_id == site.channel_id))
                row.update({'Variant SKU': variant.sku, 'Variant Barcode': variant.barcode,
                    'Variant Price': _csv_decimal(listing.price_minor) if listing else '0.00',
                    'Variant Compare At Price': _csv_decimal(listing.compare_at_minor)
                        if listing and listing.compare_at_minor is not None else '',
                    'Variant Inventory Qty': sum(s.quantity for s in db.scalars(
                        select(Stock).where(Stock.variant_id == variant.id)))})
                options = [(o.name, variant.attributes_json.get(o.name, '')) for o in product.options]
                if not options and len(variants) > 1:
                    options = [('Title', variant.name)]
                for position, (name, value) in enumerate(options, 1):
                    row[f'Option{position} Name'] = name
                    row[f'Option{position} Value'] = value
            if index < len(images):
                row.update({'Image Src': images[index].image_url, 'Image Alt Text': images[index].alt})
            elif index == 0 and product.image_url:
                row['Image Src'] = product.image_url
            writer.writerow(row)
    return stream.getvalue()


def import_csv(db, site, text, *, user_id=None, dry_run=True):
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    result = {'rows': 0, 'errors': [], 'created': 0, 'updated': 0, 'products': [], 'dry_run': dry_run}
    if not reader.fieldnames or 'Handle' not in reader.fieldnames:
        result['errors'].append({'row': 1, 'error': 'CSV must contain a Handle column.'})
        return result
    groups = {}
    for number, row in enumerate(reader, 2):
        result['rows'] += 1
        if None in row or any(value is None for value in row.values()):
            result['errors'].append({'row': number, 'error': 'Row has a different column count from the header.'})
            continue
        groups.setdefault(row.get('Handle', '').strip(), []).append((number, row))
    _ensure_transaction(db)
    outer = db.begin_nested()
    try:
        _lock_stocks(db, site)
        for handle, rows in groups.items():
            try:
                with db.begin_nested():
                    _import_group(db, site, handle, rows, user_id, result)
            except (CommerceError, ValueError, TypeError) as exc:
                if not any(e.get('handle') == handle for e in result['errors']):
                    result['errors'].append({'row': rows[0][0], 'handle': handle, 'error': str(exc)})
        if dry_run:
            outer.rollback()
        else:
            outer.commit()
    except Exception:
        outer.rollback()
        raise
    return result


def _import_group(db, site, handle, rows, user_id, result):
    first = rows[0][1]
    _handle(handle)
    product = db.scalar(select(Product).where(Product.tenant_id == site.tenant_id,
                                               Product.slug == handle))
    if product is None:
        skus = [row['Variant SKU'] for _, row in rows if row.get('Variant SKU')]
        owners = list(db.scalars(select(Product).join(ProductVariant).where(
            Product.tenant_id == site.tenant_id, ProductVariant.tenant_id == site.tenant_id,
            ProductVariant.sku.in_(skus))).unique()) if skus else []
        if len(owners) > 1:
            raise CommerceError('These SKUs belong to more than one product.')
        product = owners[0] if owners else None
    creating = product is None
    options = []
    for i in range(1, 4):
        names = list(dict.fromkeys(r.get(f'Option{i} Name', '').strip()
                                   for _, r in rows if r.get(f'Option{i} Name', '').strip()))
        if len(names) > 1:
            raise CommerceError('An option must use the same name on every row.')
        if names:
            options.append({'name': names[0], 'values': list(dict.fromkeys(
                r.get(f'Option{i} Value', '').strip() for _, r in rows
                if r.get(f'Option{i} Value', '').strip()))})
    data = {'handle': handle}
    if creating or 'Option1 Name' in first:
        data['options'] = options
    mappings = {'Title': 'name', 'Body (HTML)': 'description_html', 'Vendor': 'vendor',
        'Type': 'product_type', 'Tags': 'tags', 'SEO Title': 'seo_title',
        'SEO Description': 'seo_description', 'Status': 'status'}
    for column, field in mappings.items():
        if column in first and first[column] is not None:
            if column == 'Status' and not first[column]:
                continue
            if column in {'Title', 'Status'} and not first[column] and not creating:
                continue
            data[field] = first[column]
    if first.get('Published'):
        if first['Published'].upper() not in {'TRUE', 'FALSE'}:
            raise CommerceError('Published must be TRUE or FALSE.')
        data['is_published'] = first['Published'].upper() == 'TRUE'
        data.setdefault('status', 'active' if data['is_published'] else 'draft')
    images = [{'image_url': row['Image Src'], 'alt': row.get('Image Alt Text', '')}
              for _, row in rows if row.get('Image Src')]
    if images:
        data['images'] = images
    if product is not None and options and not product.options:
        by_sku = {v.sku: v for v in product.variants}
        for _, row in rows:
            variant = by_sku.get(row.get('Variant SKU'))
            if variant:
                variant.attributes_json = {
                    o['name']: row.get(f'Option{i} Value', '') for i, o in enumerate(options, 1)
                }
    product = create_product(db, site, data, user_id=user_id) if creating else update_product(
        db, site, product.id, data, expected_version=product.version, user_id=user_id)
    active = [v for v in product.variants if v.is_active]
    for number, row in rows:
        if not row.get('Variant SKU') and not row.get('Variant Price'):
            continue
        try:
            attrs = {o['name']: row.get(f'Option{i} Value', '') for i, o in enumerate(options, 1)}
            variant = next((v for v in active if row.get('Variant SKU') and
                            v.sku == row['Variant SKU']), None)
            if variant is None:
                variant = next((v for v in active if all(v.attributes_json.get(k) == value
                                                        for k, value in attrs.items())), None)
            if variant is None:
                raise CommerceError('Variant option combination is missing.')
            values = {'id': variant.id}
            if row.get('Variant SKU'):
                values['sku'] = row['Variant SKU']
            for column, field in [('Variant Price', 'price_minor'),
                                   ('Variant Compare At Price', 'compare_at_minor')]:
                if column in row and row[column] is not None:
                    value = _csv_money(row[column])
                    if value is not None or field == 'compare_at_minor':
                        values[field] = value
            if row.get('Variant Barcode') is not None:
                values['barcode'] = row['Variant Barcode']
            _details(db, site, product, {'variants': [values]}, user_id=user_id)
            if row.get('Variant Inventory Qty') not in ('', None):
                quantity = _integer(row['Variant Inventory Qty'], 'Inventory quantity')
                warehouses = list(db.scalars(select(Warehouse).where(
                    Warehouse.tenant_id == site.tenant_id).order_by(Warehouse.id)))
                if not warehouses:
                    warehouse = Warehouse(tenant_id=site.tenant_id, code='default',
                                          name='Default location')
                    db.add(warehouse)
                    db.flush()
                    warehouses = [warehouse]
                stocks = list(db.scalars(select(Stock).where(
                    Stock.variant_id == variant.id).order_by(Stock.id).with_for_update()))
                other = sum(s.quantity for s in stocks if s.warehouse_id != warehouses[0].id)
                if sum(s.quantity for s in stocks) != quantity:
                    adjust_inventory(db, site, variant.id, warehouses[0].id, quantity - other,
                                     mode='set', user_id=user_id)
        except CommerceError as exc:
            result['errors'].append({'row': number, 'handle': handle, 'error': str(exc)})
            raise
    result['created' if creating else 'updated'] += 1
    result['products'].append({'handle': handle, 'title': product.name, 'variants': len(active)})


def _tags(db, site, product, values):
    if isinstance(values, str):
        values = values.split(',')
    tags = list(dict.fromkeys(str(x).strip() for x in values if str(x).strip()))
    if any(len(t) > 100 for t in tags) or len(tags) > 100:
        raise CommerceError('Use at most 100 tags of up to 100 characters.')
    product.tags_json = tags
    db.execute(delete(ProductTag).where(ProductTag.product_id == product.id,
                                        ProductTag.tenant_id == site.tenant_id))
    db.add_all(ProductTag(tenant_id=site.tenant_id, product_id=product.id, tag=t) for t in tags)


def _set_fields(db, site, product, data):
    name = str(data.get('name', data.get('title', product.name or ''))).strip()
    if not name or len(name) > 220:
        raise CommerceError('Enter a product title under 221 characters.')
    product.name = name
    handle = _handle(data.get('handle', data.get('slug', product.slug or
                     re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-'))))
    if db.scalar(select(Product.id).where(Product.tenant_id == site.tenant_id,
                                         Product.slug == handle, Product.id != product.id)):
        raise CommerceError('That product handle is already in use.')
    product.slug = handle
    for field in ('vendor', 'product_type', 'short_description', 'seo_title', 'seo_description',
                  'template', 'country_of_origin', 'hs_code', 'tax_code', 'subtitle'):
        if field in data:
            setattr(product, field, _bounded(Product, field, data[field]))
    if 'description_html' in data or 'description' in data:
        product.description_html = sanitise_html(data.get('description_html', data.get('description')))
        product.description = re.sub('<[^>]+>', '', product.description_html)
    if 'status' in data:
        if data['status'] not in {'active', 'draft', 'archived'}:
            raise CommerceError('Choose Active, Draft or Archived.')
        product.status = data['status']
    if product.status is None:
        product.status = 'draft'
    product.is_published = product.status == 'active' and bool(data.get('is_published',
                                                                    product.is_published))
    if product.is_published and product.published_at is None:
        product.published_at = datetime.now(UTC)
    for field in ('requires_shipping', 'charge_tax'):
        if field in data:
            setattr(product, field, bool(data[field]))
    if 'weight_grams' in data:
        product.weight_grams = _integer(data['weight_grams'], 'Weight')
    if 'dimensions_json' in data:
        dimensions = data['dimensions_json']
        if not isinstance(dimensions, dict) or set(dimensions) - {'length', 'width', 'height', 'unit'}:
            raise CommerceError('Enter valid product dimensions.')
        for key in ('length', 'width', 'height'):
            if key in dimensions:
                _integer(dimensions[key], 'Dimension')
        product.dimensions_json = dimensions
    if 'tags' in data or 'tags_json' in data:
        _tags(db, site, product, data.get('tags', data.get('tags_json', [])))


def _default_relation(db, site, cls, slug, name):
    obj = db.scalar(select(cls).where(cls.tenant_id == site.tenant_id, cls.slug == slug))
    if obj is None:
        obj = cls(tenant_id=site.tenant_id, slug=slug, name=name)
        db.add(obj)
        db.flush()
    return obj


@_atomic
def create_product(db, site, data, *, user_id=None):
    if user_id:
        from app.plans import ensure_products
        ensure_products(db, user_id)
    product = Product(id=new_id(), tenant_id=site.tenant_id, name='', slug='',
                      status=data.get('status', 'draft'),
                      is_published=bool(data.get('is_published', data.get('status') == 'active')),
                      category_id=_default_relation(db, site, Category, 'general', 'General').id,
                      product_type_id=_default_relation(db, site, ProductType, 'physical',
                                                        'Physical product').id, version=1)
    _set_fields(db, site, product, data)
    db.add(product)
    db.flush()
    regenerate_variants(db, site, product, data.get('options', []))
    _details(db, site, product, data, user_id=user_id)
    db.flush()
    return product


@_atomic
def update_product(db, site, product_id, data, *, expected_version, user_id=None):
    product = get_product(db, site, product_id)
    _lock_stocks(db, site, [product_id])
    version = _integer(expected_version, 'Version')
    changed = db.execute(update(Product).where(Product.id == product.id,
        Product.tenant_id == site.tenant_id, Product.version == version)
        .values(version=Product.version + 1).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise CommerceError('This product changed. Reload before saving your changes.')
    db.refresh(product)
    _set_fields(db, site, product, data)
    if 'options' in data:
        regenerate_variants(db, site, product, data['options'])
    _details(db, site, product, data, user_id=user_id)
    db.flush()
    return product


@_atomic
def regenerate_variants(db, site, product, options):
    get_product(db, site, product.id)
    if not isinstance(options, list) or len(options) > 3:
        raise CommerceError('Use up to three product options.')
    names, value_sets = [], []
    for option in options:
        name = str(option.get('name', '')).strip()
        values = list(dict.fromkeys(str(v).strip() for v in option.get('values', []) if str(v).strip()))
        if not name or len(name) > 100 or name in names or not values or any(len(v) > 150 for v in values):
            raise CommerceError('Give each option a unique name and at least one value.')
        names.append(name)
        value_sets.append(values)
    combinations = list(itertools.islice(itertools.product(*value_sets), 101))
    if len(combinations) > 100:
        raise CommerceError('A product can have at most 100 variants.')
    existing = list(db.scalars(select(ProductVariant).where(
        ProductVariant.product_id == product.id, ProductVariant.tenant_id == site.tenant_id)))
    previous_names = [o.name for o in product.options]
    if not options and existing and not previous_names:
        return [variant for variant in existing if variant.is_active]
    by_attributes = {tuple(sorted((k, value) for k, value in (v.attributes_json or {}).items()
                                 if k in names)): v for v in existing
                     if set(names).issubset(v.attributes_json or {})}
    by_values = {tuple(v.attributes_json.get(n) for n in previous_names): v for v in existing}
    product.options.clear()
    db.flush()
    for position, (name, values) in enumerate(zip(names, value_sets, strict=True)):
        option = ProductOption(tenant_id=site.tenant_id, name=name, position=position)
        option.values = [ProductOptionValue(tenant_id=site.tenant_id, value=v, position=i)
                         for i, v in enumerate(values)]
        product.options.append(option)
    kept = []
    for position, values in enumerate(combinations):
        attributes = dict(zip(names, values, strict=True))
        variant = by_attributes.get(tuple(sorted(attributes.items())))
        if variant is None and previous_names and len(previous_names) == len(names):
            variant = by_values.get(values)
        if variant is None:
            variant = ProductVariant(tenant_id=site.tenant_id, product_id=product.id,
                                     sku=f'{product.slug[:60]}-{new_id()[:10]}', attributes_json=attributes)
            db.add(variant)
        variant.is_active = True
        extras = {k: value for k, value in (variant.attributes_json or {}).items()
                  if k not in previous_names}
        variant.attributes_json = {**extras, **attributes}
        variant.name = ' / '.join(values) or 'Default Title'
        variant.sort_order = position
        kept.append(variant)
    for variant in existing:
        if variant not in kept:
            variant.is_active = False
    db.flush()
    db.expire(product, ['variants'])
    return kept


def _details(db, site, product, data, *, user_id):
    _site_page(db, site, product, 'product', product.slug, product.name, product.is_published)
    if 'images' in data:
        old = {image.id: image for image in product.images}
        images = []
        for index, values in enumerate(data['images']):
            url = content.safe_url(str(values.get('image_url', values.get('url', ''))), media=True)
            content.validate_media_ownership(db, site, {'image': url})
            media_id = values.get('media_id') or None
            if media_id and not db.scalar(select(SiteMedia.id).where(SiteMedia.id == media_id,
                SiteMedia.site_id == site.id, SiteMedia.tenant_id == site.tenant_id)):
                raise CommerceError('Image not found.')
            image = old.get(values.get('id')) or next(
                (i for i in old.values() if i.image_url == url), None,
            ) or ProductImage(tenant_id=site.tenant_id)
            image.image_url, image.alt, image.position, image.media_id = (
                url, str(values.get('alt', ''))[:400], index, media_id)
            images.append(image)
        removed_ids = set(old) - {image.id for image in images}
        for variant in product.variants:
            if variant.image_id in removed_ids:
                variant.image_id = None
        product.images = images
        product.image_url = images[0].image_url if images else ''
    elif 'image_url' in data:
        _details(db, site, product, {'images': [{'image_url': data['image_url']}]}, user_id=user_id)
    db.flush()
    variants = {v.id: v for v in product.variants if v.is_active}
    for index, values in enumerate(data.get('variants', [])):
        variant = variants.get(values.get('id'))
        if variant is None and values.get('attributes_json') is not None:
            variant = next((v for v in variants.values()
                            if v.attributes_json == values['attributes_json']), None)
        if variant is None and not values.get('id') and index < len(variants):
            variant = list(variants.values())[index]
        if variant is None:
            raise CommerceError('Variant not found in this product.')
        if 'sku' in values:
            sku = str(values['sku']).strip()
            if not sku or len(sku) > 100:
                raise CommerceError('Enter a SKU under 101 characters.')
            if db.scalar(select(ProductVariant.id).where(ProductVariant.tenant_id == site.tenant_id,
                ProductVariant.sku == sku, ProductVariant.id != variant.id)):
                raise CommerceError('That SKU is already in use.')
            variant.sku = sku
        for field in ('barcode', 'track_inventory', 'continue_selling_when_out_of_stock'):
            if field in values:
                setattr(variant, field, _bounded(ProductVariant, field, values[field])
                        if field == 'barcode' else bool(values[field]))
        if 'image_id' in values:
            image_id = values['image_id'] or None
            if image_id and image_id not in {i.id for i in product.images}:
                raise CommerceError('Choose an image from this product.')
            variant.image_id = image_id
        if any(k in values for k in ('price_minor', 'compare_at_minor', 'cost_minor')):
            listing = db.scalar(select(VariantChannelListing).where(
                VariantChannelListing.variant_id == variant.id,
                VariantChannelListing.channel_id == site.channel_id))
            if listing is None:
                channel = db.get(Channel, site.channel_id)
                listing = VariantChannelListing(variant_id=variant.id, channel_id=site.channel_id,
                                                currency=channel.currency, price_minor=0, cost_minor=0)
                db.add(listing)
            for field in ('price_minor', 'compare_at_minor', 'cost_minor'):
                if field in values:
                    setattr(listing, field, None if values[field] in ('', None)
                            and field == 'compare_at_minor' else _integer(values[field], 'Price'))
        for warehouse_id, quantity in values.get('quantities', {}).items():
            adjust_inventory(db, site, variant.id, warehouse_id, quantity, mode='set', user_id=user_id)
    if 'collection_ids' in data:
        collections = [_collection(db, site, i) for i in dict.fromkeys(data['collection_ids'])]
        db.execute(delete(CollectionProduct).where(CollectionProduct.tenant_id == site.tenant_id,
                                                   CollectionProduct.product_id == product.id))
        for collection in collections:
            if collection.kind == 'manual':
                db.add(CollectionProduct(tenant_id=site.tenant_id, collection_id=collection.id,
                                         product_id=product.id, position=0))


@_atomic
def adjust_inventory(db, site, variant_id, warehouse_id, quantity, *, mode='adjust',
                     reason='correction', note='', user_id=None):
    if mode not in {'adjust', 'set'} or reason not in {
        'correction', 'received', 'damaged', 'returned', 'sale',
    }:
        raise CommerceError('Choose a valid adjustment mode and reason.')
    variant = db.scalar(select(ProductVariant).where(ProductVariant.id == variant_id,
                                                    ProductVariant.tenant_id == site.tenant_id))
    warehouse = db.scalar(select(Warehouse).where(Warehouse.id == warehouse_id,
                                                 Warehouse.tenant_id == site.tenant_id))
    if variant is None or warehouse is None:
        raise CommerceError('Inventory location or variant not found.')
    stocks = list(db.scalars(select(Stock).where(Stock.variant_id == variant_id)
                             .order_by(Stock.id).with_for_update()
                             .execution_options(populate_existing=True)))
    stock = next((s for s in stocks if s.warehouse_id == warehouse_id), None)
    if stock is None:
        stock = Stock(warehouse_id=warehouse_id, variant_id=variant_id, quantity=0, allocated=0)
        db.add(stock)
        db.flush()
    value = _integer(quantity, 'Quantity', signed=mode == 'adjust')
    resulting = value if mode == 'set' else stock.quantity + value
    if resulting < 0 or resulting < stock.allocated:
        raise CommerceError('Stock cannot be negative or lower than committed quantity.')
    adjustment = InventoryAdjustment(tenant_id=site.tenant_id, variant_id=variant_id,
        warehouse_id=warehouse_id, delta=resulting - stock.quantity, reason=reason,
        note=str(note)[:2000], actor_user_id=user_id, resulting_quantity=resulting)
    stock.quantity = resulting
    db.add(adjustment)
    db.flush()
    return adjustment


def inventory_quantity(db, site, product):
    return sum(s.quantity - s.allocated for s in db.scalars(select(Stock).join(ProductVariant)
        .join(Warehouse).where(ProductVariant.product_id == product.id,
        ProductVariant.tenant_id == site.tenant_id, ProductVariant.is_active.is_(True),
        Warehouse.tenant_id == site.tenant_id)))


def _collection(db, site, collection_id):
    collection = db.scalar(select(Collection).where(Collection.id == collection_id,
                                                     Collection.tenant_id == site.tenant_id))
    if collection is None:
        raise CommerceError('Collection not found.')
    return collection


def _validate_rules(rules):
    if not isinstance(rules, dict) or rules.get('match', 'all') not in {'all', 'any'}:
        raise CommerceError('Choose whether all or any collection rules must match.')
    rows = rules.get('rules', [])
    if not isinstance(rows, list) or len(rows) > 30:
        raise CommerceError('Use up to 30 collection rules.')
    for row in rows:
        if row.get('field') not in {'tag', 'vendor', 'type', 'title', 'price', 'inventory'}:
            raise CommerceError('Choose a valid collection rule field.')
        if row.get('operator', 'equals') not in {'equals', 'not_equals', 'contains', 'gt', 'lt', 'gte', 'lte'}:
            raise CommerceError('Choose a valid collection rule operator.')
        if row['field'] in {'price', 'inventory'}:
            _integer(row.get('value', ''), 'Rule value', signed=row['field'] == 'inventory')
            if row.get('operator') == 'contains':
                raise CommerceError('Numeric rules need a numeric comparison.')
        if row['field'] == 'tag' and row.get('operator', 'equals') not in {
            'equals', 'not_equals', 'contains',
        }:
            raise CommerceError('Tag rules support equals, not equals or contains.')
    return rules


@_atomic
def save_collection(db, site, data, *, collection_id=None, expected_version=None):
    if collection_id:
        collection = _collection(db, site, collection_id)
        result = db.execute(update(Collection).where(Collection.id == collection.id,
            Collection.tenant_id == site.tenant_id, Collection.version == expected_version)
            .values(version=Collection.version + 1).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise CommerceError('This collection changed. Reload before saving.')
        db.refresh(collection)
    else:
        collection = Collection(tenant_id=site.tenant_id, version=1)
    title = str(data.get('title', collection.title or '')).strip()
    if not title or len(title) > 220:
        raise CommerceError('Enter a collection title under 221 characters.')
    handle = _handle(data.get('handle', collection.handle or
                     re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')))
    if db.scalar(select(Collection.id).where(Collection.tenant_id == site.tenant_id,
        Collection.handle == handle, Collection.id != collection.id)):
        raise CommerceError('That collection handle is already in use.')
    collection.title, collection.handle = title, handle
    kind = data.get('kind', collection.kind or 'manual')
    if kind not in {'manual', 'automated'}:
        raise CommerceError('Choose Manual or Automated collection.')
    collection.kind = kind
    for field in ('seo_title', 'seo_description', 'sort_order'):
        if field in data:
            setattr(collection, field, _bounded(Collection, field, data[field]))
    if 'description' in data:
        collection.description = sanitise_html(data['description'])
    if 'image_url' in data:
        collection.image_url = content.safe_url(str(data['image_url']), media=True)
        content.validate_media_ownership(db, site, {'image': collection.image_url})
    if 'is_published' in data:
        collection.is_published = bool(data['is_published'])
    if 'rules_json' in data:
        collection.rules_json = _validate_rules(data['rules_json'])
    db.add(collection)
    db.flush()
    _site_page(db, site, collection, 'collection', collection.handle, collection.title,
               collection.is_published)
    if 'product_ids' in data:
        products = [get_product(db, site, i) for i in dict.fromkeys(data['product_ids'])]
        db.execute(delete(CollectionProduct).where(CollectionProduct.collection_id == collection.id,
                                                   CollectionProduct.tenant_id == site.tenant_id))
        if collection.kind == 'manual':
            db.add_all(CollectionProduct(tenant_id=site.tenant_id, collection_id=collection.id,
                product_id=p.id, position=i) for i, p in enumerate(products))
    db.flush()
    return collection


def _site_page(db, site, entity, kind, handle, title, published):
    """Associate catalogue entries with sites through the existing page boundary."""
    field = SitePage.product_id if kind == 'product' else SitePage.collection_id
    page = db.scalar(select(SitePage).where(SitePage.site_id == site.id,
        SitePage.tenant_id == site.tenant_id, field == entity.id))
    if page is None:
        block_type = 'product' if kind == 'product' else 'products'
        document = {'title': title, 'sections': [{'type': block_type, 'heading': title}]}
        page = content.create_page(db, site, title, f'/{kind}s/{handle}', kind, document)
        setattr(page, f'{kind}_id', entity.id)
    path = f'/{kind}s/{handle}'
    if db.scalar(select(SitePage.id).where(SitePage.site_id == site.id,
        SitePage.tenant_id == site.tenant_id, SitePage.path == path, SitePage.id != page.id)):
        raise CommerceError('A page already uses this catalogue handle.')
    page.path, page.title = path, title
    description = entity.seo_description or getattr(entity, 'short_description', '')
    for field in ('draft_json', 'published_json'):
        document = getattr(page, field)
        if document is not None:
            document = copy.deepcopy(document)
            document['title'] = entity.seo_title or title
            document['description'] = description
            setattr(page, field, document)
    if published and page.published_json is None:
        page.published_json = copy.deepcopy(page.draft_json)
    elif not published:
        page.published_json = None
    return page


def collection_products(db, site, collection):
    if not (collection.id is None and collection.kind == 'automated'
            and collection.tenant_id == site.tenant_id):
        collection = _collection(db, site, collection.id)
    if collection.kind == 'manual':
        result = list(db.scalars(select(Product).join(CollectionProduct).where(
            Product.tenant_id == site.tenant_id, CollectionProduct.tenant_id == site.tenant_id,
            CollectionProduct.collection_id == collection.id).order_by(CollectionProduct.position)))
    else:
        rules = _validate_rules(collection.rules_json or {})
        result = []
        for product in list_products(db, site):
            matches = []
            for rule in rules.get('rules', []):
                field, expected = rule['field'], rule.get('value', '')
                if field == 'inventory':
                    actual = inventory_quantity(db, site, product)
                elif field == 'price':
                    prices = list(db.scalars(select(VariantChannelListing.price_minor)
                        .join(ProductVariant).where(ProductVariant.product_id == product.id,
                        ProductVariant.is_active.is_(True),
                        VariantChannelListing.channel_id == site.channel_id)))
                    actual = min(prices, default=0)
                elif field == 'tag':
                    actual = [str(x).casefold() for x in product.tags_json]
                else:
                    actual = str(getattr(product, {'type': 'product_type', 'title': 'name'}.get(
                        field, field))).casefold()
                op = rule.get('operator', 'equals')
                if field in {'price', 'inventory'}:
                    expected = int(expected)
                else:
                    expected = str(expected).casefold()
                equal = expected in actual if isinstance(actual, list) else actual == expected
                comparisons = {'equals': lambda equal=equal: equal, 'not_equals': lambda equal=equal: not equal,
                    'contains': lambda expected=expected, actual=actual: expected in actual, 'gt': lambda actual=actual, expected=expected: actual > expected,
                    'lt': lambda actual=actual, expected=expected: actual < expected, 'gte': lambda actual=actual, expected=expected: actual >= expected,
                    'lte': lambda actual=actual, expected=expected: actual <= expected}
                matches.append(comparisons[op]())
            if matches and (all(matches) if rules.get('match', 'all') == 'all' else any(matches)):
                result.append(product)
    if collection.sort_order in {'title', 'title_asc', 'alphabetical'}:
        result.sort(key=lambda p: p.name.casefold())
    elif collection.sort_order in {'newest', 'created_desc'}:
        result.sort(key=lambda p: p.created_at, reverse=True)
    elif collection.sort_order in {'price_asc', 'price_desc'}:
        def price(product):
            return min(db.scalars(select(VariantChannelListing.price_minor)
                .join(ProductVariant).where(ProductVariant.product_id == product.id,
                ProductVariant.tenant_id == site.tenant_id,
                ProductVariant.is_active.is_(True),
                VariantChannelListing.channel_id == site.channel_id)), default=0)
        result.sort(key=price, reverse=collection.sort_order == 'price_desc')
    return result
