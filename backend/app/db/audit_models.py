"""
audit_models.py — append-only audit trail for security-relevant actions.

Rows are written by ``services.audit_service`` on logins, account changes,
camera mutations, and subject create/delete. The table is never updated or
deleted from in normal operation — it is the tamper-evidence record a
hospital deployment needs (who did what, when, from where).
"""

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from backend.app.db.postgres import Base
from backend.app.utils.time_ist import now_ist


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime(timezone=True), default=now_ist, index=True)

    # Actor snapshot. Nullable account_id so failed logins (no established
    # principal) still record the attempted username + ip.
    account_id = Column(Integer, nullable=True, index=True)
    username = Column(String(64), nullable=True)
    role = Column(String(20), nullable=True)

    action = Column(String(64), nullable=False, index=True)  # e.g. auth.login
    target_type = Column(String(40), nullable=True)          # e.g. account, user, camera
    target_id = Column(String(64), nullable=True)

    ip = Column(String(64), nullable=True)
    detail = Column(JSONB, nullable=True)                    # small structured extras

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditLog {self.ts} {self.action} by={self.username!r}>"
