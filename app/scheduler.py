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
