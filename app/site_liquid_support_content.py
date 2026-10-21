"""Insert-only canonical support page documents copied from Helen's registry."""

import json
from pathlib import Path

CONTENT = json.loads(Path(__file__).with_suffix('.json').read_text('utf-8'))


def block(kind, identity, **values):
    return {'id': identity, 'type': 'liquid_support_' + kind, 'version': 1, **values}


def support_documents():
    about, faq, contact = (CONTENT[k] for k in ('aboutPage', 'faqPage', 'contactPage'))
    founders = [
        {'theme': 'founder', 'heading': f['name'], 'body': f['text'], 'label': f['title'],
         'image': '/static/h24you/liquid/helen-founder.jpg' if f['id'] == 'helen' else '',
         'alt': 'Helen, co-founder and CEO of H2 | 4 YOU' if f['id'] == 'helen' else ''}
        for f in about['founders']
    ]
    about_blocks = [
        block('about_opening', 'about-opening', heading='About H2 | 4 YOU',
              body=about['quote'], eyebrow=about['attribution'], button=about['attributionTitle'],
              items=founders),
        block('about_values', 'about-values', heading=about['valuesHeading'],
              body=about['valuesIntro'], items=[
                  {'theme': v['icon'], 'heading': v['title'], 'body': v['text']}
                  for v in about['values']]),
        block('about_closing', 'about-closing', heading=about['closing'], items=[
            {'label': about['shop'], 'url': '/shop'},
            {'label': about['learn'], 'url': '/why-hydrogen'}]),
    ]
    faq_blocks = [block('faq_hero', 'faq-hero', heading=faq['title'], eyebrow=faq['label'],
                        body=faq['intro'], button=faq['search'], items=[
                            {'theme': 'category', 'heading': c['title'], 'value': c['id']}
                            for c in faq['categories']
                        ] + [{'theme': 'inline-link', 'label': record['text'],
                              'url': record['to'] + ('#' + record['hash'] if record.get('hash') else '')}
                             for record in faq['introLinks']])]
    for category in faq['categories']:
        entries = []
        for index, question in enumerate(category['questions']):
            identity = category['id'] + '-answer-' + str(index)
            entries.append({'theme': 'question', 'heading': question['question'],
                            'body': question['answer'], 'value': identity})
            entries.extend({'theme': 'inline-link', 'heading': identity, 'label': record['text'],
                            'url': record['to'] + ('#' + record['hash'] if record.get('hash') else '')}
                           for record in question.get('links', []))
        faq_blocks.append(block('faq_category', category['id'], heading=category['title'],
                                button=faq['contact'], items=entries))
    closing = faq['closing']
    faq_blocks.append(block('faq_closing', 'faq-closing', heading=closing['heading'],
                            body=closing['text'], button=closing['button'], link='/contact',
                            eyebrow=closing['emailLead'], alt=closing['email']))
    contact_block = block('contact', 'contact', heading=contact['title'],
                          eyebrow=contact['label'], body=contact['intro'],
                          alt=contact['email'], link='/#',
                          button=contact['again'], items=[
                              {'theme': 'topic', 'label': t} for t in contact['topics']
                          ] + [{'theme': 'quick', 'label': record['label'],
                                'url': record['to'] + ('#' + record['hash'] if record.get('hash') else '')}
                               for record in contact['quick']])
    return [('/about', about['title'], 'content', about_blocks),
            ('/faq', 'FAQ | H2 | 4 YOU', 'content', faq_blocks),
            ('/contact', 'Contact us | H2 | 4 YOU', 'contact', [contact_block])]


def contact_topics(document):
    """Allowed topics follow the site's published canonical contact document."""
    return {i.get('label', '') for b in document.get('blocks', [])
            if b.get('type') == 'liquid_support_contact'
            for i in b.get('items', []) if i.get('theme') == 'topic'}
