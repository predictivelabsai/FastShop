from io import BytesIO

import pytest
from docx import Document
from openpyxl import Workbook

from app.agent_document_attachments import extract_document, supported_document_type
from app.services import CommerceError


def test_extracts_plain_text_and_csv_with_a_bounded_private_representation():
    content_type, text = extract_document("brief.txt", b"Store brief\n\nKeep this private.")
    assert content_type == "text/plain"
    assert text == "Store brief\n\nKeep this private."
    content_type, text = extract_document("catalogue.csv", b"Name,Price\nBottle,49.00\n")
    assert content_type == "text/csv"
    assert text == "Name | Price\nBottle | 49.00"


def test_extracts_word_and_spreadsheet_content_without_retaining_binary_data():
    document = Document()
    document.add_paragraph("Draft source copy")
    word = BytesIO()
    document.save(word)
    assert extract_document("brief.docx", word.getvalue()) == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Draft source copy",
    )

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Products"
    sheet.append(["Name", "Price"])
    sheet.append(["Bottle", 49])
    spreadsheet = BytesIO()
    workbook.save(spreadsheet)
    content_type, text = extract_document("catalogue.xlsx", spreadsheet.getvalue())
    assert content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert text == "Sheet: Products\nName | Price\nBottle | 49"


@pytest.mark.parametrize("name", ["malware.exe", "macro.docm", "archive.zip"])
def test_rejects_unknown_document_types(name):
    with pytest.raises(CommerceError, match="Attach a PDF"):
        supported_document_type(name)


def test_rejects_invalid_pdf_and_oversized_documents():
    with pytest.raises(CommerceError, match="PDF file is invalid"):
        extract_document("brief.pdf", b"not a PDF")
    with pytest.raises(CommerceError, match="smaller than 5 MB"):
        extract_document("brief.txt", b"x" * (5 * 1024 * 1024 + 1))
