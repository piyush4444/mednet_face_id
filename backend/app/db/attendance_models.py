"""Attendance policy, daily decisions, observations, and delivery audit."""

from datetime import time

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from backend.app.db.postgres import Base
from backend.app.utils.time_ist import now_ist


class AttendancePolicy(Base):
    """Singleton, versioned attendance decision policy managed by the UI."""

    __tablename__ = "attendance_policy"

    id = Column(Integer, primary_key=True, default=1)
    enabled = Column(Boolean, nullable=False, default=False)
    shadow_mode = Column(Boolean, nullable=False, default=True)
    timezone = Column(String(64), nullable=False, default="Asia/Kolkata")
    in_window_start = Column(Time, nullable=False, default=lambda: time(6, 0))
    in_window_end = Column(Time, nullable=False, default=lambda: time(12, 0))
    out_window_start = Column(Time, nullable=False, default=lambda: time(16, 0))
    out_window_end = Column(Time, nullable=False, default=lambda: time(23, 59))
    minimum_work_minutes = Column(Integer, nullable=False, default=240)
    observation_cooldown_seconds = Column(Integer, nullable=False, default=60)
    eligible_user_types = Column(
        JSONB, nullable=False, default=lambda: ["EMPLOYEE", "DOCTOR"]
    )
    attendance_camera_ids = Column(JSONB, nullable=False, default=list)
    camera_serial_numbers = Column(JSONB, nullable=False, default=dict)
    biometric_server_ip = Column(String(64), nullable=False, default="")
    version = Column(Integer, nullable=False, default=1)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_ist, onupdate=now_ist)


class AttendanceDailyState(Base):
    """Exactly one attendance decision state per user and local date."""

    __tablename__ = "attendance_daily_state"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    attendance_date = Column(Date, nullable=False)
    first_seen_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=False)
    in_decided_at = Column(DateTime(timezone=True))
    out_decided_at = Column(DateTime(timezone=True))
    in_camera_id = Column(String(50))
    out_camera_id = Column(String(50))
    in_export_id = Column(Integer)
    out_export_id = Column(Integer)
    in_shadow = Column(Boolean, nullable=False, default=False)
    out_shadow = Column(Boolean, nullable=False, default=False)
    policy_version = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_ist, onupdate=now_ist)

    __table_args__ = (
        UniqueConstraint("user_id", "attendance_date", name="uq_attendance_user_date"),
        Index("idx_attendance_date", "attendance_date"),
    )


class RecognitionObservation(Base):
    """Throttled append-only record of registered people seen by cameras."""

    __tablename__ = "recognition_observation"

    id = Column(Integer, primary_key=True)
    observation_key = Column(String(160), unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    camera_id = Column(String(50), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    attendance_date = Column(Date, nullable=False)
    confidence = Column(Float)
    outcome = Column(String(32), nullable=False)
    reason = Column(String(120), nullable=False)
    policy_version = Column(Integer)
    created_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)

    __table_args__ = (
        Index("idx_observation_user_time", "user_id", "observed_at"),
        Index("idx_observation_date_time", "attendance_date", "observed_at"),
    )


class PunchDeliveryAttempt(Base):
    """Immutable audit row for every outbound Mednet punch attempt."""

    __tablename__ = "punch_delivery_attempt"

    id = Column(Integer, primary_key=True)
    export_id = Column(
        Integer,
        ForeignKey("punch_export_sync.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number = Column(Integer, nullable=False)
    attempted_at = Column(DateTime(timezone=True), default=now_ist, nullable=False)
    completed_at = Column(DateTime(timezone=True))
    outcome = Column(String(16), nullable=False)
    http_status = Column(Integer)
    latency_ms = Column(Integer)
    error_category = Column(String(40))
    error_detail = Column(Text)
    response_payload = Column(JSONB)

    __table_args__ = (
        UniqueConstraint("export_id", "attempt_number", name="uq_punch_attempt_number"),
    )
