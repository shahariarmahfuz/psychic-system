import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


def uuid_str():
    return str(uuid.uuid4())


class RailwayAccount(Base):
    __tablename__ = 'railway_accounts'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    workspace: Mapped[str] = mapped_column(String(200), nullable=False)
    api_token_cipher: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default='active', index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    slots: Mapped[list['Slot']] = relationship(back_populates='account')


class User(Base):
    __tablename__ = 'users'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), index=True, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(30), default='active', index=True)
    max_sessions: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Slot(Base):
    __tablename__ = 'slots'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    railway_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey('railway_accounts.id'), nullable=True)
    project_name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    service_name: Mapped[str] = mapped_column(String(120), default='final')
    server_addr: Mapped[str | None] = mapped_column(String(255), nullable=True)
    server_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frp_token_cipher: Mapped[str] = mapped_column(Text, nullable=False)
    remote_port: Mapped[int] = mapped_column(Integer, default=6000)
    status: Mapped[str] = mapped_column(String(30), default='tcp_pending', index=True)
    current_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    account: Mapped[RailwayAccount | None] = relationship(back_populates='slots')


class TunnelSession(Base):
    __tablename__ = 'sessions'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey('users.id'), index=True)
    slot_id: Mapped[str] = mapped_column(String(36), ForeignKey('slots.id'), index=True)
    status: Mapped[str] = mapped_column(String(30), default='active', index=True)
    client_local_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proxy_name: Mapped[str] = mapped_column(String(120), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = 'audit_logs'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    actor: Mapped[str] = mapped_column(String(120), default='system')
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    __tablename__ = 'app_settings'

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
