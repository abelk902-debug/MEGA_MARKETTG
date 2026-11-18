"""Store application and the seller's own stores."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.keyboards.main import btn, inline
from bot.keyboards.seller import store_panel_kb
from bot.services import stores as store_service
from bot.services.errors import StoreError
from bot.services.permissions import is_seller
from database import db
from utils.telegram import safe_edit
from utils.text import parse_id

router = Router()


class StoreForm(StatesGroup):
    name = State()
    desc = State()


@router.callback_query(F.data == 'apply_store')
async def apply(c: CallbackQuery, state: FSMContext):
    await state.set_state(StoreForm.name)
    await safe_edit(c, '🏪 اسم المتجر:')
    await c.answer()


@router.message(StoreForm.name)
async def apply_name(m: Message, state: FSMContext):
    if not m.text or not m.text.strip():
        return await m.answer('🏪 أرسل اسم المتجر كنص.')
    await state.update_data(name=m.text.strip())
    await state.set_state(StoreForm.desc)
    await m.answer('وصف المتجر:')


@router.message(StoreForm.desc)
async def apply_desc(m: Message, state: FSMContext, db_user):
    if not m.text or not m.text.strip():
        return await m.answer('أرسل وصف المتجر كنص.')
    data = await state.get_data()
    await state.clear()
    try:
        await store_service.submit_application(db.SessionLocal, db_user.id, data.get('name', ''), m.text)
    except StoreError as e:
        return await m.answer(f'⚠️ {e.message}')
    await m.answer('✅ تم إرسال طلب المتجر للإدارة.')


@router.callback_query(F.data == 'seller_menu')
async def seller(c: CallbackQuery, db_user, state: FSMContext | None = None):
    if state is not None:
        await state.clear()
    if not is_seller(db_user):
        return await c.answer('غير مصرح', show_alert=True)
    async with db.SessionLocal() as s:
        stores = await store_service.list_user_stores(s, db_user.id)
    rows = [[btn(f'🏪 {x.name}', f'mystore:{x.id}')] for x in stores]
    rows.append([btn('📦 طلباتي', 'seller_orders')])
    rows.append([btn('⬅️ رجوع', 'home')])
    await safe_edit(c, '🏪 متاجري', inline(rows))
    await c.answer()


@router.callback_query(F.data.startswith('mystore:'))
async def mystore(c: CallbackQuery, db_user, state: FSMContext | None = None):
    if state is not None:
        await state.clear()
    sid = parse_id(c.data)
    if sid is None:
        return await c.answer('طلب غير صالح', show_alert=True)
    async with db.SessionLocal() as s:
        # ownership is checked by DB user id inside the query
        store = await store_service.get_owned_store(s, sid, db_user.id)
        if store is None:
            return await c.answer('غير مصرح', show_alert=True)
        products = await store_service.count_store_products(s, sid)
    await safe_edit(c, f'🏪 {store.name}\nالحالة: {store.status}\nالمنتجات: {products}\n'
                       f'المبيعات: {store.sales_count}\nالتقييم: {store.rating}', store_panel_kb(store.id))
    await c.answer()
