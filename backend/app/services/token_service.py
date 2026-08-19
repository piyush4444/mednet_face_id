"""
token_service.py — atomic per-department daily token allocator.

The token number printed on the OPD slip ("CARD-014") must be:
  * unique within a (date, department) pair,
  * monotonically increasing within that pair,
  * safe under concurrent front-desk terminals.

Approach
--------
1. Upsert a ``token_counters`` row for (today, department) starting at 0.
2. ``SELECT ... FOR UPDATE`` that row inside the caller's transaction.
3. Increment ``last_number`` and return the formatted token string.

Step 2 serialises concurrent allocations for the same (date, dept) —
front-desk traffic is low (one scan every few seconds), so the row-lock
contention is negligible.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.frontdesk_models import Department, TokenCounter
from backend.app.utils.time_ist import now_ist


def _today_ist():
    return now_ist().date()


def allocate_token(db: Session, department: Department) -> str:
    """
    Allocate the next token number for ``department`` for today.

    Must be called inside an open transaction. The caller commits.
    """
    today = _today_ist()

    # Ensure a counter row exists for (today, department). Using raw SQL
    # for the ON CONFLICT clause — SQLAlchemy core works too but this is
    # explicit and easy to reason about.
    db.execute(
        text(
            """
            INSERT INTO token_counters (counter_date, department_id, last_number)
            VALUES (:d, :dept, 0)
            ON CONFLICT (counter_date, department_id) DO NOTHING
            """
        ),
        {"d": today, "dept": department.id},
    )

    # Lock + increment.
    counter: TokenCounter = (
        db.query(TokenCounter)
        .filter(
            TokenCounter.counter_date == today,
            TokenCounter.department_id == department.id,
        )
        .with_for_update()
        .one()
    )
    counter.last_number += 1
    db.flush()

    return f"{department.token_prefix}-{counter.last_number:03d}"
