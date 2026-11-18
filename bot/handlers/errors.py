import logging

from aiogram.types import CallbackQuery, ErrorEvent

log = logging.getLogger(__name__)


async def on_error(event: ErrorEvent) -> bool:
    """Global safety net: log the traceback, tell the user, keep the bot alive."""
    log.error("Unhandled error while processing update", exc_info=event.exception)
    cb = getattr(event.update, "callback_query", None)
    try:
        if isinstance(cb, CallbackQuery):
            await cb.answer("⚠️ حدث خطأ غير متوقع، حاول مرة أخرى.", show_alert=True)
        elif getattr(event.update, "message", None) is not None:
            await event.update.message.answer("⚠️ حدث خطأ غير متوقع، حاول مرة أخرى.")
    except Exception:
        log.debug("Could not notify user about the error", exc_info=True)
    return True
