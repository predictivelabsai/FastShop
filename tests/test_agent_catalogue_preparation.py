import pytest

from app.agent_catalogue_preparation import prepare_catalogue
from app.services import CommerceError


def test_prepares_pipe_delimited_attachment_rows_without_writing_catalogue_data():
    result = prepare_catalogue(
        "Name | SKU | Price | Inventory | Collection\nHydrogen bottle | HB-1 | 49.00 | 12 | Bottles",
        "text/csv",
    )
    assert result["state"] == "draft_review"
    assert result["published_catalogue_changed"] is False
    assert result["columns"] == ["category", "name", "price", "sku", "stock"]
    assert result["preview"] == [{"name": "Hydrogen bottle", "sku": "HB-1", "price": "49.00",
                                   "stock": "12", "category": "Bottles"}]
    assert result["errors"] == []


def test_reports_invalid_rows_and_requires_a_catalogue_name_column():
    result = prepare_catalogue("Title | Price | Stock\nBottle | 4.999 | many", "text/csv")
    assert result["errors"] == [{
        "row": 2,
        "error": "Price must be a non-negative value with at most two decimal places. "
                 "Stock must be a whole number up to 10,000,000.",
    }]
    with pytest.raises(CommerceError, match="Name or Title"):
        prepare_catalogue("SKU | Price\nHB-1 | 49.00", "text/csv")
    with pytest.raises(CommerceError, match="CSV or XLSX"):
        prepare_catalogue("Name | Price\nBottle | 49", "text/plain")
