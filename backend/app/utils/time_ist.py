"""
time_ist.py — single source of truth for timestamps.

Everything in the system (DB defaults, logs, API responses) should flow
through these helpers. Do NOT use `datetime.utcnow()`, `datetime.now()`
without a tz, or Postgres `NOW()` — they depend on server locale.
"""

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def now_ist() -> datetime:
    """Timezone-aware `datetime` in IST. Use as SQLAlchemy column default."""
    return datetime.now(IST)


def to_ist(value) -> datetime | None:
    """Coerce a datetime or Unix epoch float/int into IST.

    - Naive datetimes are assumed to already be IST.
    - Aware datetimes are converted.
    - Numbers are treated as Unix epoch seconds (UTC reference).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, IST)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=IST)
        return value.astimezone(IST)
    raise TypeError(f"to_ist: unsupported type {type(value)}")


def fmt_ist(value, with_ms: bool = False) -> str:
    """Human-readable IST string, e.g. '2026-04-20 18:29:53 IST'."""
    dt = to_ist(value)
    if dt is None:
        return "—"
    fmt = "%Y-%m-%d %H:%M:%S.%f" if with_ms else "%Y-%m-%d %H:%M:%S"
    return dt.strftime(fmt) + " IST"
