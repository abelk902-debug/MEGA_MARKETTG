"""Database engine, sessions and initialisation (SQLite via aiosqlite)."""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config.settings import SQLITE_PREFIX, settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith(SQLITE_PREFIX):
        raw = url[len(SQLITE_PREFIX):]
        if raw and raw != ":memory:":
            Path(raw).parent.mkdir(parents=True, exist_ok=True)


def make_engine(url: str, echo: bool = False) -> AsyncEngine:
    """Create an async engine. For SQLite: foreign keys ON, WAL, busy timeout."""
    kwargs: dict = {}
    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        _ensure_sqlite_dir(url)
        kwargs["connect_args"] = {"timeout": 15}
    engine = create_async_engine(url, echo=echo, **kwargs)
    if is_sqlite:
        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()
    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


engine = make_engine(settings.database_url)
SessionLocal = make_session_factory(engine)

# Old versions stored order statuses in lower case with a different name.
LEGACY_STATUS_MAP = {
    "new": "NEW",
    "awaiting_seller": "AWAITING_SELLER_APPROVAL",
    "accepted": "ACCEPTED",
    "processing": "PROCESSING",
    "shipped": "SHIPPED",
    "ready_for_pickup": "READY_FOR_PICKUP",
    "completed": "COMPLETED",
    "cancelled": "CANCELLED",
    "rejected": "REJECTED",
    "disputed": "DISPUTED",
}
_STATUS_COLUMNS = (
    ("orders", "status"),
    ("suborders", "status"),
    ("order_status_history", "old_status"),
    ("order_status_history", "new_status"),
)


async def normalize_legacy_statuses(conn) -> None:
    """Idempotent: converts lower-case legacy statuses to the official upper-case ones."""
    for table, column in _STATUS_COLUMNS:
        for old, new in LEGACY_STATUS_MAP.items():
            await conn.execute(
                text(f"UPDATE {table} SET {column} = :new WHERE {column} = :old"),
                {"new": new, "old": old},
            )


async def init_db(target_engine: AsyncEngine | None = None) -> None:
    from database.models import models  # noqa: F401  (registers all tables)

    eng = target_engine or engine
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await normalize_legacy_statuses(conn)


async def close_db(target_engine: AsyncEngine | None = None) -> None:
    await (target_engine or engine).dispose()
