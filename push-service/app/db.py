"""Database layer (SQLAlchemy).

Persists devices and check-ins so the console can filter, report on red flags,
and use history over time. Defaults to a local SQLite file; set DATABASE_URL to
a Postgres/Neon URL in production (one env var, no code change).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import String, Boolean, Text, DateTime, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    push_token: Mapped[str] = mapped_column(String(512))
    platform: Mapped[str] = mapped_column(String(16), default="ios")
    token_type: Mapped[str] = mapped_column(String(16), default="alert")
    role: Mapped[str] = mapped_column(String(16), default="survivor")
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    app_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Clinician(Base):
    __tablename__ = "clinicians"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    # physician | nurse | navigator (extensible)
    role: Mapped[str] = mapped_column(String(32), default="navigator")
    password_hash: Mapped[str] = mapped_column(String(256))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Checkin(Base):
    __tablename__ = "checkins"
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    scenario: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default="survivor")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    # started -> completed
    status: Mapped[str] = mapped_column(String(16), default="started", index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # clinical result (from VERA), persisted so we can filter/report on red flags
    has_priority: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, index=True)
    summary_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # clinician workflow
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # set once an emergency (Tier-1) alert email has been sent, to avoid resending
    alerted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ClinicianNote(Base):
    __tablename__ = "clinician_notes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    author: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


def _normalize_url(url: str) -> str:
    """Default to SQLite; use psycopg3 driver for Postgres URLs."""
    if not url:
        data_dir = Path(os.getenv("KURA_DATA_DIR", "./data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{data_dir / 'kura.db'}"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def build_engine(database_url: str):
    url = _normalize_url(database_url)
    if url in ("sqlite://", "sqlite:///:memory:"):
        # Shared in-memory DB (tests): one connection for the whole engine.
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool, future=True,
        )
    elif url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)
    else:
        engine = create_engine(url, pool_pre_ping=True, future=True)
    Base.metadata.create_all(engine)
    _ensure_columns(engine)
    return engine


def _ensure_columns(engine) -> None:
    """Tiny forward-migration: add columns introduced after a table already
    existed (create_all only creates missing *tables*, not columns). Safe to
    run every startup; no-op once the column is present.
    """
    # (table, column, DDL type with default)
    additions = [
        ("devices", "role", "VARCHAR(16) DEFAULT 'survivor'"),
        ("devices", "display_name", "VARCHAR(128)"),
        ("checkins", "acknowledged_by", "VARCHAR(128)"),
        ("checkins", "resolved_by", "VARCHAR(128)"),
        ("checkins", "alerted_at", "TIMESTAMP"),
    ]
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for table, column, ddl in additions:
        if table not in tables:
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        if column not in existing:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
