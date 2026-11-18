"""Admin API. Protected by X-API-Key; refuses to run with an unsafe/default key."""
from __future__ import annotations

import hmac
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import func, select

from config.settings import settings
from database import db
from database.models import NON_SALE_STATUSES, Order, Product, Store, User


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await db.init_db()
    yield
    await db.close_db()


app = FastAPI(title='MEGA MARKET TG Admin API', version='1.1.0', lifespan=lifespan)


def get_session_factory():
    return db.SessionLocal


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not settings.admin_api_key_is_safe:
        raise HTTPException(503, 'Admin API disabled: set a strong ADMIN_API_KEY (16+ chars) in .env')
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), settings.admin_api_key.encode()):
        raise HTTPException(401, 'Unauthorized')


@app.get('/health')
async def health():
    return {'ok': True, 'service': settings.platform_name}


@app.get('/stats', dependencies=[Depends(require_api_key)])
async def stats(session_factory=Depends(get_session_factory)):
    async with session_factory() as s:
        return {
            'users': await s.scalar(select(func.count(User.id))),
            'stores': await s.scalar(select(func.count(Store.id))),
            'products': await s.scalar(select(func.count(Product.id))),
            'orders': await s.scalar(select(func.count(Order.id))),
            'sales': float(await s.scalar(
                select(func.coalesce(func.sum(Order.total), 0)).where(Order.status.notin_(NON_SALE_STATUSES)))),
        }


@app.get('/users', dependencies=[Depends(require_api_key)])
async def users(limit: int = 50, session_factory=Depends(get_session_factory)):
    limit = max(1, min(limit, 200))
    async with session_factory() as s:
        rows = (await s.execute(select(User).order_by(User.created_at.desc()).limit(limit))).scalars().all()
    return [{'id': u.id, 'telegram_id': u.telegram_id, 'username': u.username, 'role': u.role, 'banned': u.is_banned}
            for u in rows]
