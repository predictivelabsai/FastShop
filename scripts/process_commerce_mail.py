"""Run from the application environment on a scheduler; prints counts, no secrets."""

import argparse
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import SessionLocal
from app.integrations.commerce_email import dispatch_mail
from app.models import CommerceMail


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    with SessionLocal() as db:
        ids = list(db.scalars(select(CommerceMail.id).where(CommerceMail.status.in_(
            ["pending", "failed", "awaiting_configuration", "sending"]), CommerceMail.attempts < 5,
            CommerceMail.available_at <= datetime.now(UTC)).order_by(CommerceMail.created_at).limit(max(1, min(args.limit, 500)))))
    sent = sum(bool(dispatch_mail(message_id)) for message_id in ids)
    print({"processed": len(ids), "accepted_by_provider": sent})


if __name__ == "__main__":
    main()
