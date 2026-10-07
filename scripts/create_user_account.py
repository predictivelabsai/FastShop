"""Create a local password-login account (User + Membership role) for FastShop.

Passwords never appear in output or argv-derived logs: read them from:
  - --password-stdin (a single line on stdin), or
  - the FASTSHOP_ACCOUNT_PASSWORD environment variable, or
  - --password (explicit, fine for throwaway demo accounts).

Examples:
  python scripts/create_user_account.py --email helen@h24you.com \
      --name "Helen Kaljuvee" --role merchant --site h24you --password-stdin
  python scripts/create_user_account.py --email owner@h24you.com \
      --role admin --site h24you --password "a-passphrase-of-24-chars-or-more"

Idempotent: re-running with the same email updates name/role/password without
creating duplicates. Passwords must be 24-1024 characters (same policy as the
env admin account). The role must be admin, merchant or customer.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from app.auth import hash_admin_password
from app.db import SessionLocal
from app.models import Membership, Site, Tenant, User


def resolve_password(args):
    if args.password_stdin:
        return getpass.getpass("Password for the account (min 24 chars): ")
    if args.password:
        return args.password
    env_value = os.environ.get("FASTSHOP_ACCOUNT_PASSWORD")
    if env_value:
        return env_value
    sys.exit("create_user_account.py: provide --password, --password-stdin or FASTSHOP_ACCOUNT_PASSWORD")


def resolve_tenant(db, slug: str):
    tenant_id = db.scalar(select(Site.tenant_id).where(Site.slug == slug))
    if tenant_id:
        return tenant_id, f"site:{slug}"
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant:
        return tenant.id, f"tenant:{slug}"
    sys.exit(f"No site or tenant with slug {slug!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--role", default="merchant", choices=["admin", "merchant", "customer"])
    parser.add_argument("--site", default="h24you", help="site slug or tenant slug (default h24you)")
    parser.add_argument("--clear-password", action="store_true", help="revoke password login only")
    args = parser.parse_args()

    email = args.email.strip().lower()
    with SessionLocal() as db:
        tenant_id, scope = resolve_tenant(db, args.site)
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(email=email)
            db.add(user)
            db.flush()
        user.name = args.name or user.name or email.split("@", 1)[0]
        if args.clear_password:
            user.password_hash = None
        else:
            password = resolve_password(args)
            user.password_hash = hash_admin_password(password)
        membership = db.scalar(
            select(Membership).where(Membership.tenant_id == tenant_id, Membership.user_id == user.id)
        )
        if membership:
            membership.role = args.role
        else:
            db.add(Membership(tenant_id=tenant_id, user_id=user.id, role=args.role))
        db.commit()
        print(f"ok: {email} role={args.role} scope={scope} password={'set' if not args.clear_password else 'cleared'}")


if __name__ == "__main__":
    main()