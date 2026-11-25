"""Bundled, opt-in background scheduler for the sandbox subscription lifecycle.

Off by default. When ``FASTSHOP_ENABLE_SCHEDULER`` is truthy the app process runs
renewal preparation/progression and upcoming-delivery notices on an interval, so a
store does not need an external cron for the sandbox lifecycle. It reuses the same
functions as the CLI workers and stays sandbox-gated by the Stripe gateway. Real
go-live scheduling and provider acceptance remain separate gates.
"""

from __future__ import annotations

import logging
import threading

from app.config import _bool

logger = logging.getLogger("fastshop.scheduler")

_thread: threading.Thread | None = None
_stop = threading.Event()


def enabled() -> bool:
    return _bool("FASTSHOP_ENABLE_SCHEDULER", False)


def run_due_jobs(*, renewal_limit: int = 50, notice_days_ahead: int = 3,
                 notice_limit: int = 100) -> dict:
    """One scheduler tick: advance due renewals, then queue upcoming notices."""
    from app.subscription_notifications import queue_upcoming
    from app.subscription_renewals import process_due
    result: dict = {}
    try:
        result["renewals"] = process_due(limit=renewal_limit)
    except Exception:  # noqa: BLE001 - a tick must never crash the loop
        logger.exception("renewal processing failed")
        result["renewals"] = "error"
    try:
        result["notices"] = queue_upcoming(days_ahead=notice_days_ahead, limit=notice_limit)
    except Exception:  # noqa: BLE001
        logger.exception("notice queueing failed")
        result["notices"] = "error"
    return result


def _loop(interval_seconds: int) -> None:
    while not _stop.wait(interval_seconds):
        run_due_jobs()


def start_scheduler(interval_seconds: int = 900) -> bool:
    """Start the daemon loop once when enabled. Returns True if it started."""
    global _thread
    if not enabled() or (_thread and _thread.is_alive()):
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(interval_seconds,),
                               name="fastshop-scheduler", daemon=True)
    _thread.start()
    logger.info("scheduler started (interval=%ss)", interval_seconds)
    return True


def stop_scheduler() -> None:
    _stop.set()


_domain_thread = None


def domain_worker_enabled() -> bool:
    """Re-check custom domains waiting for DNS; DNS lookups only, on in production."""
    from app.config import settings

    return _bool("FASTSHOP_ENABLE_DOMAIN_CHECKS", settings.is_production)


def run_domain_checks() -> dict:
    """Connect domains whose DNS now points here, sync HTTPS, and tell the store's admins."""
    from app import coolify_domains
    from app import site_domains as domains
    from app.db import SessionLocal
    from app.integrations.commerce_email import send_email

    with SessionLocal() as db:
        newly = domains.check_pending(db)
        notices = [domains.connected_notice(db, domain) + (domain.id,) for domain in newly]
        db.commit()
        if newly:
            coolify_domains.sync(db)
    sent = 0
    for emails, subject, body, domain_id in notices:
        for email in emails:
            status, _ = send_email(email, subject, body, message_id=f"domain-connected-{domain_id}")
            sent += status == "sent"
    return {"connected": len(newly), "emails": sent}


def _domain_loop(interval_seconds: int) -> None:
    while not _stop.wait(interval_seconds):
        try:
            result = run_domain_checks()
            if result["connected"]:
                logger.info("domain checks: %s", result)
        except Exception:  # noqa: BLE001
            logger.exception("domain checks failed")


def start_domain_worker(interval_seconds: int = 600) -> bool:
    """Check waiting custom domains every ten minutes."""
    global _domain_thread
    if not domain_worker_enabled() or (_domain_thread and _domain_thread.is_alive()):
        return False
    _domain_thread = threading.Thread(target=_domain_loop, args=(interval_seconds,),
                                      name="fastshop-domain-checks", daemon=True)
    _domain_thread.start()
    logger.info("domain checks started (interval=%ss)", interval_seconds)
    return True
