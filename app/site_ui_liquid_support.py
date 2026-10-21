"""Source-faithful support components backed only by canonical block fields."""

import json
import re

from fasthtml.common import (
    H1,
    H2,
    H3,
    A,
    Article,
    Blockquote,
    Button,
    Details,
    Div,
    Em,
    Figcaption,
    Figure,
    Form,
    Img,
    Input,
    Label,
    Li,
    Nav,
    NotStr,
    Option,
    P,
    Script,
    Section,
    Select,
    Span,
    Summary,
    Textarea,
    Ul,
)


def rows(block, theme):
    return [item for item in block.get('items', []) if item.get('theme') == theme]


def names(value):
    return [Em(p, cls='bottle-one font-accent') if p in {'One', 'Fizz'} else p
            for p in re.split(r'\b(One|Fizz)\b', value)]


def href(base, value):
    return base + value if value.startswith('/') and not value.startswith('//') else value


def linked(answer, links, base):
    parts = []
    while answer:
        found = [(answer.find(record['label']), record) for record in links if record['label'] and record['label'] in answer]
        if not found:
            parts.extend(names(answer))
            break
        index, link = min(found, key=lambda p: p[0])
        parts.extend(names(answer[:index]))
        parts.append(A(*names(link['label']), href=href(base, link['url'])))
        answer = answer[index + len(link['label']):]
    return parts



def small_icon(kind):
    paths = {'search': '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
             'chevron': '<path d="m6 9 6 6 6-6"/>',
             'arrow': '<path d="M5 12h14m-6-6 6 6-6 6"/>'}
    size = 20 if kind == 'search' else 16
    cls = ' class="contact-chevron"' if kind == 'chevron' else ''
    return NotStr(f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" '
                  'fill="none" stroke="currentColor" stroke-width="2" '
                  'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"' + cls + '>'
                  + paths[kind] + '</svg>')


def value_icon(kind):
    paths = {
        'quality': '<path d="m28 7 6 4 7 1 1 7 4 6-4 6-1 7-7 1-6 4-6-4-7-1-1-7-4-6 4-6 1-7 7-1Z"/><path d="m21 25 5 5 10-11M19 39l-3 11 12-5 12 5-3-11"/>',
        'molecule': '<circle cx="16" cy="28" r="10"/><circle cx="40" cy="28" r="10"/><path d="M26 26h4m-4 4h4M13 25v6m6-6v6m-6-3h6m18-3v6m6-6v6m-6-3h6M18 11c6-3 14-3 20 0M18 45c6 3 14 3 20 0"/>',
        'support': '<path d="M44 29c0 10-9 17-20 17H10l4-8c-5-4-7-8-7-13 0-10 9-18 20-18s20 8 20 18"/><path d="M27 35s-10-6-10-12a5 5 0 0 1 10-1 5 5 0 0 1 10 1c0 6-10 12-10 12Z"/>',
    }
    return NotStr('<svg viewBox="0 0 56 56" fill="none" stroke="currentColor" '
                  'stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" '
                  'class="about-value-icon" aria-hidden="true">' + paths.get(kind, '') + '</svg>')


def faq_structured_data(blocks):
    """Read exactly the questions used by the HTML, including merchant edits."""
    return {'@context': 'https://schema.org', '@type': 'FAQPage', 'mainEntity': [
        {'@type': 'Question', 'name': q['heading'],
         'acceptedAnswer': {'@type': 'Answer', 'text': q['body']}}
        for b in blocks if b.get('type') == 'liquid_support_faq_category'
        for q in rows(b, 'question') if not b.get('hidden')
    ]}


def faq_jsonld(blocks):
    payload = json.dumps(faq_structured_data(blocks), ensure_ascii=False).replace('<', '\\u003c')
    return Script(NotStr(payload), type='application/ld+json')


def render_support(b, base, csrf, enabled, *, message=''):
    kind = b['type'].removeprefix('liquid_support_')
    if not b['type'].startswith('liquid_support_'):
        return None
    if kind == 'about_opening':
        founders = []
        for f in rows(b, 'founder'):
            portrait = (Img(src=f['image'], alt=f.get('alt', ''), width=300, height=300,
                            cls='about-portrait', loading='lazy') if f.get('image') else
                        Div(Span('A', cls='font-display'), cls='about-portrait about-portrait-placeholder',
                            role='img', aria_label=f['heading'] + ' portrait placeholder'))
            founders.append(Figure(
                Div(portrait, cls='about-portrait-ring'),
                Span('“', cls='about-quote-mark about-quote-small', aria_hidden='true'),
                Blockquote(f['body'], cls='' if f.get('image') else 'about-placeholder-text'),
                Figcaption(P(f['heading'], cls='about-name'), P(f['label'], cls='about-title')),
                cls='about-founder'))
        return Section(Div(H1(b['heading'], cls='sr-only'), Figure(
            Span('“', cls='about-quote-mark', aria_hidden='true'),
            Blockquote(b['body'], cls='font-accent'),
            Figcaption(P(b['eyebrow'], cls='about-name'), P(b['button'], cls='about-title')),
            cls='about-founder-quote'), Div(*founders, cls='about-founders'), cls='metal-container'),
            cls='about-light about-opening')
    if kind == 'about_values':
        return Section(Div(H2(b['heading'], cls='font-display'),
            P(b['body'], cls='about-values-intro'), Div(*[
                Article(value_icon(i['theme']), H3(i['heading'], cls='font-display'),
                        P(*names(i['body'])), cls='about-value') for i in b.get('items', [])
            ], cls='about-values-grid'), cls='metal-container'), cls='about-values metal-section')
    if kind == 'about_closing':
        return Section(Div(H2(b['heading'], cls='font-display'), Div(*[
            A(i['label'], href=href(base, i['url']), cls='why-final-btn ' +
              ('bg-metal-sheen' if index == 0 else 'about-outline'))
            for index, i in enumerate(b.get('items', []))
        ], cls='about-closing-actions'), cls='metal-container'), cls='about-light about-closing')
    if kind == 'faq_hero':
        # Email remains a genuine email link, and product names retain source links.
        links = rows(b, 'inline-link') + [{'label': 'hello@h24you.com', 'url': 'mailto:hello@h24you.com'}]
        return [Section(Div(P(b['eyebrow'], cls='why-label'), H1(b['heading'], cls='font-display'),
            P(*linked(b['body'], links, base), cls='why-intro'), Div(
                small_icon('search'), Input(type='search', aria_label=b['button'],
                    placeholder=b['button'], data_faq_search=True), cls='faq-search'),
            cls='metal-container why-hero-inner'), cls='metal-light why-hero faq-hero'),
            Nav(Div(*[A(*names(i['heading']), href=base + '/faq#' + i['value'], cls='faq-chip')
                for i in rows(b, 'category')], cls='metal-container faq-category-links'),
                aria_label='FAQ categories', cls='metal-light faq-category-nav faq-categories'),
            Section(Div(P('No questions found.'), A('Contact us', href=base + '/contact'),
                        cls='metal-container'), hidden=True, data_faq_empty=True,
                    aria_live='polite', cls='metal-light metal-section')]
    if kind == 'faq_category':
        questions = rows(b, 'question')
        return Section(Div(Div(H2(*names(b['heading']), id=b['id'], cls='font-display'),
            cls='faq-category-head'), Div(*[
                Details(Summary(Span(*names(q['heading'])),
                    Span('+', cls='faq-toggle', aria_hidden='true'), cls='faq-question'),
                    Div(P(*linked(q['body'], [record for record in rows(b, 'inline-link')
                        if record['heading'] == q['value']], base)), cls='faq-answer', id=q['value']),
                    cls='faq-row', data_faq_question=True, data_faq_text=q['heading'] + ' ' + q['body'])
                for q in questions
            ], cls='faq-questions') if questions else
            A(b['button'], href=base + '/contact', cls='faq-contact'), cls='metal-container faq-category-inner'),
            cls='faq-category metal-section metal-light', data_faq_category=True)
    if kind == 'faq_closing':
        return Section(Div(Div(H2(b['heading'], cls='font-display'), P(b['body'], cls='faq-closing-text'),
            Div(A(b['button'], href=href(base, b['link']), cls='bg-metal-sheen why-final-btn'),
                cls='faq-closing-actions'), P(b['eyebrow'] + ' ',
                A(b['alt'], href='mailto:' + b['alt'], cls='faq-email'), cls='faq-closing-email'),
            cls='faq-closing-card'), cls='metal-container'), cls='metal-light faq-closing', aria_label='Closing')
    if kind == 'contact':
        def field(key, label, control, optional=False):
            return Div(Label(label, Span(' (optional)', cls='contact-optional') if optional else None,
                             fr='contact-' + key), control,
                       None if optional else P(id='contact-' + key + '-error', cls='contact-error'),
                       cls='contact-field')
        form = Form(Input(type='hidden', name='csrf_token', value=csrf),
            field('name', 'Name', Input(id='contact-name', name='name', autocomplete='name', required=True, maxlength=160)),
            field('email', 'Email', Input(id='contact-email', name='email', type='email', autocomplete='email', required=True, maxlength=255)),
            field('topic', 'Topic', Div(Select(Option('Choose a topic', value='', disabled=True, selected=True),
                *[Option(t['label']) for t in rows(b, 'topic')], id='contact-topic', name='topic', required=True,
                data_empty='true'), small_icon('chevron'), cls='contact-select')),
            field('order', 'Order number', Input(id='contact-order', name='order', placeholder='e.g. #10234', maxlength=160), True),
            field('message', 'Message', Textarea(id='contact-message', name='message', rows=5, required=True, minlength=10, maxlength=5000)),
            Div(Label('Website', fr='contact-website'), Input(id='contact-website', name='website', tabindex=-1,
                autocomplete='off'), cls='contact-honeypot', aria_hidden='true'),
            Button('Send message', type='submit', disabled=not enabled, cls='bg-metal-sheen why-final-btn contact-submit'),
            P("We'll only use your details to answer your message. See our ",
              A('Privacy policy', href='#' if b['link'] == '/#' else href(base, b['link'])), '.', cls='contact-privacy'),
            action=base + '/contact', method='post', cls='contact-form', data_liquid_contact=True)
        return Div(Div(P(b['eyebrow'], cls='why-label'), H1(b['heading'], cls='font-display'),
            P(b['body'], cls='contact-text'), cls='contact-intro'),
            Div(P(message, role='status') if message else None, form, cls='contact-card'),
            Div(P('Or email us at ', A(b['alt'], href='mailto:' + b['alt'], cls='faq-email'),
                cls='contact-text'), H2('Quick answers', cls='contact-quick'), Ul(*[
                    Li(A(Span(i['label']), small_icon('arrow'), href=href(base, i['url'])))
                    for i in rows(b, 'quick')]), cls='contact-links'), cls='metal-container contact-grid')
    return None
