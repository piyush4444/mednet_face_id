"""
export_worker.py — background delivery of punches + pre-registrations.

A single daemon thread drains two outbound queues:

- ``punch_export_queue``  → client attendance API (staff punches).
- ``pre_registration_log`` (rows still PENDING/FAILED) → client HIS.
  The common case is pushed *inline* by the kiosk so the token shows
  immediately; this worker is the retry safety net for inline failures
  and for rows created while the integration was disabled.

Reliability model
-----------------
- **Claim before send**: a row is flipped to ``SENDING`` with an atomic
  ``UPDATE … WHERE status IN (PENDING, FAILED)`` (rowcount == 1) before
  the HTTP call, so no other worker/thread — or the inline kiosk path —
  can double-send it. A row stuck in ``SENDING`` (crash mid-send) is
  reclaimed to ``PENDING`` after ``EXPORT_STALE_SECONDS``.
- **Idempotent on the client side**: punches carry ``biometricIDX`` and
  pre-regs are keyed by the client; a retry after an ambiguous failure
  cannot double-count.
- **Exponential backoff** with a cap; give up (dead-letter, left FAILED)
  after ``EXPORT_MAX_ATTEMPTS``.

The worker only runs when at least one client URL is configured; it is
started/stopped from the FastAPI lifespan.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Optional

from sqlalchemy import or_

from backend.app.core.config import settings
from backend.app.db.facility_models import (
    ExportStatus,
    PersonVisitMapping,
    PreRegistrationLog,
    PunchExport,
)
from backend.app.db.postgres import SessionLocal
from backend.app.services import client_api
from backend.app.utils.time_ist import now_ist

logger = logging.getLogger("backend.export_worker")

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_wake = threading.Event()


# ── Lifecycle ────────────────────────────────────────────────────────────
def start_export_worker() -> bool:
    """Start the worker if any client integration is configured.

    Returns True if a thread was started, False if it stayed dormant.
    """
    global _thread
    if not (client_api.punch_enabled() or client_api.prereg_enabled()):
        logger.info("Export worker dormant — no CLIENT_*_API_URL configured")
        return False
    if _thread is not None and _thread.is_alive():
        return True
    _stop.clear()
    _thread = threading.Thread(
        target=_run, name="export-worker", daemon=True
    )
    _thread.start()
    logger.info(
        "Export worker started (punch=%s, prereg=%s, interval=%ss)",
        client_api.punch_enabled(), client_api.prereg_enabled(),
        settings.EXPORT_POLL_INTERVAL,
    )
    return True


def stop_export_worker(timeout: float = 3.0) -> None:
    _stop.set()
    _wake.set()
    if _thread is not None and _thread.is_alive():
        _thread.join(timeout=timeout)


def wake() -> None:
    """Ask the worker to sweep now instead of waiting for the interval."""
    _wake.set()


def _run() -> None:
    # Process once on startup, then on each interval / wake.
    while not _stop.is_set():
        try:
            process_once()
        except Exception as exc:  # pragma: no cover — the loop must survive
            logger.error("Export sweep failed: %s", exc)
        _wake.wait(settings.EXPORT_POLL_INTERVAL)
        _wake.clear()


# ── Sweep ────────────────────────────────────────────────────────────────
def process_once() -> dict:
    """One sweep of both queues. Returns a small counts dict (for tests /
    the flush endpoint)."""
    counts = {"punch_sent": 0, "punch_failed": 0, "prereg_sent": 0, "prereg_failed": 0}
    db = SessionLocal()
    try:
        _reclaim_stale(db)
        if client_api.punch_enabled():
            _sweep_punches(db, counts)
        if client_api.prereg_enabled():
            _sweep_preregs(db, counts)
    finally:
        db.close()
    return counts


def _backoff(attempts: int) -> "timedelta":
    delay = settings.EXPORT_BACKOFF_BASE * (2 ** max(0, attempts - 1))
    return timedelta(seconds=min(delay, settings.EXPORT_BACKOFF_CAP))


def _reclaim_stale(db) -> None:
    """Return rows stuck in SENDING (crash mid-send) to PENDING."""
    cutoff = now_ist() - timedelta(seconds=settings.EXPORT_STALE_SECONDS)
    for model in (PunchExport, PreRegistrationLog):
        db.query(model).filter(
            model.status == ExportStatus.SENDING.value,
            model.updated_at.isnot(None),
            model.updated_at < cutoff,
        ).update(
            {model.status: ExportStatus.PENDING.value}, synchronize_session=False
        )
    db.commit()


def _claim(db, model, row_id: int) -> bool:
    """Atomically flip a retryable row to SENDING. True if we won it."""
    updated = (
        db.query(model)
        .filter(
            model.id == row_id,
            model.status.in_([ExportStatus.PENDING.value, ExportStatus.FAILED.value]),
        )
        .update(
            {model.status: ExportStatus.SENDING.value, model.updated_at: now_ist()},
            synchronize_session=False,
        )
    )
    db.commit()
    return updated == 1


def _due_ids(db, model) -> list[int]:
    now = now_ist()
    rows = (
        db.query(model.id)
        .filter(
            model.status.in_([ExportStatus.PENDING.value, ExportStatus.FAILED.value]),
            model.attempts < settings.EXPORT_MAX_ATTEMPTS,
            or_(model.next_retry_at.is_(None), model.next_retry_at <= now),
        )
        .order_by(model.created_at.asc())
        .limit(settings.EXPORT_BATCH_SIZE)
        .all()
    )
    return [r[0] for r in rows]


def _sweep_punches(db, counts: dict) -> None:
    for row_id in _due_ids(db, PunchExport):
        if not _claim(db, PunchExport, row_id):
            continue
        row = db.query(PunchExport).filter(PunchExport.id == row_id).first()
        if row is None:
            continue
        result = client_api.push_punch(row.payload)
        if result.ok:
            row.status = ExportStatus.SENT.value
            row.sent_at = now_ist()
            row.last_error = None
            counts["punch_sent"] += 1
        else:
            _mark_failed(row, result.error)
            counts["punch_failed"] += 1
        db.commit()


def _sweep_preregs(db, counts: dict) -> None:
    for row_id in _due_ids(db, PreRegistrationLog):
        if not _claim(db, PreRegistrationLog, row_id):
            continue
        row = db.query(PreRegistrationLog).filter(
            PreRegistrationLog.id == row_id
        ).first()
        if row is None:
            continue
        result = client_api.push_prereg(row.request_payload or {})
        if result.ok:
            _apply_prereg_success(db, row, result)
            counts["prereg_sent"] += 1
        else:
            _mark_failed(row, result.error)
            counts["prereg_failed"] += 1
        db.commit()


def _mark_failed(row, error: Optional[str]) -> None:
    row.attempts = (row.attempts or 0) + 1
    row.last_error = (error or "unknown error")[:1000]
    row.status = ExportStatus.FAILED.value
    row.next_retry_at = now_ist() + _backoff(row.attempts)


def _apply_prereg_success(db, row: PreRegistrationLog, result) -> None:
    """Persist the client's pre-registration response and surface the
    token onto the visit mapping's MRN if the HIS returned one."""
    row.status = ExportStatus.SENT.value
    row.attempts = (row.attempts or 0) + 1
    row.last_error = None
    row.next_retry_at = None
    row.response_payload = result.data
    row.pre_regn_id = result.pre_regn_id
    row.token_no = result.token_no
    if result.queue_setup_id is not None:
        row.queue_setup_id = result.queue_setup_id

    # If the HIS assigned an MRN, copy it onto the per-facility mapping.
    data = (result.data or {}).get("data") if isinstance(result.data, dict) else None
    hin_mrn = (data or {}).get("mrn") if isinstance(data, dict) else None
    if hin_mrn and row.person_facility_id:
        mapping = db.query(PersonVisitMapping).filter(
            PersonVisitMapping.id == row.person_facility_id
        ).first()
        if mapping is not None and not mapping.mrn:
            mapping.mrn = str(hin_mrn)
