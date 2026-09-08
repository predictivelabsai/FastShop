#!/usr/bin/env python3
"""Safely prepare an ignored FastShop env without displaying secret values."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from dotenv import dotenv_values

COPY_NAMES = (
    "DB_URL",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "POSTMARK_API_TOKEN",
    "XAI_API_KEY",
)


def ignored(path: Path) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", str(path)],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-source", type=Path, required=True)
    parser.add_argument("--shared-source", type=Path, required=True)
    parser.add_argument("--target", type=Path, default=Path(".env"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not ignored(args.target):
        raise SystemExit(f"Refusing non-ignored target: {args.target}")
    values = dict(dotenv_values(".env.sample"))
    db_values = dotenv_values(args.db_source)
    shared_values = dotenv_values(args.shared_source)
    values["DB_URL"] = db_values.get("DB_URL") or db_values.get("DATABASE_URL_PROD") or ""
    for name in COPY_NAMES[1:]:
        values[name] = shared_values.get(name) or db_values.get(name) or values.get(name, "")
    values["FASTSHOP_PUBLIC_URL"] = "https://shop.fastsme.com"
    values["FASTSHOP_ENV"] = "production"
    values["FASTSHOP_AUTO_CREATE_SCHEMA"] = "0"
    values["GOOGLE_REDIRECT_URI"] = "https://shop.fastsme.com/auth/google/callback"
    values["DB_SCHEMA"] = "fast_shop"
    populated = [name for name in COPY_NAMES if values.get(name)]
    print("would prepare:", ", ".join(populated), "(values hidden)")
    if not args.apply:
        print("dry run only; pass --apply to write the ignored mode-0600 target")
        return
    args.target.write_text("".join(f"{key}={value or ''}\n" for key, value in values.items()))
    args.target.chmod(0o600)
    print(f"prepared {args.target} with mode 0600; values hidden")


if __name__ == "__main__":
    main()
