"""Database engine and session management for AMGR.

Configures SQLAlchemy engine, sessionmaker, and declarative base using
standard project environment configuration (DATABASE_URL).
"""

from __future__ import annotations

import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Default to SQLite local database if DATABASE_URL is not set or PostgreSQL driver is unavailable
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "sqlite:///./amgr.db"
)

# Connect args (e.g. check_same_thread for SQLite)
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


class Base(DeclarativeBase):
    """Declarative base class for all AMGR SQLAlchemy ORM models."""
    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a transactional database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
