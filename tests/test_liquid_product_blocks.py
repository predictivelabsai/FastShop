"""Canonical Liquid product documents retain validated editable content."""
import copy

import pytest

from app.services import CommerceError
from app.site_blocks import LIQUID_PRODUCT_THEMES, normalize_document, validate_block
from app.site_liquid_products_content import product_documents


def test_product_documents_cover_three_pages():
    docs = product_documents()
    assert [d[0] for d in docs] == [
        "/products/h2-4-you-one", "/products/hydrogen-tablets", "/products/taste-the-range",
    ]
    for _, title, _, blocks in docs:
        document = normalize_document({"version": 1, "title": title, "blocks": blocks})
        assert document["blocks"][0]["type"] == "liquid_product_purchase"
        assert document["blocks"][-1]["type"] == "liquid_product_pair"
        assert any(b["type"] == "liquid_reviews" for b in blocks)
        for block in blocks:
            if block["type"] not in LIQUID_PRODUCT_THEMES:
                continue
            with pytest.raises(CommerceError, match="layout"):
                validate_block(block | {"layout": "merchant-script"})
            if block.get("items"):
                invalid = copy.deepcopy(block)
                invalid["items"][0]["theme"] = "<script>"
                with pytest.raises(CommerceError, match="entry"):
                    validate_block(invalid)
                invalid["items"][0]["theme"] = block["items"][0]["theme"]
                invalid["items"][0]["image"] = "javascript:alert(1)"
                with pytest.raises(CommerceError):
                    validate_block(invalid)


def test_real_bottle_photos_and_source_placeholders():
    purchase = product_documents()[0][3][0]
    gallery = [i for i in purchase["items"] if i["theme"] == "gallery"]
    assert [i["image"].rsplit("/", 1)[-1] for i in gallery[:3]] == [
        "studio-front.jpg", "studio-glow.jpg", "held-out-face-hidden.jpg",
    ]
    assert all("placeholder" not in i["alt"].lower() for i in gallery[:3])
    assert all("PLACEHOLDER" in i["alt"] for i in gallery[3:])
    for _, _, _, blocks in product_documents()[1:]:
        facts = [i for i in blocks[0]["items"] if i["theme"] == "facts-placeholder"]
        assert facts[0]["body"] == "PLACEHOLDER – to be confirmed against the final label"


@pytest.mark.parametrize("entry", [
    {"theme": "gallery", "alt": "Missing image"},
    {"theme": "bundle", "label": "Invalid", "value": "0", "body": "10"},
    {"theme": "bundle", "label": "Invalid", "value": {"en": "2"}, "body": "10"},
    {"theme": "rating", "label": "3", "value": "NaN"},
    {"theme": "rating", "label": "0", "value": "5"},
    {"theme": "rating", "label": "3", "value": "6"},
    {"theme": {"en": "gallery"}, "image": "/static/example.jpg", "alt": "Invalid theme"},
])
def test_product_entry_values_are_bounded(entry):
    with pytest.raises(CommerceError):
        validate_block({
            "id": "purchase", "type": "liquid_product_purchase", "layout": "bottle",
            "items": [entry],
        })
