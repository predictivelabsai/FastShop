"""Operator-only, read-only provider readiness; never prints keys or provider bodies."""

import argparse
import json

from sqlalchemy import select

from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway
from app.integrations.woocommerce import WooCommerceGateway
from app.models import Site
from app.services import CommerceError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--provider", choices=["stripe", "woocommerce"], required=True)
    args = parser.parse_args()
    try:
        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.id == args.site_id, Site.tenant_id == args.tenant_id))
            if site is None:
                raise CommerceError("Store not found.")
            gateway = (StripeGateway(site) if args.provider == "stripe" else
                       WooCommerceGateway(site, tenant_id=args.tenant_id))
        print(json.dumps(gateway.connection_status(), sort_keys=True))
    except CommerceError as exc:
        print(json.dumps({"connected": False, "error": str(exc)}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
