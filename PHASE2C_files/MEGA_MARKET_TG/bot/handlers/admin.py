"""Owner dashboard. Every handler re-checks the owner by Telegram ID on the server."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from bot.keyboards import admin as admin_kb
from bot.keyboards.main import btn, inline
from bot.services import categories as category_service
from bot.services import stores as store_service
from bot.services.errors import ServiceError, StoreError, ValidationError
from bot.services.permissions import is_owner_id
from config.settings import settings
from database import db
from database.models import NON_SALE_STATUSES, Category, Order, Product, Store, StoreApplication, User
from utils.telegram import notify, safe_edit
from utils.text import parse_id

router = Router()
BACK_OWNER = [[btn('⬅️ لوحة المالك', 'owner')]]


class CategoryForm(StatesGroup):
    add = State()
    rename = State()


async def ensure_owner(c: CallbackQuery) -> bool:
    if is_owner_id(c.from_user.id):
        return True
    await c.answer('غير مصرح', show_alert=True)
    return False


@router.callback_query(F.data == 'owner')
async def owner(c: CallbackQuery, state: FSMContext | None = None):
    if not await ensure_owner(c):
        return
    if state is not None:
        await state.clear()
    async with db.SessionLocal() as s:
        users = await s.scalar(select(func.count(User.id)))
        stores = await s.scalar(select(func.count(Store.id)))
        products = await s.scalar(select(func.count(Product.id)))
        orders = await s.scalar(select(func.count(Order.id)))
        categories = await s.scalar(select(func.count(Category.id)))
        sales = await s.scalar(select(func.coalesce(func.sum(Order.total), 0)).where(Order.status.notin_(NON_SALE_STATUSES)))
        apps = await s.scalar(select(func.count(StoreApplication.id)).where(StoreApplication.status == 'pending'))
    rows = [
        [btn('👥 المستخدمون', 'admin:users'), btn('🏪 طلبات المتاجر', 'admin:apps')],
        [btn('📦 الطلبات', 'admin:orders'), btn('🛍️ المنتجات', 'admin:products')],
        [btn('📂 التصنيفات', 'admin:cats'), btn('⚙️ الإعدادات', 'admin:settings')],
        [btn('⬅️ رجوع', 'home')],
    ]
    await safe_edit(c, f'👑 لوحة المالك\n\n👥 {users}\n🏪 {stores}\n🛍️ {products}\n📂 {categories}\n📦 {orders}\n'
                       f'💰 {sales} {settings.default_currency}\n📝 طلبات المتاجر: {apps}', inline(rows))
    await c.answer()


async def _render_apps(c: CallbackQuery) -> None:
    async with db.SessionLocal() as s:
        pending = await store_service.list_pending_applications(s)
    rows = [[btn(f'🏪 {a.store_name}', f'admin:app:{a.id}')] for a in pending] + BACK_OWNER
    await safe_edit(c, '📝 طلبات فتح المتاجر:' if pending else '📝 لا توجد طلبات معلّقة.', inline(rows))


@router.callback_query(F.data == 'admin:apps')
async def apps(c: CallbackQuery):
    if not await ensure_owner(c):
        return
    await _render_apps(c)
    await c.answer()


@router.callback_query(F.data.startswith('admin:app:'))
async def app(c: CallbackQuery):
    if not await ensure_owner(c):
        return
    aid = parse_id(c.data)
    async with db.SessionLocal() as s:
        a = await store_service.get_application(s, aid) if aid is not None else None
    if a is None or a.status != 'pending':
        return await c.answer('الطلب غير موجود أو تمت معالجته', show_alert=True)
    rows = [[btn('✅ موافقة', f'admin:approve:{aid}'), btn('❌ رفض', f'admin:reject:{aid}')],
            [btn('⬅️ رجوع', 'admin:apps')]]
    await safe_edit(c, f'🏪 {a.store_name}\n\n{a.description or "بدون وصف"}\n\nUser DB ID: {a.user_id}', inline(rows))
    await c.answer()


@router.callback_query(F.data.startswith('admin:approve:'))
async def approve(c: CallbackQuery):
    if not await ensure_owner(c):
        return
    aid = parse_id(c.data)
    try:
        d = await store_service.approve_application(db.SessionLocal, aid, c.from_user.id) if aid is not None else None
    except StoreError as e:
        await c.answer(e.message, show_alert=True)
        return await _render_apps(c)
    if d is None:
        return await c.answer('طلب غير صالح', show_alert=True)
    await notify(c.bot, d.applicant_telegram_id, f'🎉 تمت الموافقة على متجرك: {d.store_name}')
    await c.answer('✅ تمت الموافقة')
    await _render_apps(c)


@router.callback_query(F.data.startswith('admin:reject:'))
async def reject(c: CallbackQuery):
    if not await ensure_owner(c):
        return
    aid = parse_id(c.data)
    try:
        d = await store_service.reject_application(db.SessionLocal, aid, c.from_user.id) if aid is not None else None
    except StoreError as e:
        await c.answer(e.message, show_alert=True)
        return await _render_apps(c)
    if d is None:
        return await c.answer('طلب غير صالح', show_alert=True)
    await notify(c.bot, d.applicant_telegram_id, f'❌ تم رفض طلب متجرك: {d.store_name}')
    await c.answer('تم الرفض')
    await _render_apps(c)


@router.callback_query(F.data == 'admin:users')
async def users(c: CallbackQuery):
    if not await ensure_owner(c):
        return
    async with db.SessionLocal() as s:
        rows = (await s.execute(select(User).order_by(User.created_at.desc()).limit(30))).scalars().all()
    text = '👥 المستخدمون\n\n' + '\n'.join(f'{u.telegram_id} — {u.role} — {"🚫" if u.is_banned else "✅"}' for u in rows)
    await safe_edit(c, text, inline(BACK_OWNER))
    await c.answer()


@router.callback_query(F.data == 'admin:orders')
async def admin_orders(c: CallbackQuery):
    if not await ensure_owner(c):
        return
    async with db.SessionLocal() as s:
        rows = (await s.execute(select(Order).order_by(Order.created_at.desc()).limit(30))).scalars().all()
    await safe_edit(c, '📦 آخر الطلبات\n\n' + ('\n'.join(f'{o.order_no} — {o.total} — {o.status}' for o in rows)
                                              if rows else 'لا توجد طلبات'), inline(BACK_OWNER))
    await c.answer()


@router.callback_query(F.data == 'admin:products')
async def admin_products(c: CallbackQuery):
    if not await ensure_owner(c):
        return
    async with db.SessionLocal() as s:
        rows = (await s.execute(select(Product).order_by(Product.created_at.desc()).limit(30))).scalars().all()
    await safe_edit(c, '🛍️ المنتجات\n\n' + ('\n'.join(f'{p.public_id} — {p.name} — {p.status} — {p.stock}' for p in rows)
                                           if rows else 'لا توجد منتجات'), inline(BACK_OWNER))
    await c.answer()


@router.callback_query(F.data == 'admin:settings')
async def admin_settings(c: CallbackQuery):
    if not await ensure_owner(c):
        return
    await safe_edit(c, f'⚙️ الإعدادات\n\nالعمولة: {settings.commission_percent}%\nالعملة: {settings.default_currency}\n'
                       f'قاعدة البيانات: SQLite\nالمعرف المالك محمي على مستوى السيرفر.', inline(BACK_OWNER))
    await c.answer()


# ------------------------------------------------------------------ categories
async def _categories_payload(notice: str | None = None):
    async with db.SessionLocal() as s:
        cats = await category_service.list_all_categories(s)
    visible = sum(1 for c in cats if c.is_active)
    text = f'📂 التصنيفات\n\nالعدد: {len(cats)} — الظاهرة للمشترين: {visible}'
    text += ('\n\nاختر تصنيفاً لإدارته، أو أضف تصنيفاً جديداً.' if cats
             else '\n\nلا توجد تصنيفات بعد. أضف أول تصنيف.')
    if notice:
        text = notice + '\n\n' + text
    return text, admin_kb.categories_list_kb(cats)


async def _render_categories(c: CallbackQuery, notice: str | None = None) -> None:
    text, markup = await _categories_payload(notice)
    await safe_edit(c, text, markup)


async def _answer_categories(m: Message, notice: str | None = None) -> None:
    text, markup = await _categories_payload(notice)
    await m.answer(text, reply_markup=markup)


async def _category_snapshot(category_id: int):
    async with db.SessionLocal() as s:
        category = await category_service.get_category(s, category_id)
        if category is None:
            return None
        count = await category_service.count_category_products(s, category_id)
    return category, count


async def _render_category_actions(c: CallbackQuery, category_id: int, notice: str | None = None) -> None:
    snap = await _category_snapshot(category_id)
    if snap is None:
        return await _render_categories(c, 'التصنيف غير موجود.')
    category, count = snap
    status = 'ظاهر للمشترين ✅' if category.is_active else 'مخفي 🙈'
    text = (notice + '\n\n' if notice else '') + f'📂 {category.name}\n\nالحالة: {status}\nالمنتجات: {count}'
    await safe_edit(c, text, admin_kb.category_actions_kb(category))


@router.callback_query(F.data == 'admin:cats')
async def admin_categories(c: CallbackQuery, state: FSMContext | None = None):
    if not await ensure_owner(c):
        return
    if state is not None:
        await state.clear()
    await _render_categories(c)
    await c.answer()


@router.callback_query(F.data == 'admin:catadd')
async def category_add_start(c: CallbackQuery, state: FSMContext | None = None):
    if not await ensure_owner(c):
        return
    if state is None:
        return await c.answer('طلب غير صالح', show_alert=True)
    await state.set_state(CategoryForm.add)
    await safe_edit(c, '➕ اكتب اسم التصنيف الجديد (من 2 إلى 100 حرف):', admin_kb.category_cancel_kb())
    await c.answer()


@router.message(CategoryForm.add)
async def category_add_name(m: Message, state: FSMContext):
    try:
        await category_service.create_category(db.SessionLocal, m.from_user.id, m.text)
    except ServiceError as e:  # validation / duplicate: keep the state so the owner can retry
        return await m.answer('⚠️ ' + e.message)
    await state.clear()
    await _answer_categories(m, '✅ تمت إضافة التصنيف.')


@router.callback_query(F.data.startswith('admin:catren:'))
async def category_rename_start(c: CallbackQuery, state: FSMContext | None = None):
    if not await ensure_owner(c):
        return
    cid = parse_id(c.data)
    snap = await _category_snapshot(cid) if cid is not None else None
    if snap is None:
        return await c.answer('التصنيف غير موجود', show_alert=True)
    if state is None:
        return await c.answer('طلب غير صالح', show_alert=True)
    category, _ = snap
    await state.set_state(CategoryForm.rename)
    await state.update_data(category_id=category.id)
    await safe_edit(c, f'✏️ الاسم الحالي: {category.name}\n\nاكتب الاسم الجديد:', admin_kb.category_cancel_kb())
    await c.answer()


@router.message(CategoryForm.rename)
async def category_rename_name(m: Message, state: FSMContext):
    data = await state.get_data()
    cid = data.get('category_id')
    if cid is None:
        await state.clear()
        return await m.answer('انتهت الجلسة. ابدأ العملية من جديد.')
    try:
        await category_service.rename_category(db.SessionLocal, m.from_user.id, cid, m.text)
    except ValidationError as e:  # keep the state so the owner can fix the typo
        return await m.answer('⚠️ ' + e.message)
    except ServiceError as e:
        await state.clear()
        await m.answer('⚠️ ' + e.message)
        return await _answer_categories(m)
    await state.clear()
    await _answer_categories(m, '✅ تم تحديث اسم التصنيف.')


@router.callback_query(F.data.startswith('admin:cat:'))
async def category_actions(c: CallbackQuery, state: FSMContext | None = None):
    if not await ensure_owner(c):
        return
    if state is not None:
        await state.clear()
    cid = parse_id(c.data)
    if cid is None:
        return await c.answer('طلب غير صالح', show_alert=True)
    if await _category_snapshot(cid) is None:
        return await c.answer('التصنيف غير موجود', show_alert=True)
    await _render_category_actions(c, cid)
    await c.answer()


@router.callback_query(F.data.startswith('admin:cattog:'))
async def category_toggle(c: CallbackQuery):
    if not await ensure_owner(c):
        return
    cid = parse_id(c.data)
    snap = await _category_snapshot(cid) if cid is not None else None
    if snap is None:
        return await c.answer('التصنيف غير موجود', show_alert=True)
    category, _ = snap
    try:
        updated = await category_service.set_category_active(
            db.SessionLocal, c.from_user.id, cid, not category.is_active)
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    await c.answer('✅ أصبح ظاهراً للمشترين' if updated.is_active else '🙈 تم إخفاء التصنيف')
    await _render_category_actions(c, cid)


@router.callback_query(F.data.startswith('admin:catdel:'))
async def category_delete_confirm(c: CallbackQuery):
    if not await ensure_owner(c):
        return
    cid = parse_id(c.data)
    snap = await _category_snapshot(cid) if cid is not None else None
    if snap is None:
        return await c.answer('التصنيف غير موجود', show_alert=True)
    category, count = snap
    warning = (f'\n\n⚠️ يوجد {count} منتج في هذا التصنيف. سيُزال عنها التصنيف ولن تُحذف.'
               if count else '')
    await safe_edit(c, f'🗑 حذف «{category.name}»؟{warning}\n\nلا يمكن التراجع.',
                    admin_kb.category_delete_kb(cid))
    await c.answer()


@router.callback_query(F.data.startswith('admin:catdelok:'))
async def category_delete_do(c: CallbackQuery):
    if not await ensure_owner(c):
        return
    cid = parse_id(c.data)
    if cid is None:
        return await c.answer('طلب غير صالح', show_alert=True)
    try:
        await category_service.delete_category(db.SessionLocal, c.from_user.id, cid)
    except ServiceError as e:
        await c.answer(e.message, show_alert=True)
        return await _render_categories(c)
    await c.answer('🗑 تم حذف التصنيف')
    await _render_categories(c, '🗑 تم حذف التصنيف. المنتجات بقيت محفوظة بدون تصنيف.')
