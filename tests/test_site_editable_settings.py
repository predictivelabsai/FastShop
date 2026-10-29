import pytest

from app.services import CommerceError
from app.site_editable_settings import merge_fields, resolve_fields


def test_editable_settings_defaults_are_absence_only_and_merge_is_additive():
    schema = [{"id": "footer_note", "type": "textarea", "default": "Default text"}]
    assert resolve_fields({}, schema) == {"footer_note": "Default text"}
    assert resolve_fields({"footer_note": "Merchant text"}, schema) == {
        "footer_note": "Merchant text"
    }
    assert merge_fields({"email": "shop@example.test"}, {"footer_note": "Edited"}, schema) == {
        "email": "shop@example.test",
        "footer_note": "Edited",
    }


def test_editable_settings_reject_undeclared_and_unbounded_values():
    schema = [{"id": "footer_note", "type": "textarea", "default": "", "max_length": 4}]
    with pytest.raises(CommerceError):
        merge_fields({}, {"unknown": "value"}, schema)
    with pytest.raises(CommerceError):
        resolve_fields({"footer_note": "too long"}, schema)
