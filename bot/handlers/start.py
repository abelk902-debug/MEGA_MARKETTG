from pathlib import Path

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, Message

from bot.keyboards.main import main_menu
from bot.services.permissions import is_owner_id, is_seller
from config.settings import settings

router = Router()

WELCOME_IMAGE = Path("assets") / "welcome.png"


@router.message(CommandStart())
async def start(message: Message, db_user):
    # `db_user` is created / ban-checked by UserMiddleware
    caption = (
        f"🛍️ أهلاً بك في {settings.platform_name}\n\n"
        "سوق متعدد البائعين داخل Telegram.\n"
        "تسوّق، اكتشف المنتجات، واختر من متاجر متعددة."
    )

    markup = main_menu(
        is_seller(db_user),
        is_owner_id(db_user.telegram_id),
    )

    if WELCOME_IMAGE.exists():
        await message.answer_photo(
            photo=FSInputFile(WELCOME_IMAGE),
            caption=caption,
            reply_markup=markup,
        )
    else:
        # Fallback إذا لم تكن الصورة موجودة.
        await message.answer(
            caption,
            reply_markup=markup,
        )


@router.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext):
    """Works in ANY flow (search, store application, add/edit product)."""
    had_flow = await state.get_state()
    await state.clear()

    await message.answer(
        "تم إلغاء العملية." if had_flow else "لا توجد عملية جارية."
    )