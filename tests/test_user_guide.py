"""Documentation-only checks; optional presentation dependencies do not gate app tests."""

import re
from pathlib import Path

import pytest

pytest.importorskip('pptx')

from scripts.build_guide_pptx import parse_slides  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_guide_structure_and_local_links():
    markdown = (ROOT / 'docs/USER_GUIDE.md').read_text(encoding='utf-8')
    slides = parse_slides(markdown)
    assert len(slides) == 8
    assert slides[0]['kind'] == 'cover'
    assert not any(slide['image'] for slide in slides)
    assert 'Northstar Goods' in markdown
    for target in re.findall(r'\]\(([^)]+)\)', markdown):
        if not target.startswith(('https://', 'http://', '#')):
            assert (ROOT / 'docs' / target).is_file(), target


def test_guide_covers_building_commerce_and_launch():
    markdown = (ROOT / 'docs/USER_GUIDE.md').read_text(encoding='utf-8')
    for phrase in ('Business:', 'Audience:', 'Pages:', 'Tone:', 'primary-site',
                   'subscriptions', 'compliance', 'go-live', 'undo'):
        assert phrase in markdown
    for prompt in re.findall(r'(?:^>.*\n)+', markdown, re.M):
        assert len(prompt) < 4000


def test_dated_guide_matches_canonical_source():
    assert (ROOT / 'docs/USER_GUIDE.md').read_text() == (ROOT / 'docs/fastshop_user_guide_2026-09-21.md').read_text()
