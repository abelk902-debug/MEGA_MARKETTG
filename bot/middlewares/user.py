"""Runs before every handler: loads/creates the user, blocks banned users, injects `db_user`."""
from __future__ import annotations

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from bot.services.users import get_or_create_user
from database import db

BANNED_TEXT = "🚫 حسابك موقوف."


class UserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        tg_user = getattr(event, "from_user", None)
        if tg_user is None or getattr(tg_user, "is_bot", False):
            return await handler(event, data)
        async with db.SessionLocal() as session:
            user = await get_or_create_user(session, tg_user)
            await session.commit()
        if user.is_banned or not user.is_active:
            if isinstance(event, CallbackQuery):
                await event.answer(BANNED_TEXT, show_alert=True)
            else:
                await event.answer(BANNED_TEXT)
            return None
        data["db_user"] = user
        return await handler(event, data)
