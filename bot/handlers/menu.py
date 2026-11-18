"""Home, account, support and terms."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import func, select

from bot.keyboards.main import back_menu, main_menu
from bot.services.permissions import is_owner_id, is_seller
from config.settings import settings
from database import db
from database.models import Order
from utils.telegram import safe_edit

router = Router()


@router.callback_query(F.data == 'home')
async def home(c: CallbackQuery, db_user, state: FSMContext | None = None):
    if state is not None:
        await state.clear()  # leaving any half-finished flow
    await safe_edit(c, f'🛍️ {settings.platform_name}',
                    main_menu(is_seller(db_user), is_owner_id(db_user.telegram_id)))
    await c.answer()


@router.callback_query(F.data == 'account')
async def account(c: CallbackQuery, db_user):
    async with db.SessionLocal() as s:
        n = await s.scalar(select(func.count(Order.id)).where(Order.buyer_id == db_user.id))
    await safe_edit(
        c,
        f'👤 الحساب\n\nالاسم: {db_user.first_name or "-"}\n@{db_user.username or "-"}\n'
        f'ID: {db_user.telegram_id}\nالطلبات: {n}\nالحالة: نشط',
        back_menu())
    await c.answer()


@router.callback_query(F.data == 'support')
async def support(c: CallbackQuery):
    await safe_edit(c, '💬 الدعم\n\nنظام تذاكر الدعم قيد التطوير ولم يُفعّل بعد.\n'
                       'للمساعدة العاجلة تواصل مع إدارة المنصة مباشرة.', back_menu())
    await c.answer()


@router.callback_query(F.data == 'terms')
async def terms(c: CallbackQuery):
    await safe_edit(c, '📜 الشروط والخصوصية\n\nلا نخزن بيانات البطاقات.\n'
                       'الأسعار والمخزون يتحقق منهما الخادم عند إنشاء كل طلب.', back_menu())
    await c.answer()
