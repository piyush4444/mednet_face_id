"""Database-backed roles and permissions for canonical users.

There is one human identity table: ``users``. A user may optionally receive
credentials, one global role, and per-user permission overrides. This module
contains authorization relationships only; it does not define another user.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from backend.app.db.postgres import Base
from backend.app.utils.time_ist import now_ist


class RoleMaster(Base):
    __tablename__ = "role_master"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(40), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)

    permission_links = relationship(
        "RolePermissionMapping", back_populates="role", cascade="all, delete-orphan"
    )


class PermissionMaster(Base):
    __tablename__ = "permission_master"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_ist)
    updated_at = Column(DateTime(timezone=True), onupdate=now_ist)


class RolePermissionMapping(Base):
    __tablename__ = "role_permission_mapping"

    id = Column(Integer, primary_key=True, index=True)
    role_id = Column(
        Integer, ForeignKey("role_master.id", ondelete="CASCADE"), nullable=False
    )
    permission_id = Column(
        Integer,
        ForeignKey("permission_master.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), default=now_ist)

    role = relationship("RoleMaster", back_populates="permission_links")
    permission = relationship("PermissionMaster")
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
    )


class UserRoleMapping(Base):
    """The single global application role assigned to a canonical user."""

    __tablename__ = "user_role_mapping"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    role_id = Column(
        Integer, ForeignKey("role_master.id", ondelete="RESTRICT"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), default=now_ist)

    user = relationship("User")
    role = relationship("RoleMaster")


class UserPermissionMapping(Base):
    """A GRANT or REVOKE applied over a user's role bundle."""

    __tablename__ = "user_permission_mapping"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    permission_id = Column(
        Integer,
        ForeignKey("permission_master.id", ondelete="CASCADE"),
        nullable=False,
    )
    effect = Column(String(8), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_ist)

    user = relationship("User")
    permission = relationship("PermissionMaster")
    __table_args__ = (
        UniqueConstraint("user_id", "permission_id", name="uq_user_permission"),
    )
