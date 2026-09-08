#!/usr/bin/env python3
"""Share one ignored FastShop connector token without displaying its value."""

from __future__ import annotations

import argparse
import os
import secrets
import stat
import subprocess
import tempfile
from pathlib import Path

KEY = "FASTSHOP_CONNECTOR_TOKEN"


def read_value(path: Path) -> str:
    if not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith(f"{KEY}="):
            return raw.partition("=")[2].strip()
    return ""


def ensure_ignored(path: Path) -> None:
    probe = subprocess.run(
        ["git", "-C", str(path.parent), "check-ignore", "-q", str(path)],
        check=False,
    )
    if probe.returncode:
        raise RuntimeError(f"Refusing to update non-ignored env file: {path}")


def rewrite(path: Path, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output: list[str] = []
    replaced = False
    for raw in lines:
        if raw.startswith(f"{KEY}="):
            if not replaced:
                output.append(f"{KEY}={value}")
                replaced = True
        else:
            output.append(raw)
    if not replaced:
        if output and output[-1]:
            output.append("")
        output.append(f"{KEY}={value}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("\n".join(output).rstrip() + "\n")
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shop-env", type=Path, default=Path(".env"))
    parser.add_argument("--erp-env", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for path in (args.shop_env, args.erp_env):
        ensure_ignored(path.resolve())
    existing = read_value(args.erp_env) or read_value(args.shop_env)
    print(f"mode={'apply' if args.apply else 'dry-run'}")
    print(f"token={'existing' if existing else 'would-generate'}; value hidden")
    if not args.apply:
        return
    token = existing or secrets.token_urlsafe(48)
    rewrite(args.erp_env, token)
    rewrite(args.shop_env, token)
    print("result=FastShop and FastERP envs synchronized; value hidden")


if __name__ == "__main__":
    main()
