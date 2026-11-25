"""Safe extraction for private merchant documents attached to the assistant."""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader

from app.services import CommerceError

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_CHARS = 80_000
MAX_PDF_PAGES = 50
MAX_SHEETS = 20
MAX_CELLS = 4_000

_TYPES = {
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def supported_document_type(filename: str) -> str:
    suffix = Path(filename or "").suffix.casefold()
    content_type = _TYPES.get(suffix)
    if content_type is None:
        raise CommerceError("Attach a PDF, Word document, spreadsheet, CSV, text or Markdown file.")
    return content_type


def _bounded(text: str) -> str:
    clean = text.replace("\x00", "").strip()
    if not clean:
        raise CommerceError("The document did not contain readable text.")
    return clean[:MAX_DOCUMENT_CHARS]


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CommerceError("The text document uses an unsupported character encoding.")


def _csv_text(data: bytes) -> str:
    rows = []
    for index, row in enumerate(csv.reader(io.StringIO(_decode_text(data)))):
        if index >= MAX_CELLS:
            break
        rows.append(" | ".join(cell.strip() for cell in row))
    return "\n".join(rows)


def _pdf_text(data: bytes) -> str:
    if not data.startswith(b"%PDF-"):
        raise CommerceError("The PDF file is invalid.")
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES])
    except Exception as exc:
        raise CommerceError("The PDF could not be read.") from exc


def _docx_text(data: bytes) -> str:
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise CommerceError("The Word document could not be read.") from exc
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts)


def _xlsx_text(data: bytes) -> str:
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise CommerceError("The spreadsheet could not be read.") from exc
    try:
        parts, cells = [], 0
        for sheet in workbook.worksheets[:MAX_SHEETS]:
            parts.append(f"Sheet: {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                if cells >= MAX_CELLS:
                    break
                values = [str(value).strip() for value in row if value not in (None, "")]
                cells += len(values)
                if values:
                    parts.append(" | ".join(values))
            if cells >= MAX_CELLS:
                break
        return "\n".join(parts)
    finally:
        workbook.close()


def extract_document(filename: str, data: bytes) -> tuple[str, str]:
    """Return an approved content type and bounded plain-text extraction.

    Raw document bytes are deliberately not retained. They are needed only for this
    immediate conversion, while the extracted private context remains tenant-scoped.
    """
    if not isinstance(data, bytes) or not data or len(data) > MAX_DOCUMENT_BYTES:
        raise CommerceError("Documents must be smaller than 5 MB.")
    content_type = supported_document_type(filename)
    suffix = Path(filename).suffix.casefold()
    if suffix == ".pdf":
        text = _pdf_text(data)
    elif suffix == ".docx":
        text = _docx_text(data)
    elif suffix == ".xlsx":
        text = _xlsx_text(data)
    elif suffix == ".csv":
        text = _csv_text(data)
    else:
        text = _decode_text(data)
    return content_type, _bounded(re.sub(r"\n{3,}", "\n\n", text))
