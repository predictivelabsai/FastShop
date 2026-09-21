# Rebuilding the H24YOU user guide

The guide follows `../FastClinic/docs/fastclinic_user_guide.md`: branded cover,
contents with page/slide ranges, section dividers, screenshot-led instructions,
native PowerPoint text/tables and a matching landscape PDF. The adapted renderer
and CSS are stored in this repository; FastClinic is not a runtime dependency.

## Sources and outputs

- Canonical editable source: `docs/USER_GUIDE.md`.
- Versioned brief snapshot: `docs/H24YOU_SOURCE_BRIEF.md`, copied from
  `data/feature-requests/h24you/h24you_website_build_brief.md` so links work in GitHub.
- Fresh local evidence: `output/playwright/h24you-guide/` (23 screenshots).
- Dated Markdown, PDF and PPTX: `docs/fastshop_user_guide_2026-09-21.*`.
- Structure: 39 pages/slides, five sections, 18 embedded screenshots.

Prompt examples cite the relevant brief section. Only the phase instruction is
labelled original wording; the other prompts are adaptations. Study references
are copied from the supplied brief, not independently verified research findings.
The model was not called to generate this guide's capture results.

## Refresh screenshots safely

Install Chrome and Playwright through the normal development setup. The script
refuses nonlocal/database-server targets. It boots an isolated H24YOU fixture,
blocks external browser requests and never saves browser credentials or sessions.

```bash
DB_URL= FASTSHOP_ENV=development \
  FASTSHOP_DATA_DIR="$(mktemp -d /tmp/fastshop-guide-XXXXXX)" \
  FASTSHOP_PUBLIC_URL=http://127.0.0.1:5047 \
  XAI_API_KEY= POSTMARK_API_TOKEN= POSTMARK_SERVER_TOKEN= \
  STRIPE_SECRET_KEY= STRIPE_WEBHOOK_SECRET= \
  uv run --extra dev python -m scripts.capture_user_guide
```

It temporarily changes only the disposable H24YOU fixture to private draft for
sample seeding. Its commerce journey uses the private simulator; the training
product is explicitly a placeholder, not H24YOU's approved catalog.

## Generate and check both editions

Install `pandoc`, `weasyprint` and `pdfinfo` through your OS/tool environment.
Python presentation dependencies are in the optional `docs` extra.

```bash
uv run --extra docs python -m scripts.build_user_guide
uv run --extra docs --extra dev python -m pytest tests/test_user_guide.py
uv run ruff check scripts/build_guide_pptx.py scripts/build_user_guide.py scripts/capture_user_guide.py
```

The build validates image paths and PDF/source page count parity. When adding
pages, update the contents ranges and expected section positions in the guide
test. Update the edition date in the source, CSS and builder together.

For visual review, render selected PDF pages with `pdftoppm`. Open the PowerPoint
in a presentation application, or export it with LibreOffice to a temporary
directory and inspect that PDF. The September edition was checked in both forms:
39 PDF pages, 39 PPTX slides and 39 pages after LibreOffice conversion.
