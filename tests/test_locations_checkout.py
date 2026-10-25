"""Inactive and non-fulfilling locations cannot supply storefront commitments."""

import pytest

from app import checkout_services, locations
from app.models import Warehouse
from app.services import CommerceError, available_stock
from tests.test_checkout_services import prepare, ready  # noqa: F401
from tests.test_us_commerce import shop  # noqa: F401


@pytest.mark.parametrize("flag", ["is_active", "fulfils_online_orders"])
def test_disabled_location_never_supplies_checkout_stock(ready, flag):
    r = ready
    location = r.db.get(Warehouse, r.stock.warehouse_id)
    setattr(location, flag, False)
    r.db.commit()
    assert available_stock(r.db, r.variant.id) == 0
    with pytest.raises(CommerceError, match="not enough stock"):
        prepare(r)
    r.db.refresh(r.stock)
    assert r.stock.allocated == 0


def test_reserved_checkout_prevents_location_deactivation_until_released(ready):
    r = ready
    attempt = prepare(r)
    r.db.commit()
    with pytest.raises(CommerceError, match="committed"):
        locations.save_location(r.db, r.site,
            dict(name="Fixture", country_code="EE", is_active=False), r.stock.warehouse_id)
    r.db.rollback()
    checkout_services.cancel_unstarted(r.db, r.site, attempt.id)
    r.db.commit()
    row = locations.save_location(r.db, r.site,
        dict(name="Fixture", country_code="EE", is_active=False), r.stock.warehouse_id)
    assert not row.is_active
