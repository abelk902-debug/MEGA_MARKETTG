"""Catalog browsing: lists, categories, public store page, product page, images, search."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

from bot.keyboards.catalog import product_page_kb, store_page_kb
from bot.keyboards.main import btn, inline
from bot.services import catalog as catalog_service
from bot.services import products as product_service
from database import db
from utils.telegram import safe_edit, safe_product_view
from utils.text import parse_id

router = Router()

INVALID = 'طلب غير صالح'
STORE_UNAVAILABLE = 'المتجر غير متاح'
PRODUCT_UNAVAILABLE = 'المنتج غير موجود'


class Search(StatesGroup):
    q = State()


def _shop_args(data: str | None):
    """'shop:12:1' -> (12, 1); anything malformed -> None."""
    parts = (data or '').split(':')
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        return None
    store_id, page = int(parts[1]), int(parts[2])
    return (store_id, page) if store_id > 0 and page >= 0 else None


def _product_buttons(products, with_currency=False):
    return [[btn(f'🛒 {p.name} — {p.price}' + (f' {p.currency}' if with_currency else ''), f'product:{p.id}')]
            for p in products]


@router.callback_query(F.data.in_({'store', 'best', 'newest', 'featured', 'discounts'}))
async def catalog(c: CallbackQuery):
    async with db.SessionLocal() as s:
        products = await catalog_service.list_products(s, c.data)
    rows = _product_buttons(products, with_currency=True) + [[btn('⬅️ رجوع', 'home')]]
    body = '\n'.join(p.name for p in products) if products else 'لا توجد منتجات حالياً.'
    await safe_edit(c, '🛍️ المنتجات:\n' + body, inline(rows))
    await c.answer()


@router.callback_query(F.data == 'categories')
async def categories(c: CallbackQuery):
    async with db.SessionLocal() as s:
        cats = await catalog_service.list_categories(s)
    rows = [[btn(f'📂 {x.name}', f'cat:{x.id}')] for x in cats] + [[btn('⬅️ رجوع', 'home')]]
    await safe_edit(c, '📂 التصنيفات' if cats else '📂 لا توجد تصنيفات حالياً.', inline(rows))
    await c.answer()


@router.callback_query(F.data.startswith('cat:'))
async def category(c: CallbackQuery):
    cid = parse_id(c.data)
    if cid is None:
        return await c.answer('طلب غير صالح', show_alert=True)
    async with db.SessionLocal() as s:
        products = await catalog_service.list_products(s, 'store', category_id=cid)
    rows = _product_buttons(products) + [[btn('⬅️ رجوع', 'categories')]]
    await safe_edit(c, '📂 النتائج' if products else '📂 لا توجد منتجات في هذا التصنيف.', inline(rows))
    await c.answer()


@router.callback_query(F.data.startswith('shop:'))
async def store_page(c: CallbackQuery):
    args = _shop_args(c.data)
    if args is None:
        return await c.answer(INVALID, show_alert=True)
    store_id, page = args
    async with db.SessionLocal() as s:
        store = await catalog_service.get_public_store(s, store_id)
        if store is None:
            return await c.answer(STORE_UNAVAILABLE, show_alert=True)
        pg = await catalog_service.list_store_products_public(s, store_id, page=page)
    text = (f'🏪 {store.name}\n\n{store.description or "بدون وصف"}\n\n'
            f'⭐ {store.rating}  •  🛒 {store.sales_count}  •  📦 {pg.total} منتج\n'
            f'صفحة {pg.page + 1}/{pg.pages}')
    if not pg.items:
        text += '\n\nلا توجد منتجات معروضة حالياً.'
    await safe_edit(c, text, store_page_kb(pg, store_id))
    await c.answer()


@router.callback_query(F.data.startswith('product:'))
async def product(c: CallbackQuery):
    pid = parse_id(c.data)
    if pid is None or pid <= 0:
        return await c.answer(INVALID, show_alert=True)
    async with db.SessionLocal() as s:
        p = await catalog_service.get_visible_product(s, pid)
        if p is None:
            return await c.answer(PRODUCT_UNAVAILABLE, show_alert=True)
        store = await catalog_service.get_public_store(s, p.store_id)
        if store is None:
            return await c.answer(STORE_UNAVAILABLE, show_alert=True)
        await catalog_service.record_view(s, pid)
        await s.commit()
    price = f'💰 {p.price} {p.currency}'
    if p.original_price and p.original_price > p.price:
        pct = round((1 - float(p.price) / float(p.original_price)) * 100)
        price += f'  (بدل {p.original_price} — خصم {pct}%)'
    stock = f'📦 المخزون: {p.stock}' + ('  ⚠️ نفد المخزون' if p.stock == 0 else '')
    text = f'🛍️ {p.name}\n\n{p.description}\n\n{price}\n{stock}\n🏪 {store.name}\n🆔 {p.public_id}'
    images = product_service.parse_image_ids(p.image_ids_json)
    await safe_product_view(c, text, images[:1], product_page_kb(p, len(images)))
    await c.answer()


@router.callback_query(F.data.startswith('pimg:'))
async def product_images(c: CallbackQuery):
    pid = parse_id(c.data)
    if pid is None or pid <= 0:
        return await c.answer(INVALID, show_alert=True)
    async with db.SessionLocal() as s:
        p = await catalog_service.get_visible_product(s, pid)
    if p is None:
        return await c.answer(PRODUCT_UNAVAILABLE, show_alert=True)
    images = product_service.parse_image_ids(p.image_ids_json)[:10]
    if not images:
        return await c.answer('لا توجد صور لهذا المنتج', show_alert=True)
    if len(images) == 1:
        await c.message.answer_photo(images[0])
    else:
        await c.message.answer_media_group([InputMediaPhoto(media=i) for i in images])
    await c.answer()


@router.callback_query(F.data == 'search')
async def search_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(Search.q)
    await safe_edit(c, '🔍 اكتب اسم المنتج أو Product ID:')
    await c.answer()


@router.message(Search.q)
async def search_do(m: Message, state: FSMContext):
    if not m.text or not m.text.strip():
        return await m.answer('🔍 أرسل نصاً (اسم المنتج أو Product ID).')
    await state.clear()
    async with db.SessionLocal() as s:
        products = await catalog_service.search_products(s, m.text)
    rows = _product_buttons(products) + [[btn('⬅️ رجوع', 'home')]]
    body = '\n'.join(p.name for p in products) if products else 'لا توجد نتائج'
    await m.answer('🔍 النتائج:\n' + body, reply_markup=inline(rows))
