"""Build sectioned FastShop PDF/PPTX from one Markdown source, like FastClinic.

Requires pandoc, weasyprint, pdfinfo and python-pptx (uv sync --extra docs).
Run: uv run python -m scripts.build_user_guide
The canonical source is docs/USER_GUIDE.md; images are local Playwright captures.
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path

from scripts.build_guide_pptx import parse_slides

ROOT = Path(__file__).resolve().parents[1]
GUIDE_DATE = '2026-09-21'


def main():
    docs = ROOT / 'docs'
    source = docs / 'USER_GUIDE.md'
    markdown = source.read_text()
    slides = parse_slides(markdown)
    for slide in slides:
        if slide['image'] and not (docs / slide['image']).is_file():
            raise RuntimeError('Missing guide screenshot: ' + slide['image'])
    base = docs / ('fastshop_user_guide_' + GUIDE_DATE)
    snapshot = base.with_suffix('.md')
    snapshot.write_text(markdown)
    with tempfile.TemporaryDirectory(prefix='fastshop-guide-render-') as temporary:
        html = Path(temporary) / 'guide.html'
        subprocess.run(['pandoc', str(snapshot), '-s', '--embed-resources',
            '--from=markdown-implicit_figures', '--css', str(docs / 'assets/guide.css'),
            '--metadata', 'pagetitle=FastShop — H24YOU User Guide', '-o', str(html)], cwd=docs, check=True)
        subprocess.run(['weasyprint', str(html), str(base.with_suffix('.pdf'))], check=True)
    subprocess.run([os.sys.executable, str(ROOT / 'scripts/build_guide_pptx.py'),
        str(snapshot), str(base.with_suffix('.pptx')), 'FastShop — H24YOU User Guide'],
        env=os.environ | {'GUIDE_DATE': GUIDE_DATE}, check=True)
    info = subprocess.check_output(['pdfinfo', str(base.with_suffix('.pdf'))], text=True)
    pages = int(re.search(r'^Pages:\s+(\d+)', info, re.M).group(1))
    if pages != len(slides):
        raise RuntimeError(f'Layout overflow: PDF {pages} pages vs source/PPTX {len(slides)} slides')
    print({'pages_and_slides': pages, 'base': str(base), 'images_checked': sum(bool(s['image']) for s in slides)})


if __name__ == '__main__':
    main()
