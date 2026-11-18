"""Small helpers that make Telegram calls robust."""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest

log = logging.getLogger(__name__)


async def safe_edit(callback, text: str, reply_markup=None) -> None:
    """Edit the callback message; never crash on 'not modified' / photo / old messages."""
    message = getattr(callback, "message", None)
    edit = getattr(message, "edit_text", None)
    if edit is None:  # inaccessible/old message
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=reply_markup)
        return
    try:
        await edit(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        reason = str(exc).lower()
        if "message is not modified" in reason:
            return
        if "there is no text in the message" in reason or "message can't be edited" in reason:
            await message.answer(text, reply_markup=reply_markup)
            return
        raise


PHOTO_CAPTION_LIMIT = 1000


def _caption(text: str) -> str:
    return text if len(text) <= PHOTO_CAPTION_LIMIT else text[:PHOTO_CAPTION_LIMIT - 1] + '…'


async def safe_product_view(callback, text: str, image_ids=(), reply_markup=None) -> None:
    """Shows the product page: a photo with caption when the product has images.

    Falls back to plain text if Telegram refuses the photo (or the caption is too long).
    """
    images = [x for x in (image_ids or []) if isinstance(x, str) and x]
    if not images:
        return await safe_edit(callback, text, reply_markup)
    message = getattr(callback, "message", None)
    if message is not None and getattr(message, "photo", None):
        try:
            await message.edit_caption(caption=_caption(text), reply_markup=reply_markup)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
    answer_photo = getattr(message, "answer_photo", None)
    if answer_photo is None:
        return await safe_edit(callback, text, reply_markup)
    try:
        await answer_photo(images[0], caption=_caption(text), reply_markup=reply_markup)
    except TelegramBadRequest:
        await safe_edit(callback, text, reply_markup)


async def notify(bot, chat_id: int, text: str) -> bool:
    """Best-effort notification. Returns False (and logs) if Telegram refuses."""
    try:
        await bot.send_message(chat_id, text)
        return True
    except Exception:  # user blocked the bot, chat not found, ...
        log.warning("Could not notify %s", chat_id, exc_info=True)
        return False
