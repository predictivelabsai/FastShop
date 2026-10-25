"""Local-only screenshot fixture, restricted to the disposable preview database."""
import argparse
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import checkout_services, customer_services
from app.config import settings
from app.integrations.stripe_commerce import TaxResult
from app.models import CustomerChallenge, Site, SiteCart, new_id


class PreviewTax:
    def calculate_tax(self, lines, destination, origin, shipping_minor, **kwargs):
        return TaxResult("local_preview_tax", "USD", 0,
                         sum(line.amount_minor for line in lines) + shipping_minor,
                         datetime.now(UTC) + timedelta(hours=1), [])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cart")
    args = parser.parse_args()
    if settings.db_url or not Path(settings.data_dir).name.startswith("fastshop-"):
        raise SystemExit("Requires an isolated fastshop-* data directory and empty DB_URL.")
    engine = create_engine(settings.database_url)
    with Session(engine) as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
        cart = db.scalar(select(SiteCart).where(SiteCart.id == args.cart,
                         SiteCart.site_id == site.id, SiteCart.tenant_id == site.tenant_id))
        customer = customer_services.customer_for(db, site, "preview@example.test", create=True)
        attempt = checkout_services.prepare(db, site, customer.id, cart.lines_json,
            {"line1":"123 Main St", "city":"Los Angeles", "postal_code":"90001",
             "state":"CA", "country":"US"}, PreviewTax(), request_key=cart.request_key,
             recipient_name="Local Preview", subscription_consent=True)
        cart.checkout_id = attempt.id
        token = secrets.token_urlsafe(24)
        challenge = CustomerChallenge(id=new_id(), tenant_id=site.tenant_id, site_id=site.id,
            customer_id=customer.id, purpose="login", client_hash="local-preview",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(minutes=20))
        db.add(challenge)
        db.commit()
        print(json.dumps({"attempt":attempt.id, "challenge":challenge.id, "token":token}))
    engine.dispose()


if __name__ == "__main__":
    main()
