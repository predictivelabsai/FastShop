from types import SimpleNamespace

import pytest

from app.services import CommerceError
from app.site_analytics import measurement_id, validate_measurement_id


@pytest.mark.parametrize("value", ["https://example.test/tag.js", "G-ABC\" onload=alert(1)", "UA-12345", "G-", "G-" + "X" * 21])
def test_analytics_rejects_arbitrary_tags(value):
    with pytest.raises(CommerceError, match="measurement ID"):
        validate_measurement_id(value)


def test_analytics_is_disabled_for_preview_unconfigured_and_invalid_sites():
    site = SimpleNamespace(status="published", published_settings_json={"ga4_measurement_id": "g-abc1234567"})
    assert measurement_id(site) == "G-ABC1234567"
    assert measurement_id(site, preview=True) == ""
    site.status = "preview"
    assert measurement_id(site) == ""
    site.status = "published"
    site.published_settings_json = {"ga4_measurement_id": "not-a-tag"}
    assert measurement_id(site) == ""
    site.published_settings_json = {}
    assert measurement_id(site) == ""
