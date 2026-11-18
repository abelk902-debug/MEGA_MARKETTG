from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.keyboards.main import main_menu
from bot.services.permissions import is_owner_id, is_seller
from config.settings import settings

router = Router()


@router.message(CommandStart())
async def start(message: Message, db_user):
    # `db_user` is created / ban-checked by UserMiddleware
    await message.answer(
        f'🛍️ أهلاً بك في {settings.platform_name}\n\nسوق متعدد البائعين داخل Telegram.',
        reply_markup=main_menu(is_seller(db_user), is_owner_id(db_user.telegram_id)),
    )


@router.message(Command('cancel'))
async def cancel_command(message: Message, state: FSMContext):
    """Works in ANY flow (search, store application, add/edit product). Router is registered first."""
    had_flow = await state.get_state()
    await state.clear()
    await message.answer('تم إلغاء العملية.' if had_flow else 'لا توجد عملية جارية.')
