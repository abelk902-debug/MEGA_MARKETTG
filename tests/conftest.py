import os
import tempfile
from pathlib import Path

# Must run BEFORE any project import: tests never touch the real .env database.
_TMP = Path(tempfile.mkdtemp(prefix="mm_tests_"))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (_TMP / "default.db").as_posix()
os.environ["ADMIN_API_KEY"] = "test-admin-key-0123456789"
os.environ["OWNER_ID"] = "8399658960"

import pytest_asyncio  # noqa: E402

from database import db  # noqa: E402
from database.db import Base, make_engine, make_session_factory  # noqa: E402


@pytest_asyncio.fixture
async def engine(tmp_path):
    from database.models import models  # noqa: F401
    eng = make_engine("sqlite+aiosqlite:///" + (tmp_path / "test.db").as_posix())
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def sf(engine):
    return make_session_factory(engine)


@pytest_asyncio.fixture
async def patched_db(sf, monkeypatch):
    """Makes handlers/middleware (which read `database.db.SessionLocal`) use the test DB."""
    monkeypatch.setattr(db, "SessionLocal", sf)
    return sf
