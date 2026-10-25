# Rebuilding the FastShop user guide

The canonical source is `docs/USER_GUIDE.md`. The dated Markdown copy must match it.
The guide uses fictional sample data and contains no embedded store screenshots.

Run `uv sync --extra docs`, then `uv run python -m scripts.build_user_guide` with
Pandoc, WeasyPrint and pdfinfo installed. The builder produces PDF and PowerPoint
locally and checks that the PDF page count matches the source slide count.
Generated binaries are optional and are not shipped in this repository.

Run `uv run pytest tests/test_user_guide.py` to check source structure and links.
