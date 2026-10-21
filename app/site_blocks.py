"""Versioned block documents and pure content editing primitives."""
from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass
from typing import NotRequired, TypedDict
from urllib.parse import urlsplit

from app.services import CommerceError

CONTENT_VERSION = 1
LocalizedText = str | dict[str, str]


class Block(TypedDict):
    id: str
    type: str
    version: NotRequired[int]
    hidden: NotRequired[bool]
    subscription_preview: NotRequired[bool]
    eyebrow: NotRequired[LocalizedText]
    button: NotRequired[LocalizedText]
    image: NotRequired[LocalizedText]
    video: NotRequired[LocalizedText]
    mobile_video: NotRequired[LocalizedText]
    poster: NotRequired[LocalizedText]
    link: NotRequired[LocalizedText]
    url: NotRequired[LocalizedText]
    category: NotRequired[LocalizedText]
    alt: NotRequired[LocalizedText]
    gallery: NotRequired[list[LocalizedText]]
    role: NotRequired[str]
    layout: NotRequired[str]
    heading: NotRequired[LocalizedText]
    body: NotRequired[LocalizedText]
    items: NotRequired[list[dict[str, LocalizedText]]]


@dataclass(frozen=True)
class BlockSpec:
    label: str
    required_item_fields: tuple[str, ...] = ()


BLOCK_TYPES = {
    'hero': BlockSpec('Hero image or video'),
    'text': BlockSpec('Editorial text'),
    'split': BlockSpec('Image and text'),
    'products': BlockSpec('Products'),
    'facts': BlockSpec('Research figures'),
    'claims': BlockSpec('Reviewed statements'),
    'reviews': BlockSpec('Reviews'),
    'articles': BlockSpec('Latest articles'),
    'research': BlockSpec('Research library', ('url',)),
    'faq': BlockSpec('Questions and answers', ('heading',)),
    'team': BlockSpec('People', ('heading',)),
    'contact': BlockSpec('Contact form'),
    'product': BlockSpec('Product details'),
    'references': BlockSpec('Studies referenced', ('url',)),
    'embed': BlockSpec('Secure embed'),
    'liquid_hero': BlockSpec('Liquid hero'),
    'liquid_stats': BlockSpec('Liquid research figures', ('label', 'value')),
    'liquid_collection': BlockSpec('Liquid collection', ('theme',)),
    'liquid_benefits': BlockSpec('Liquid benefits', ('heading', 'body', 'theme')),
    'liquid_reviews': BlockSpec('Liquid sample reviews', ('heading', 'body', 'label', 'value')),
    'liquid_articles': BlockSpec('Liquid reading cards', ('heading', 'url', 'image', 'alt')),
    'liquid_catalog': BlockSpec('Liquid shop cards', ('heading', 'url', 'image', 'alt')),
    'liquid_choices': BlockSpec('Liquid product choices', ('heading', 'body', 'url', 'label')),
    'liquid_trust': BlockSpec('Liquid shopping assurances', ('label',)),
    'liquid_product_purchase': BlockSpec('Liquid product purchase', ('theme',)),
    'liquid_product_steps': BlockSpec('Liquid product instructions', ('theme', 'heading', 'body')),
    'liquid_product_technology': BlockSpec('Liquid bottle technology', ('theme', 'heading', 'body')),
    'liquid_product_day': BlockSpec('Liquid daily timeline', ('theme',)),
    'liquid_product_lifestyle': BlockSpec('Liquid lifestyle image'),
    'liquid_product_flavors': BlockSpec('Liquid flavour cards', ('theme',)),
    'liquid_product_benefits': BlockSpec('Liquid product benefits', ('theme',)),
    'liquid_product_faq': BlockSpec('Liquid product disclosures', ('theme',)),
    'liquid_product_pair': BlockSpec('Liquid product pairing', ('theme',)),
    'liquid_support_about_opening': BlockSpec('Liquid founder stories'),
    'liquid_support_about_values': BlockSpec('Liquid values'),
    'liquid_support_about_closing': BlockSpec('Liquid About links'),
    'liquid_support_faq_hero': BlockSpec('Liquid FAQ search and categories'),
    'liquid_support_faq_category': BlockSpec('Liquid FAQ category'),
    'liquid_support_faq_closing': BlockSpec('Liquid FAQ contact links'),
    'liquid_support_contact': BlockSpec('Liquid contact form'),
    'liquid_editorial': BlockSpec('Liquid learning chapter', ('theme',)),
}
LIQUID_PRODUCT_THEMES = {
    'liquid_product_purchase': {
        'gallery', 'trust', 'box', 'rating', 'spec', 'bundle', 'color', 'flavor', 'quality',
        'fact', 'facts-lead', 'facts-placeholder', 'quality-title', 'ingredient',
        'range-link', 'subscription', 'one-time', 'mix-hint', 'pack', 'subscribe-link',
        'facts-note', 'ingredient-range',
    },
    'liquid_product_steps': {'step'},
    'liquid_product_technology': {'technology'},
    'liquid_product_day': {'day', 'day-stat', 'day-source'},
    'liquid_product_lifestyle': set(),
    'liquid_product_flavors': {'flavor', 'range-link'},
    'liquid_product_benefits': {
        'cell', 'shield', 'exercise', 'leaf', 'water', 'research', 'research-cta',
    },
    'liquid_product_faq': {'faq', 'faq-link'},
    'liquid_product_pair': {'product', 'flavor', 'spec'},
}
LIQUID_PRODUCT_ITEM_FIELDS = {
    'gallery': {'image', 'alt'}, 'bundle': {'label', 'value', 'body'},
    'rating': {'label', 'value'}, 'spec': {'label', 'value'},
    'flavor': {'value'}, 'trust': {'label'}, 'box': {'body'},
    'quality': {'label', 'value'}, 'fact': {'label', 'value', 'body'},
    'ingredient': {'value', 'body'}, 'day': {'heading', 'body', 'label', 'image', 'alt'},
    'day-stat': {'heading', 'body', 'label', 'image', 'alt', 'value'},
    'day-source': {'label', 'body'}, 'faq': {'heading', 'body'},
    'product': {'heading', 'body'},
}
LIQUID_PRODUCT_LAYOUTS = {
    'liquid_product_purchase': {'bottle', 'tablet', 'range'},
    'liquid_product_steps': {'bottle-dark', 'tablet-dark', 'tablet-light'},
    'liquid_product_flavors': {'tablet-light', 'range-dark'},
    'liquid_product_pair': {'tablet-dark', 'bottle-light', 'bottle-dark'},
}
TEXT_FIELDS = {'heading', 'eyebrow', 'body', 'button', 'image', 'video', 'mobile_video',
               'poster', 'link', 'url', 'category', 'alt'}
ITEM_FIELDS = {'heading', 'body', 'url', 'theme', 'image', 'alt', 'label', 'value'}
URL_FIELDS = {'image', 'video', 'mobile_video', 'poster', 'link', 'url'}
LOCALE = re.compile(r'[a-zA-Z]{2,8}(?:-[a-zA-Z0-9]{1,8})*')


def validate_locale(locale: str) -> str:
    if not isinstance(locale, str) or not LOCALE.fullmatch(locale):
        raise CommerceError('Use a valid locale such as en or en-US.')
    return locale


def default_locale(site, *, preview=True) -> str:
    config = site.settings_json if preview else site.published_settings_json
    return validate_locale((config or {}).get('default_locale', 'en'))


def _text(value, key, limit=30000):
    from app.content import safe_url
    values = value.values() if isinstance(value, dict) else [value]
    if isinstance(value, dict):
        if not value:
            raise CommerceError('Localized text needs at least one locale.')
        for locale in value:
            validate_locale(locale)
    for text in values:
        if not isinstance(text, str) or len(text) > limit:
            raise CommerceError(f'{key} must be text under {limit + 1} characters.')
        if key in URL_FIELDS and text:
            safe_url(text, media=key not in {'link', 'url'})
    return copy.deepcopy(value)


def validate_block(block: dict) -> Block:
    if not isinstance(block, dict) or not isinstance(block.get('type'), str) or block['type'] not in BLOCK_TYPES:
        raise CommerceError('Choose a supported block type.')
    result = copy.deepcopy(block)
    if not isinstance(result.get('id'), str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', result['id']):
        raise CommerceError('Each block needs a valid identifier.')
    allowed = TEXT_FIELDS | {'id', 'type', 'role', 'layout', 'version', 'hidden', 'subscription_preview', 'items', 'gallery'}
    if set(result) - allowed:
        raise CommerceError('Choose supported block fields.')
    for key in {'role', 'layout'} & result.keys():
        if not isinstance(result[key], str) or len(result[key]) > 120:
            raise CommerceError(f'{key} must be a short string.')
    if 'version' in result and (type(result['version']) is not int or result['version'] < 1):
        raise CommerceError('Block version must be a positive integer.')
    for key in {'hidden', 'subscription_preview'} & result.keys():
        if not isinstance(result[key], bool):
            raise CommerceError('Visibility and subscription preview must be true or false.')
    for key in TEXT_FIELDS & result.keys():
        result[key] = _text(result[key], key)
    if result['type'] == 'embed':
        from app.content import safe_url
        value = result.get('url', '')
        urls = value.values() if isinstance(value, dict) else [value]
        for embed_url in urls:
            if embed_url and urlsplit(safe_url(embed_url)).scheme != 'https':
                raise CommerceError('Embed URLs must use HTTPS.')
    if result['type'] in LIQUID_PRODUCT_THEMES:
        allowed_layouts = LIQUID_PRODUCT_LAYOUTS.get(result['type'], {'light', 'dark'})
        if result.get('layout') not in allowed_layouts:
            raise CommerceError('Choose a supported Liquid product layout.')
    if 'items' in result:
        if not isinstance(result['items'], list) or len(result['items']) > 200:
            raise CommerceError('Block entries must be a list of up to 200 objects.')
        for item in result['items']:
            if not isinstance(item, dict) or set(item) - ITEM_FIELDS:
                raise CommerceError('Choose supported block entry fields.')
            if not set(BLOCK_TYPES[result['type']].required_item_fields) <= item.keys():
                raise CommerceError('Block entry is missing required fields.')
            for key, value in item.items():
                _text(value, key)
            if result['type'] in LIQUID_PRODUCT_THEMES:
                theme = item.get('theme')
                if not isinstance(theme, str) or theme not in LIQUID_PRODUCT_THEMES[result['type']]:
                    raise CommerceError('Choose a supported Liquid product entry.')
                if not LIQUID_PRODUCT_ITEM_FIELDS.get(theme, set()) <= item.keys():
                    raise CommerceError('Complete the Liquid product entry fields.')
                if theme == 'bundle' and (
                    not isinstance(item['value'], str)
                    or not isinstance(item['body'], str)
                    or item['value'] not in {'1', '2', '3', '4'}
                    or item['body'] not in {'0', '10', '15', '20'}
                ):
                    raise CommerceError('Choose a supported bottle bundle tier.')
                if theme == 'rating':
                    ratings = item['value'].values() if isinstance(item['value'], dict) else [item['value']]
                    counts = item['label'].values() if isinstance(item['label'], dict) else [item['label']]
                    try:
                        valid = all(1 <= float(r) <= 5 for r in ratings)
                        valid = valid and all(1 <= int(c) <= 200 for c in counts)
                    except (ValueError, TypeError):
                        raise CommerceError('Use a valid product sample rating.') from None
                    if not valid:
                        raise CommerceError('Use a valid product sample rating.')
            if result['type'] == 'liquid_reviews':
                ratings = item['value'].values() if isinstance(item['value'], dict) else [item['value']]
                if any(rating not in {'1', '2', '3', '4', '5'} for rating in ratings):
                    raise CommerceError('Sample review ratings must be between one and five.')
            if result['type'] == 'liquid_benefits' and (not isinstance(item['theme'], str) or item['theme'] not in {
                'cell', 'shield', 'exercise', 'leaf', 'molecule', 'water'
            }):
                raise CommerceError('Choose a supported Liquid benefit icon.')
            if result['type'] == 'liquid_collection':
                required = {
                    'product': {'heading', 'label', 'body', 'url', 'image', 'alt', 'value'},
                    'spec': {'label', 'value'}, 'flavor': {'label', 'value'},
                }
                if not isinstance(item['theme'], str) or item['theme'] not in required or not required[item['theme']] <= item.keys():
                    raise CommerceError('Choose a complete Liquid collection entry.')
    if 'gallery' in result:
        if not isinstance(result['gallery'], list) or len(result['gallery']) > 12:
            raise CommerceError('A gallery accepts up to twelve image URLs.')
        for value in result['gallery']:
            _text(value, 'image')
    if result['type'].startswith('liquid_support_'):
        from app.site_liquid_support_validation import validate_support
        validate_support(result)
    if result['type'] == 'liquid_editorial':
        from app.site_liquid_editorial_content import validate_editorial
        validate_editorial(result)
    return result


def normalize_document(document: dict | list) -> dict:
    """Read legacy data without mutating it; missing legacy IDs are stable by position."""
    if isinstance(document, list):
        document = {'sections': document}
    if not isinstance(document, dict):
        raise CommerceError('A page must be an object or a legacy section list.')
    result = copy.deepcopy(document)
    canonical = 'blocks' in result
    if canonical and 'sections' in result:
        raise CommerceError('Use blocks or legacy sections, not both.')
    if canonical and (type(result.get('version')) is not int or result['version'] != CONTENT_VERSION):
        raise CommerceError('Unsupported content version.')
    blocks = result.pop('sections', []) if not canonical else result['blocks']
    if not isinstance(blocks, list) or len(blocks) > 60:
        raise CommerceError('A page can contain up to 60 blocks.')
    normalized = []
    ids = set()
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise CommerceError('Blocks must be objects.')
        if not canonical:
            block.setdefault('id', uuid.uuid5(uuid.NAMESPACE_URL, f'fastshop:legacy-block:{index}').hex)
        block.setdefault('version', 1)
        block = validate_block(block)
        if block['id'] in ids:
            raise CommerceError('Each block needs a unique identifier.')
        ids.add(block['id'])
        normalized.append(block)
    result.update(version=CONTENT_VERSION, blocks=normalized)
    if 'blog' in result:
        from app.site_blog import validate_blog
        result['blog'] = validate_blog(result['blog'])
    for key in {'title', 'description', 'image', 'category'} & result.keys():
        _text(result[key], key, 240 if key == 'title' else 30000)
    return result


def resolve_text(value: LocalizedText, locale='en') -> str:
    """Missing translations resolve to empty text, never an arbitrary language."""
    return value.get(locale, '') if isinstance(value, dict) else value


def resolve_document(document, locale='en') -> dict:
    result = normalize_document(document)
    for key in {'title', 'description', 'image', 'category'} & result.keys():
        result[key] = resolve_text(result[key], locale)
    for block in result['blocks']:
        for key in TEXT_FIELDS & block.keys():
            block[key] = resolve_text(block[key], locale)
        for item in block.get('items', []):
            for key in item:
                item[key] = resolve_text(item[key], locale)
        if 'gallery' in block:
            block['gallery'] = [resolve_text(v, locale) for v in block['gallery']]
    return result


def merge_localized(old, new, locale):
    """Retain translations when editing default-locale text."""
    validate_locale(locale)
    if isinstance(old, dict) and isinstance(new, str):
        return copy.deepcopy(old) | {locale: new}
    if isinstance(old, dict) and isinstance(new, dict):
        if old and all(isinstance(k, str) and LOCALE.fullmatch(k) and isinstance(v, str) for k, v in old.items()):
            return copy.deepcopy(old) | copy.deepcopy(new)
        return {k: merge_localized(old.get(k), v, locale) for k, v in new.items()}
    if isinstance(old, list) and isinstance(new, list):
        return [merge_localized(old[i] if i < len(old) else None, v, locale) for i, v in enumerate(new)]
    return copy.deepcopy(new)


def get_block(document, block_id) -> Block:
    for block in normalize_document(document)['blocks']:
        if block['id'] == block_id:
            return block
    raise CommerceError('Block not found.')


def patch_block(document, block_id, values, locale=None) -> dict:
    if not isinstance(values, dict) or {'id', 'type'} & values.keys():
        raise CommerceError('A patch cannot change block identity or type.')
    result = normalize_document(document)
    old = get_block(result, block_id)
    changes = {key: merge_localized(old.get(key), value, locale) if locale and value is not None else copy.deepcopy(value)
               for key, value in values.items() if value is not None}
    removals = {key for key, value in values.items() if value is None}
    updated = {key: value for key, value in old.items() if key not in removals} | changes
    result['blocks'] = [validate_block(updated) if block['id'] == block_id else block
                        for block in result['blocks']]
    return result


def add_block(document, block) -> dict:
    result = normalize_document(document)
    if not isinstance(block, dict):
        raise CommerceError('Blocks must be objects.')
    block = copy.deepcopy(block)
    block.setdefault('id', uuid.uuid4().hex)
    result['blocks'].append(block)
    return normalize_document(result)


def remove_block(document, block_id) -> dict:
    result = normalize_document(document)
    get_block(result, block_id)
    result['blocks'] = [block for block in result['blocks'] if block['id'] != block_id]
    return result


def reorder_blocks(document, ids) -> dict:
    result = normalize_document(document)
    current = {block['id']: block for block in result['blocks']}
    if not isinstance(ids, list) or not all(isinstance(v, str) for v in ids) or len(ids) != len(current) or set(ids) != set(current):
        raise CommerceError('Reorder must retain each block exactly once.')
    result['blocks'] = [current[key] for key in ids]
    return result
