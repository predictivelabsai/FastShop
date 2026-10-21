"""Pure structural constraints for editable Liquid support blocks."""

import re

from app.services import CommerceError

PREFIX = 'liquid_support_'
SCHEMAS = {
    'about_opening': {'founder': {'heading', 'body', 'label'}},
    'about_values': {kind: {'heading', 'body'} for kind in ('quality', 'molecule', 'support')},
    'about_closing': {'': {'label', 'url'}},
    'faq_hero': {'category': {'heading', 'value'}, 'inline-link': {'label', 'url'}},
    'faq_category': {'question': {'heading', 'body', 'value'},
                     'inline-link': {'heading', 'label', 'url'}},
    'faq_closing': {},
    'contact': {'topic': {'label'}, 'quick': {'label', 'url'}},
}
REQUIRED = {
    'about_opening': {'heading', 'body', 'eyebrow', 'button'},
    'about_values': {'heading', 'body'},
    'about_closing': {'heading'},
    'faq_hero': {'heading', 'eyebrow', 'body', 'button'},
    'faq_category': {'heading', 'button'},
    'faq_closing': {'heading', 'body', 'button', 'link', 'eyebrow', 'alt'},
    'contact': {'heading', 'eyebrow', 'body', 'alt', 'link', 'button'},
}


def _nonempty(value):
    values = value.values() if isinstance(value, dict) else [value]
    return bool(value) and all(isinstance(text, str) and text.strip() for text in values)


def _identity(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', value))


def validate_support(block):
    """Validate after generic URL/text validation, without changing the block."""
    kind = block.get('type', '').removeprefix(PREFIX)
    if kind not in SCHEMAS or not REQUIRED[kind] <= block.keys():
        raise CommerceError('Complete the Liquid support block fields.')
    if kind == 'faq_category' and not _identity(block.get('id')):
        raise CommerceError('Use a valid FAQ category anchor.')
    identities = set()
    topic_labels = set()
    question_ids = set()
    link_targets = set()
    for entry in block.get('items', []):
        theme = entry.get('theme', '')
        if not isinstance(theme, str) or theme not in SCHEMAS[kind] or not SCHEMAS[kind][theme] <= entry.keys():
            raise CommerceError('Choose a complete Liquid support entry.')
        if theme in {'inline-link', 'topic', 'quick', ''} and not _nonempty(entry.get('label')):
            raise CommerceError('Liquid support link and topic labels cannot be empty.')
        if theme == 'topic':
            labels = entry['label'].values() if isinstance(entry['label'], dict) else [entry['label']]
            label_key = tuple(labels)
            if label_key in topic_labels:
                raise CommerceError('Use unique contact topics.')
            topic_labels.add(label_key)
        if theme in {'question', 'category'}:
            identity = entry.get('value')
            if not _identity(identity) or identity in identities:
                raise CommerceError('Use unique valid FAQ question and category anchors.')
            identities.add(identity)
            if theme == 'question':
                question_ids.add(identity)
        if kind == 'faq_category' and theme == 'inline-link':
            if not _identity(entry.get('heading')):
                raise CommerceError('Choose a valid FAQ question for this link.')
            link_targets.add(entry['heading'])
    if link_targets - question_ids:
        raise CommerceError('FAQ links must belong to a question in the same category.')
    return block
