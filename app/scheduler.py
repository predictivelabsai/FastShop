"""Background workers for commerce mail and subscription lifecycle jobs.

Mail runs by default in production, on its own short interval. Renewals and upcoming
notices remain opt-in through ``FASTSHOP_ENABLE_SCHEDULER``. Those jobs use the
site's accepted Stripe mode, which can be live; enabling the renewal scheduler can
therefore charge live subscriptions. Every mail sender uses the atomic queue claim,
which is the sole guard against duplicate sends across app workers.
"""

from __future__ import annotations

import logging
import threading

from app.config import _bool

logger = logging.getLogger("fastshop.scheduler")

_thread: threading.Thread | None = None
_stop = threading.Event()
_mail_thread: threading.Thread | None = None


def enabled() -> bool:
    return _bool("FASTSHOP_ENABLE_SCHEDULER", False)


def mail_worker_enabled() -> bool:
    from app.config import settings
    return _bool("FASTSHOP_ENABLE_MAIL_WORKER", settings.is_production)


def run_mail_job(*, limit: int = 50) -> dict:
    from app.integrations.commerce_email import process_due_mail
    try:
        return process_due_mail(limit=limit)
    except Exception:  # noqa: BLE001 - a tick must never crash the loop
        logger.exception("commerce mail processing failed")
        return {"error": "error"}


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


def _mail_loop(interval_seconds: int) -> None:
    while not _stop.wait(interval_seconds):
        run_mail_job()


def start_mail_worker(interval_seconds: int = 30) -> bool:
    """Start the mail daemon once when enabled; claims prevent cross-worker duplicates."""
    global _mail_thread
    if not mail_worker_enabled() or (_mail_thread and _mail_thread.is_alive()):
        return False
    _stop.clear()
    _mail_thread = threading.Thread(target=_mail_loop, args=(interval_seconds,),
                                    name="fastshop-mail-worker", daemon=True)
    _mail_thread.start()
    logger.info("commerce mail worker started (interval=%ss)", interval_seconds)
    return True


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
