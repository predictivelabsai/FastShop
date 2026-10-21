"""Ensure admin/merchant accounts can edit every seeded h24you site.

Prints the site membership state that the site editor needs, then upserts
Memberships for the configured accounts on the h24you, h24you-kinetic and h24you-liquid tenants only, so
"Site not found or access denied" in /admin/sites/{id} cannot come from a
missing or mis-scoped membership. Idempotent; touches no passwords.

Run from the repository root with: python ensure_admin.py
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Membership, Site, Tenant, User

ACCOUNTS = {
    "helen@h24you.com": "merchant",
    "joosep.laats@gmail.com": "admin",
}


def main() -> None:
    with SessionLocal() as db:
        users = {user.email: user for user in db.scalars(select(User))}
        sites = list(db.scalars(select(Site).join(Tenant, Tenant.id == Site.tenant_id).where(
            Tenant.slug.in_(("h24you", "h24you-kinetic", "h24you-liquid"))
        ).order_by(Site.tenant_id, Site.slug)))

        print("sites:")
        for site in sites:
            print(
                f"  site {site.id} slug={site.slug!r} tenant={site.tenant_id} "
                f"hostname={site.hostname!r} status={site.status!r}"
            )

        before = {membership.user_id for membership in db.scalars(select(Membership))}
        print("memberships (user -> tenant:role):")
        for membership in db.scalars(select(Membership)):
            owner = next(
                (email for email, user in users.items() if user.id == membership.user_id),
                membership.user_id,
            )
            print(f"  {owner} -> {membership.tenant_id}: {membership.role}")

        missing = [email for email in ACCOUNTS if email not in users]
        if missing:
            print(f"ERROR: users not found: {missing}; run create_user_account.py first")
            sys.exit(1)

        changed = 0
        for site in sites:
            for email, role in ACCOUNTS.items():
                user = users[email]
                membership = db.scalar(
                    select(Membership).where(
                        Membership.tenant_id == site.tenant_id, Membership.user_id == user.id
                    )
                )
                if membership:
                    if membership.role != role:
                        membership.role = role
                        changed += 1
                else:
                    db.add(Membership(tenant_id=site.tenant_id, user_id=user.id, role=role))
                    changed += 1
        if changed and not os.environ.get("DRY_RUN"):
            db.commit()
            print(f"ok: upserted/updated {changed} membership(s)")
        else:
            db.rollback()
            print(f"no writes ({changed} membership(s) needed; set DRY_RUN=1 to stay read-only)")

        after = {membership.user_id for membership in db.scalars(select(Membership))}
        print(f"distinct member users before={len(before)} after={len(after)}")


if __name__ == "__main__":
    main()
