import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher
from bot.handlers.seller_orders import router as seller_orders_router
from bot.handlers.admin import router as admin_router
from bot.handlers.cart import router as cart_router
from bot.handlers.catalog import router as catalog_router
from bot.handlers.errors import on_error
from bot.handlers.menu import router as menu_router
from bot.handlers.seller import router as seller_router
from bot.handlers.seller_products import router as seller_products_router
from bot.handlers.start import router as start_router
from bot.middlewares.user import UserMiddleware
from config.settings import BASE_DIR, settings
from database.db import close_db, init_db


def setup_logging() -> None:
    logs = Path(BASE_DIR) / 'logs'
    logs.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
        handlers=[logging.StreamHandler(),
                  RotatingFileHandler(logs / 'bot.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8')],
    )

def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.message.outer_middleware(UserMiddleware())
    dp.callback_query.outer_middleware(UserMiddleware())
    dp.errors.register(on_error)

    for router in (
        start_router,
        admin_router,
        menu_router,
        catalog_router,
        cart_router,
        seller_router,
        seller_products_router,
        seller_orders_router,
    ):
        dp.include_router(router)

    return dp


async def main():
    setup_logging()
    if not settings.bot_token:
        raise RuntimeError('BOT_TOKEN is missing. Copy .env.example to .env and add your token.')
    await init_db()
    bot = Bot(settings.bot_token)
    dp = build_dispatcher()
    try:
        await dp.start_polling(bot)
    finally:
        await close_db()
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
