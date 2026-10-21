"""Rehearse old-code SQLite creation, Liquid cutover and old-code rollback.

Run with the project's Python. No deployed services or credentials are used.
The reference repository is read only. All databases and old code live in TEMP.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = "h24you.fastsme.com"
PASSWORD = "disposable-deployment-test-password"
EMAILS = ("helen@h24you.com", "joosep.laats@gmail.com")


def worker(mode, screenshots=None):
    from sqlalchemy import select
    from starlette.testclient import TestClient

    # Both versions call create_all unconditionally for SQLite, even with the
    # auto-create flag off. Disable that fallback so Alembic alone must supply
    # every table/column, as it does in the PostgreSQL container.
    from app import db as database_module
    from app.auth import hash_admin_password
    from app.db import SessionLocal
    from app.models import Membership, Site, SiteCommerceSettings, Tenant, User

    database_module.prepare_schema = lambda: None
    from app.main import app

    with SessionLocal() as db:
        kinetic = db.scalar(select(Site).where(Site.slug == "h24you-kinetic"))
        if mode == "legacy":
            kinetic.hostname = HOST
            kinetic.status = "preview"
            config = db.scalar(select(SiteCommerceSettings).where(
                SiteCommerceSettings.site_id == kinetic.id))
            if not config:
                db.add(SiteCommerceSettings(tenant_id=kinetic.tenant_id,
                    site_id=kinetic.id, mode="sandbox"))
            else:
                config.mode = "sandbox"
            old = db.scalar(select(Tenant).where(Tenant.slug == "h24you"))
            for email, role in zip(EMAILS, ("merchant", "admin"), strict=True):
                user = db.scalar(select(User).where(User.email == email))
                if not user:
                    user = User(email=email, name=email.split("@")[0])
                    db.add(user)
                    db.flush()
                user.password_hash = hash_admin_password(PASSWORD)
                for tenant_id in (old.id, kinetic.tenant_id):
                    member = db.scalar(select(Membership).where(
                        Membership.tenant_id == tenant_id, Membership.user_id == user.id))
                    if member:
                        member.role = role
                    else:
                        db.add(Membership(tenant_id=tenant_id, user_id=user.id, role=role))
            db.commit()
        elif mode == "new":
            from app.site_seed_liquid import seed_h24you_liquid
            liquid = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
            assert kinetic.hostname is None and kinetic.status == "draft"
            assert liquid.hostname == HOST and liquid.status == "published"
            versions = (liquid.version, kinetic.version)
            seed_h24you_liquid(db, EMAILS[1])
            assert versions == (liquid.version, kinetic.version)
            db.commit()
            for email, role in zip(EMAILS, ("merchant", "admin"), strict=True):
                user = db.scalar(select(User).where(User.email == email))
                assert db.scalar(select(Membership.role).where(
                    Membership.tenant_id == liquid.tenant_id, Membership.user_id == user.id)) == role
            liquid_id, kinetic_id = liquid.id, kinetic.id
        elif mode == "restore":
            liquid = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
            liquid.hostname = None
            db.flush()
            kinetic.hostname = HOST
            kinetic.status = "preview"
            db.commit()

    with TestClient(app, base_url="https://" + HOST) as http:
        if mode == "new":
            paths = ("/", "/shop", "/products/h2-4-you-one", "/products/hydrogen-tablets",
                "/products/taste-the-range", "/about", "/faq", "/contact", "/learn",
                "/why-hydrogen", "/why-hydrogen/science", "/science", "/cart", "/account")
            for path in paths:
                response = http.get(path)
                assert response.status_code == 200, (path, response.status_code, response.text[:500])
                assert response.headers.get("x-robots-tag") == "noindex, nofollow", path
                if path not in ("/cart", "/account"):
                    assert "site-theme-liquid.css" in response.text, path
            assert http.get("/checkout", follow_redirects=False).status_code in (200, 303)
            account = http.get("/account")
            assert "Sign in with an email link" in account.text
            product = http.get("/products/h2-4-you-one")
            token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', product.text)[1]
            from app.models import ProductVariant
            with SessionLocal() as db:
                variant_id = db.scalar(select(ProductVariant.id).where(
                    ProductVariant.tenant_id == liquid.tenant_id,
                    ProductVariant.sku == "h2-4-you-one-0"))
            result = http.post("/cart/add", data={"csrf_token": token,
                "variant_id": variant_id, "quantity": "1", "subscription": "off"},
                follow_redirects=False)
            assert result.status_code == 303 and result.headers["location"] == "/cart"
            assert "H2 | 4 YOU One" in http.get("/cart").text
            checkout = http.get("/checkout")
            assert checkout.status_code == 200 and 'action="/checkout"' in checkout.text
            assert "Review shipping &amp; tax" in checkout.text
            for path in ("/signup", "/marketing/"):
                assert http.get(path).status_code == 404, path
            for email in EMAILS:
                http.cookies.clear()
                login = http.get("/login")
                token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', login.text)[1]
                response = http.post("/login", data={"email": email, "password": PASSWORD,
                    "csrf_token": token}, follow_redirects=False)
                assert response.status_code == 303, response.text
                assert response.headers["location"] in ("/admin", "/admin/sites")
                assert http.get("/login", follow_redirects=False).headers["location"] == "/admin"
                for path in ("/admin", "/admin/products", "/admin/orders", "/admin/inventory",
                    "/admin/promotions", "/admin/customers", "/admin/channels", "/admin/sites",
                    f"/admin/sites/{liquid_id}", f"/admin/sites/{liquid_id}/build",
                    f"/admin/sites/{kinetic_id}"):
                    response = http.get(path)
                    assert response.status_code == 200, (email, path, response.status_code, response.text[:500])
                    if email == EMAILS[0]:
                        assert "Northstar" not in response.text, (email, path)
                        assert "harbour-runner" not in response.text, (email, path)
                if email == EMAILS[0] and screenshots:
                    from playwright.sync_api import sync_playwright

                    screenshots.mkdir(parents=True, exist_ok=True)
                    with sync_playwright() as playwright:
                        browser = playwright.chromium.launch()
                        context = browser.new_context()

                        def serve(route):
                            request = route.request
                            result = http.request(request.method, request.url,
                                follow_redirects=False)
                            headers = {key: value for key, value in result.headers.items()
                                if key not in {"content-length", "content-encoding"}}
                            route.fulfill(status=result.status_code, headers=headers,
                                body=result.content)

                        context.route("**/*", serve)
                        page = context.new_page()
                        for width in (390, 1440):
                            page.set_viewport_size({"width": width, "height": 950})
                            for name, path in (("catalog", "/admin/products"),
                                ("sites", "/admin/sites"), ("home", "/")):
                                page.goto("https://" + HOST + path)
                                page.screenshot(path=str(screenshots / f"{name}-{width}.png"),
                                    full_page=True)
                        browser.close()
            print("NEW: 14 host routes, checkout, signup/marketing isolation, both logins, 11 admin routes each, repeat boot: PASS")
        else:
            response = http.get("/")
            print(mode.upper(), "host /", response.status_code,
                "kinetic stylesheet:", "site-theme-kinetic.css" in response.text)
            if mode in ("legacy", "restore"):
                assert response.status_code == 200
                assert "site-theme-kinetic.css" in response.text
                for path in ("/shop", "/cart", "/account"):
                    response = http.get(path)
                    assert response.status_code == 200, (mode, path, response.status_code)
                print(mode.upper(), "shop/cart/account: PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", type=Path, default=ROOT.parent / "ref" / "fastshop")
    parser.add_argument("--worker", choices=("legacy", "new", "rollback", "restore"))
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.screenshots)
        return
    scratch = Path(tempfile.mkdtemp(prefix="h24you-deploy-"))
    old = scratch / "old"
    old.mkdir()
    archive = scratch / "old.zip"
    subprocess.run(["git", "-C", str(args.ref), "archive", "deploy/main", "--format=zip",
        "-o", str(archive)], check=True)
    shutil.unpack_archive(archive, old)
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHON_DOTENV_DISABLED="1", FASTSHOP_ENV="production",
        FASTSHOP_AUTO_CREATE_SCHEMA="0", FASTSHOP_DATA_DIR=str(scratch / "storage"),
        FASTSHOP_SESSION_SECRET="disposable-test-session-secret-32-chars",
        FASTSHOP_ADMIN_EMAIL=EMAILS[1], FASTSHOP_ALLOW_PASSWORD_LOGIN="1",
        FASTSHOP_ADMIN_PASSWORD="", FASTSHOP_ADMIN_PASSWORD_HASH="", FASTSHOP_SIGNUP_OPEN="0",
        FASTSHOP_PUBLIC_URL="https://shop.fastsme.com", FASTSHOP_ENABLE_SCHEDULER="0",
        XAI_API_KEY="", POSTMARK_API_TOKEN="", POSTMARK_SERVER_TOKEN="",
        STRIPE_SECRET_KEY="", STRIPE_WEBHOOK_SECRET="",
        FASTSHOP_H24YOU_LIQUID_HOSTNAME="")
    def run(command, cwd, **updates):
        subprocess.run(command, cwd=cwd, env=env | updates, check=True)
    run(["uv", "sync", "--extra", "dev"], old)
    old_python = str(old / ".venv" / "Scripts" / "python.exe")
    env["DB_URL"] = "sqlite:///" + (scratch / "legacy.sqlite3").as_posix()
    run([old_python, "-m", "alembic", "upgrade", "head"], old)
    worker_file = scratch / "worker.py"
    shutil.copy2(__file__, worker_file)
    # The worker runs in the code under test, never imports code from this repo by accident.
    run([old_python, str(worker_file), "--worker", "legacy"], old, PYTHONPATH=str(old))
    upgraded = scratch / "upgraded.sqlite3"
    shutil.copy2(scratch / "legacy.sqlite3", upgraded)
    env["DB_URL"] = "sqlite:///" + upgraded.as_posix()
    run([sys.executable, "-m", "alembic", "upgrade", "head"], ROOT)
    run([sys.executable, str(worker_file), "--worker", "new",
        "--screenshots", str(ROOT / "output/playwright/h24you-liquid-deploy")], ROOT,
        PYTHONPATH=str(ROOT), FASTSHOP_H24YOU_LIQUID_HOSTNAME=HOST)
    # Test the actual old Docker entry point's migration command too.
    result = subprocess.run([old_python, "-m", "alembic", "upgrade", "head"], cwd=old,
        env=env, capture_output=True, text=True)
    print("OLD entrypoint Alembic on upgraded DB:", result.returncode)
    print(result.stdout, result.stderr)
    run([old_python, str(worker_file), "--worker", "rollback"], old, PYTHONPATH=str(old))
    rollback_command = (
        "from sqlalchemy import select, update; from app.db import SessionLocal; "
        "from app.models import Site; db=SessionLocal(); "
        "target=db.scalar(select(Site).where(Site.slug=='h24you-kinetic')); "
        "assert target is not None, 'Kinetic site missing'; "
        "db.execute(update(Site).where(Site.hostname=='h24you.fastsme.com').values(hostname=None)); "
        "db.flush(); target.hostname='h24you.fastsme.com'; target.status='preview'; "
        "db.commit(); db.close()"
    )
    run([old_python, "-c", rollback_command], old)
    run([old_python, str(worker_file), "--worker", "restore"], old, PYTHONPATH=str(old))
    print("Evidence directory:", scratch)


if __name__ == "__main__":
    main()
