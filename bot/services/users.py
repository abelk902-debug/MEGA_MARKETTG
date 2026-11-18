from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from config.settings import settings
from database.models import User


async def get_user_by_telegram_id(session, telegram_id: int) -> User | None:
    return (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()


async def get_or_create_user(session, tg_user, owner_id: int | None = None) -> User:
    """Return the DB user for a Telegram user, creating it if needed.

    The owner (by Telegram ID) always has role 'owner' and is never demoted.
    Caller must commit.
    """
    owner_id = settings.owner_id if owner_id is None else owner_id
    user = await get_user_by_telegram_id(session, tg_user.id)
    if user is None:
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=getattr(tg_user, "last_name", None),
            role="owner" if tg_user.id == owner_id else "customer",
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:  # two first-updates raced; the other one won
            await session.rollback()
            user = await get_user_by_telegram_id(session, tg_user.id)
            if user is None:
                raise
    else:
        user.username = tg_user.username
        user.first_name = tg_user.first_name
        user.last_name = getattr(tg_user, "last_name", None)
    if tg_user.id == owner_id and user.role != "owner":
        user.role = "owner"
    return user
