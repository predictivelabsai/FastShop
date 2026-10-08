"""Offline Phase 4b live-credential safety evaluation; no provider network calls."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import commerce, content, live_credentials, site_golive
from app.commerce_webhooks import signature_secret
from app.config import settings
from app.integrations.stripe_commerce import StripeGateway, verify_webhook
from app.models import Base, Membership, SiteChangeSet, User
from app.services import CommerceError


def main():
    key = "sk_live_eval_fixture_1234"
    webhook = "whsec_eval_fixture_5678"
    original_settings = live_credentials.settings
    original_assess = live_credentials.site_golive.assess
    cases = []
    try:
        live_credentials.settings = SimpleNamespace(
            admin_email=settings.admin_email,
            store_key_encryption_key="eval-encryption-material-never-output",
        )
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as db:
            operator = User(email=settings.admin_email, name="Eval operator")
            merchant = User(email="eval-merchant@example.test", name="Eval merchant")
            db.add_all([operator, merchant])
            db.flush()
            site = content.create_site(db, operator.id, "Eval live", "eval-live")
            db.add(Membership(tenant_id=site.tenant_id, user_id=merchant.id, role="merchant"))
            config = commerce.settings_for(db, site, create=True)
            db.flush()

            try:
                live_credentials.store(db, site.id, merchant.id, key, webhook)
            except CommerceError:
                cases.append({"case": "merchant cannot store live credentials", "passed": True})
            else:
                cases.append({"case": "merchant cannot store live credentials", "passed": False})

            credential = live_credentials.store(db, site.id, operator.id, key, webhook)
            state = live_credentials.state_for(db, site, config)
            audit_text = "".join(
                str(row.before_json) + str(row.after_json)
                for row in db.scalars(select(SiteChangeSet).where(SiteChangeSet.site_id == site.id))
            )
            cases.append({
                "case": "ciphertext and audit contain no submitted secret",
                "passed": key.encode() not in credential.secret_key_ciphertext
                and webhook.encode() not in credential.webhook_secret_ciphertext
                and key not in audit_text and webhook not in audit_text
                and state.masked_key == "sk_live_****1234",
            })

            config.mode = "sandbox"
            site.status = "preview"
            site.hostname = "eval.example.test"
            live_credentials.mark_verified(db, site.id, operator.id, credential.id)
            try:
                live_credentials.accept(
                    db, site.id, operator.id, site.version, config.version, credential.id,
                    "Evaluation confirms preview sites remain blocked.",
                )
            except CommerceError:
                cases.append({"case": "preview site cannot accept live payments", "passed": True})
            else:
                cases.append({"case": "preview site cannot accept live payments", "passed": False})

            site.status = "published"
            ready = site_golive.ReadinessCheck("ready", "Ready", True, "Ready.", "/")
            live_credentials.site_golive.assess = lambda db, site, commerce_requested: (
                site_golive.ReadinessReport(site.id, True, (ready,))
            )
            live_credentials.accept(
                db, site.id, operator.id, site.version, config.version, credential.id,
                "Evaluation operator explicitly accepts the verified live account.",
            )
            cases.append({
                "case": "accepted live mode resolves only stored live credentials",
                "passed": commerce.payment_mode(db, site, config) == "live"
                and live_credentials.live_secrets(db, site, config) == (key, webhook),
            })

            mode, signing_secret = signature_secret(db, site, config)
            body = json.dumps({"id": "evt_eval", "livemode": True}).encode()
            stamp = int(time.time())
            digest = hmac.new(
                signing_secret.encode(), str(stamp).encode() + b"." + body, hashlib.sha256
            ).hexdigest()
            event = verify_webhook(body, f"t={stamp},v1={digest}", signing_secret)
            cases.append({
                "case": "live webhook dispatch uses accepted site secret",
                "passed": mode == "live" and event["id"] == "evt_eval",
            })

            seen = []

            def respond(request):
                seen.append((request.method, request.url.path))
                return httpx.Response(200, json={"id": "acct_eval", "livemode": True})

            result = StripeGateway.for_live_validation(
                key, webhook, transport=httpx.MockTransport(respond)
            ).validate_live_account()
            cases.append({
                "case": "provider validation is one read-only mocked call",
                "passed": result == {"connected": True, "mode": "live"}
                and seen == [("GET", "/v1/account")],
            })

            live_credentials.disable(
                db, site.id, operator.id, site.version, config.version,
                "Evaluation operator revokes live processing and returns to sandbox.",
            )
            cases.append({
                "case": "disable reverts to sandbox and clears acceptance and verification",
                "passed": config.mode == "sandbox"
                and not config.live_accepted_at
                and not live_credentials.state_for(db, site, config).verified,
            })
    finally:
        live_credentials.settings = original_settings
        live_credentials.site_golive.assess = original_assess

    report = {
        "suite": "phase4b-live-credentials",
        "passed": sum(case["passed"] for case in cases),
        "total": len(cases),
        "external_calls": 0,
        "cases": cases,
    }
    output = Path("output/evals/phase4b-live-credentials.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
