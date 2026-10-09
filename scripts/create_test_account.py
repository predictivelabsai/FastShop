"""Operator utility: provision a self-serve test account with a generated password.

Bypasses the signup UI and mail delivery by calling the same one-transaction
provisioning service the signup flow uses. The queued verification mail row
remains pending (harmless) while the test account is marked email-verified so
hosted logins work. Run against the environment whose database the account
should live in:

    uv run python scripts/create_test_account.py --email someone@example.test

The generated password is printed once on stdout; it is never stored anywhere.
"""

import argparse
import secrets
import string
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from app import signup_services
from app.config import settings
from app.db import SessionLocal, prepare_schema
from app.models import User

_PASSWORD_ALPHABET = (
    string.ascii_lowercase + string.ascii_uppercase + string.digits
    + "abcdefghjkmnpqrstuvwxyz"
)


def generated_password() -> str:
    length = max(signup_services.SIGNUP_PASSWORD_MIN_LENGTH + 4, 30)
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    if not settings.allow_password_login:
        print(
            "Set FASTSHOP_ALLOW_PASSWORD_LOGIN=1 to sign in with the generated "
            "password.",
            file=sys.stderr,
        )
    prepare_schema()

    email = signup_services.normalized_email(args.email)
    name = signup_services.normalized_name(args.name or email.split("@")[0].title())
    password = args.password or generated_password()

    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.email == email)):
            print(f"Account already exists: {email}", file=sys.stderr)
            return 1
        result = signup_services.provision_password_signup(
            db, name, email, password, "operator-script"
        )
        result.user.email_verified_at = datetime.now(UTC)
        db.commit()

    print(f"email:    {email}")
    print(f"password: {password}")
    print(f"workspace: /admin/onboarding/{result.site.id} (status: open)")
    print(
        "note: this account was also queued a verification mail; it is already "
        "marked email-verified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())