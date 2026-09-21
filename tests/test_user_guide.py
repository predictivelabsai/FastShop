"""Documentation-only checks; optional presentation dependencies do not gate app tests."""

import re
from pathlib import Path

import pytest

pytest.importorskip('pptx')

from scripts.build_guide_pptx import parse_slides  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_guide_structure_matches_contents_and_h24you_example():
    markdown = (ROOT / 'docs/USER_GUIDE.md').read_text()
    slides = parse_slides(markdown)
    assert len(slides) == 39
    assert slides[0]['kind'] == 'cover'
    assert [i + 1 for i, slide in enumerate(slides) if slide['kind'] == 'section'] == [3, 7, 14, 27, 35]
    assert sum(bool(slide['image']) for slide in slides) == 18
    assert all('h24you-guide' in slide['image'] for slide in slides if slide['image'])
    for target in re.findall(r'\]\(([^)]+)\)', markdown):
        if not target.startswith(('https://', 'http://', '#')):
            assert (ROOT / 'docs' / target).is_file(), target


def test_guide_includes_brief_prompts_and_all_launch_article_sources():
    markdown = (ROOT / 'docs/USER_GUIDE.md').read_text()
    for phrase in ('Original wording', 'Business:', 'Audience:', 'Pages:', 'Tone:', 'Shipping: 1000',
        'Prompt: Learn article 1', 'Prompt: Learn article 2', 'Prompt: Learn article 3',
        '26483953', '23680032', '30918832', '31251888', '38590828', '36819697', 'ph16020142'):
        assert phrase in markdown
    for prompt in re.findall(r'(?:^>.*\n)+', markdown, re.M):
        assert len(prompt) < 4000


def test_dated_guide_matches_canonical_source():
    assert (ROOT / 'docs/USER_GUIDE.md').read_text() == (ROOT / 'docs/fastshop_user_guide_2026-09-21.md').read_text()
