"""Seller panel: product list, add-product wizard, edit, stock, discount, visibility, delete.

Every action goes through bot.services.products, which re-verifies ownership and validates input.
The UI never trusts callback data: ids are parsed strictly and checked server-side.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

from bot.keyboards import seller as kb
from bot.services import catalog as catalog_service
from bot.services import products as ps
from bot.services import stores as store_service
from bot.services import validation as val
from bot.services.errors import ServiceError, ValidationError
from database import db
from database.models import Category
from utils.locks import user_lock
from utils.telegram import safe_edit

router = Router()

STATUS_LABEL = {"active": "نشط ✅", "hidden": "مخفي 🙈", "draft": "مسودة 📝"}
INVALID = "طلب غير صالح"
STALE = "انتهت الجلسة. ابدأ العملية من جديد."
FIELD_PROMPTS = {
    "name": "✏️ اكتب الاسم الجديد للمنتج:",
    "description": "📝 اكتب الوصف الجديد:",
    "price": "💰 اكتب السعر الجديد (مثل 1500 أو 99.90).\nتنبيه: تعديل السعر يلغي الخصم الحالي.",
    "stock": "📦 اكتب الكمية الدقيقة الجديدة (رقم من 0 فما فوق):",
    "discount": "🎁 اكتب نسبة الخصم (رقم من 1 إلى 90):",
}


class AddProduct(StatesGroup):
    name = State()
    description = State()
    price = State()
    stock = State()
    category = State()
    photos = State()
    confirm = State()


class EditProduct(StatesGroup):
    value = State()


class EditImages(StatesGroup):
    photos = State()


# ------------------------------------------------------------------ helpers
def _args(data: str, count: int):
    """'sp:stk:12:+5' -> [12, 5]; anything malformed -> None."""
    parts = (data or "").split(":")[2:]
    if len(parts) != count:
        return None
    try:
        return [int(x) for x in parts]
    except ValueError:
        return None


def _is_callback(target) -> bool:
    return hasattr(target, "data")


async def _send(target, text: str, markup=None) -> None:
    if _is_callback(target):
        await safe_edit(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


async def _alert(target, text: str) -> None:
    if _is_callback(target):
        await target.answer(text, show_alert=True)
    else:
        await target.answer("⚠️ " + text)


def _card_text(product, category) -> str:
    discount = ""
    if product.original_price and product.original_price > product.price:
        pct = round((1 - float(product.price) / float(product.original_price)) * 100)
        discount = f"  (بدل {product.original_price} — خصم {pct}%)"
    stock_note = "  ⚠️ نفد المخزون" if product.stock == 0 else ""
    return (f"🛍️ {product.name}\n🆔 {product.public_id}  •  {STATUS_LABEL.get(product.status, product.status)}\n\n"
            f"💰 {product.price} {product.currency}{discount}\n📦 المخزون: {product.stock}{stock_note}\n"
            f"📂 التصنيف: {category.name if category else '—'}\n"
            f"🖼 الصور: {len(ps.parse_image_ids(product.image_ids_json))}\n"
            f"👁 {product.views}  •  🛒 {product.sales_count}\n\n{product.description}")


async def _build_card(user_id: int, pid: int):
    async with db.SessionLocal() as s:
        product, _store = await ps.get_my_product(s, user_id, pid)
        category = await s.get(Category, product.category_id) if product.category_id else None
    has_images = bool(ps.parse_image_ids(product.image_ids_json))
    return _card_text(product, category), kb.product_card_kb(product, has_images)


async def _show_card(target, user, pid: int, notice: str | None = None) -> None:
    text, markup = await _build_card(user.id, pid)
    await _send(target, (notice + "\n\n" if notice else "") + text, markup)


async def _show_list(target, user, store_id: int, page: int) -> None:
    async with db.SessionLocal() as s:
        store = await store_service.get_owned_store(s, store_id, user.id)
        if store is None:
            raise ps.ProductError("forbidden", "غير مصرح")
        pg = await ps.list_my_products(s, user.id, store_id, page=page)
    text = f"📦 منتجات {store.name}\nالعدد: {pg.total} — صفحة {pg.page + 1}/{pg.pages}"
    if not pg.items:
        text += "\n\nلا توجد منتجات بعد. اضغط ➕ لإضافة أول منتج."
    await _send(target, text, kb.products_list_kb(pg.items, store_id, pg.page, pg.pages))


async def _clear(state) -> None:
    if state is not None:
        await state.clear()


# ------------------------------------------------------------------ list / view
@router.callback_query(F.data.startswith("sp:list:"))
async def products_list(c: CallbackQuery, db_user, state: FSMContext | None = None):
    a = _args(c.data, 2)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    await _clear(state)
    try:
        await _show_list(c, db_user, a[0], a[1])
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    await c.answer()


@router.callback_query(F.data.startswith("sp:view:"))
async def product_view(c: CallbackQuery, db_user, state: FSMContext | None = None):
    a = _args(c.data, 1)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    await _clear(state)
    try:
        await _show_card(c, db_user, a[0])
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    await c.answer()


@router.callback_query(F.data.startswith("sp:photos:"))
async def photos_preview(c: CallbackQuery, db_user):
    a = _args(c.data, 1)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    try:
        async with db.SessionLocal() as s:
            product, _ = await ps.get_my_product(s, db_user.id, a[0])
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    images = ps.parse_image_ids(product.image_ids_json)
    if not images:
        return await c.answer("لا توجد صور لهذا المنتج", show_alert=True)
    if len(images) == 1:
        await c.message.answer_photo(images[0])
    else:
        await c.message.answer_media_group([InputMediaPhoto(media=i) for i in images[: ps.MAX_IMAGES]])
    await c.answer()


# ------------------------------------------------------------------ cancel / stale
@router.callback_query(F.data == "spx:cancel")
async def cancel_flow(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(c, "❌ تم إلغاء العملية.", kb.inline([[kb.btn("⬅️ متاجري", "seller_menu")]]))
    await c.answer()


# ------------------------------------------------------------------ add-product wizard
@router.callback_query(F.data.startswith("sp:add:"))
async def add_start(c: CallbackQuery, state: FSMContext, db_user):
    a = _args(c.data, 1)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    async with db.SessionLocal() as s:
        store = await store_service.get_owned_store(s, a[0], db_user.id)
    if store is None:
        return await c.answer("غير مصرح", show_alert=True)
    if store.status != "approved":
        return await c.answer("متجرك غير مفعّل حالياً", show_alert=True)
    await state.clear()
    await state.set_state(AddProduct.name)
    await state.update_data(store_id=store.id, images=[])
    await safe_edit(c, "➕ منتج جديد (1/6)\nاكتب اسم المنتج:\n\n(للإلغاء في أي وقت: /cancel)", kb.cancel_kb())
    await c.answer()


@router.message(AddProduct.name)
async def add_name(m: Message, state: FSMContext):
    try:
        name = ps.validate_name(m.text)
    except ServiceError as e:
        return await m.answer("⚠️ " + e.message)
    await state.update_data(name=name)
    await state.set_state(AddProduct.description)
    await m.answer("(2/6) اكتب وصف المنتج:", reply_markup=kb.cancel_kb())


@router.message(AddProduct.description)
async def add_description(m: Message, state: FSMContext):
    try:
        description = ps.validate_description(m.text)
    except ServiceError as e:
        return await m.answer("⚠️ " + e.message)
    await state.update_data(description=description)
    await state.set_state(AddProduct.price)
    await m.answer("(3/6) اكتب السعر (مثل 1500 أو 99.90):", reply_markup=kb.cancel_kb())


@router.message(AddProduct.price)
async def add_price(m: Message, state: FSMContext):
    try:
        price = val.parse_price(m.text)
    except ServiceError as e:
        return await m.answer("⚠️ " + e.message)
    await state.update_data(price=str(price))
    await state.set_state(AddProduct.stock)
    await m.answer("(4/6) اكتب الكمية المتوفرة (رقم من 0 فما فوق):", reply_markup=kb.cancel_kb())


@router.message(AddProduct.stock)
async def add_stock(m: Message, state: FSMContext):
    try:
        stock = val.parse_stock(m.text)
    except ServiceError as e:
        return await m.answer("⚠️ " + e.message)
    await state.update_data(stock=stock)
    async with db.SessionLocal() as s:
        categories = await catalog_service.list_categories(s)
    if categories:
        await state.set_state(AddProduct.category)
        markup = kb.categories_kb(categories, lambda cid: f"spa:cat:{cid}", "spa:cat:0", "spx:cancel")
        return await m.answer("(5/6) اختر التصنيف:", reply_markup=markup)
    await state.update_data(category_id=None, category_name=None)
    await state.set_state(AddProduct.photos)
    await m.answer("(6/6) أرسل صور المنتج (حتى 5)، ثم اضغط «تم». يمكنك التخطي بالضغط على «تم».",
                   reply_markup=kb.add_photos_kb())


@router.message(AddProduct.category)
async def add_category_text(m: Message):
    await m.answer("اختر التصنيف من الأزرار أعلاه.")


@router.callback_query(AddProduct.category, F.data.startswith("spa:cat:"))
async def add_category(c: CallbackQuery, state: FSMContext):
    a = _args(c.data, 1)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    category_id, category_name = None, None
    if a[0] != 0:
        async with db.SessionLocal() as s:
            category = await s.get(Category, a[0])
        if category is None or not category.is_active:
            return await c.answer("التصنيف غير متاح", show_alert=True)
        category_id, category_name = category.id, category.name
    await state.update_data(category_id=category_id, category_name=category_name)
    await state.set_state(AddProduct.photos)
    await safe_edit(c, "(6/6) أرسل صور المنتج (حتى 5)، ثم اضغط «تم». يمكنك التخطي بالضغط على «تم».",
                    kb.add_photos_kb())
    await c.answer()


async def _collect_photo(m: Message, state: FSMContext, markup) -> None:
    file_id = m.photo[-1].file_id  # largest size
    group = getattr(m, "media_group_id", None)
    async with user_lock(m.from_user.id):  # album photos arrive concurrently
        data = await state.get_data()
        images = list(data.get("images", []))
        silent = group is not None and group == data.get("last_group")
        if file_id in images:
            note = "هذه الصورة أضيفت مسبقاً."
        elif len(images) >= ps.MAX_IMAGES:
            note = f"وصلت للحد الأقصى ({ps.MAX_IMAGES} صور)."
        else:
            images.append(file_id)
            note = f"✅ تمت إضافة الصورة ({len(images)}/{ps.MAX_IMAGES}). أرسل المزيد أو اضغط «تم»."
        await state.update_data(images=images, last_group=group)
    if not silent:
        await m.answer(note, reply_markup=markup)


@router.message(AddProduct.photos, F.photo)
async def add_photo(m: Message, state: FSMContext):
    await _collect_photo(m, state, kb.add_photos_kb())


@router.message(AddProduct.photos)
async def add_photo_text(m: Message):
    await m.answer("أرسل صورة، أو اضغط «تم» للمتابعة.", reply_markup=kb.add_photos_kb())


def _summary(d: dict) -> str:
    return ("🧾 مراجعة المنتج\n\n"
            f"الاسم: {d.get('name')}\nالوصف: {str(d.get('description'))[:200]}\nالسعر: {d.get('price')}\n"
            f"الكمية: {d.get('stock')}\nالتصنيف: {d.get('category_name') or '—'}\n"
            f"الصور: {len(d.get('images', []))}\n\nاضغط «نشر» ليظهر للمشترين.")


@router.callback_query(AddProduct.photos, F.data == "spa:photodone")
async def add_photos_done(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddProduct.confirm)
    await safe_edit(c, _summary(await state.get_data()), kb.confirm_add_kb())
    await c.answer()


@router.message(AddProduct.confirm)
async def add_confirm_text(m: Message):
    await m.answer("اضغط «نشر المنتج» أو «إلغاء».", reply_markup=kb.confirm_add_kb())


@router.callback_query(AddProduct.confirm, F.data == "spa:save")
async def add_save(c: CallbackQuery, state: FSMContext, db_user):
    async with user_lock(c.from_user.id):  # double-tap => the second call sees the cleared state
        if await state.get_state() != AddProduct.confirm.state:
            return await c.answer(STALE, show_alert=True)
        d = await state.get_data()
        if not all(k in d for k in ("store_id", "name", "description", "price", "stock")):
            await state.clear()
            return await c.answer(STALE, show_alert=True)
        try:
            product = await ps.create_product(
                db.SessionLocal, db_user.id, d["store_id"], name=d["name"], description=d["description"],
                price=d["price"], stock=d["stock"], category_id=d.get("category_id"),
                image_ids=d.get("images", []))
        except ServiceError as e:
            await state.clear()
            return await c.answer(e.message, show_alert=True)
        await state.clear()
    await c.answer("✅ تم نشر المنتج")
    await _show_card(c, db_user, product.id, "✅ تم نشر المنتج")


# ------------------------------------------------------------------ edit
@router.callback_query(F.data.startswith("sp:edit:"))
async def edit_menu(c: CallbackQuery, db_user, state: FSMContext | None = None):
    a = _args(c.data, 1)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    await _clear(state)
    try:
        async with db.SessionLocal() as s:
            await ps.get_my_product(s, db_user.id, a[0])
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    await safe_edit(c, "✏️ ماذا تريد أن تعدّل؟", kb.edit_menu_kb(a[0]))
    await c.answer()


@router.callback_query(F.data.startswith("sp:ef:"))
async def edit_field_start(c: CallbackQuery, state: FSMContext, db_user):
    parts = (c.data or "").split(":")
    if len(parts) != 4 or parts[3] not in FIELD_PROMPTS or not parts[2].isdigit():
        return await c.answer(INVALID, show_alert=True)
    pid, field = int(parts[2]), parts[3]
    try:
        async with db.SessionLocal() as s:
            await ps.get_my_product(s, db_user.id, pid)
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    await state.clear()
    await state.set_state(EditProduct.value)
    await state.update_data(pid=pid, field=field)
    await safe_edit(c, FIELD_PROMPTS[field] + "\n\n(للإلغاء: /cancel)", kb.cancel_kb())
    await c.answer()


@router.message(EditProduct.value)
async def edit_field_value(m: Message, state: FSMContext, db_user):
    data = await state.get_data()
    pid, field = data.get("pid"), data.get("field")
    if pid is None or field not in FIELD_PROMPTS:
        await state.clear()
        return await m.answer(STALE)
    try:
        if field == "name":
            await ps.update_product(db.SessionLocal, db_user.id, pid, name=m.text)
        elif field == "description":
            await ps.update_product(db.SessionLocal, db_user.id, pid, description=m.text)
        elif field == "price":
            await ps.update_product(db.SessionLocal, db_user.id, pid, price=m.text)
        elif field == "stock":
            await ps.set_stock(db.SessionLocal, db_user.id, pid, m.text)
        else:
            await ps.set_discount(db.SessionLocal, db_user.id, pid, m.text)
    except ValidationError as e:  # typo: let the seller retry without losing the flow
        return await m.answer("⚠️ " + e.message)
    except ServiceError as e:
        await state.clear()
        return await m.answer("⚠️ " + e.message)
    await state.clear()
    await _show_card(m, db_user, pid, "✅ تم التحديث")


@router.callback_query(F.data.startswith("sp:ecat:"))
async def edit_category_list(c: CallbackQuery, db_user, state: FSMContext | None = None):
    a = _args(c.data, 1)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    await _clear(state)
    pid = a[0]
    try:
        async with db.SessionLocal() as s:
            await ps.get_my_product(s, db_user.id, pid)
            categories = await catalog_service.list_categories(s)
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    markup = kb.categories_kb(categories, lambda cid: f"sp:ecatset:{pid}:{cid}", f"sp:ecatset:{pid}:0",
                              f"sp:edit:{pid}")
    await safe_edit(c, "📂 اختر التصنيف:" if categories else "📂 لا توجد تصنيفات بعد (يضيفها مالك المنصة).", markup)
    await c.answer()


@router.callback_query(F.data.startswith("sp:ecatset:"))
async def edit_category_set(c: CallbackQuery, db_user):
    a = _args(c.data, 2)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    pid, cid = a
    try:
        await ps.update_product(db.SessionLocal, db_user.id, pid, category_id=None if cid == 0 else cid)
        await _show_card(c, db_user, pid, "✅ تم تحديث التصنيف")
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    await c.answer()


@router.callback_query(F.data.startswith("sp:eimg:"))
async def edit_images_start(c: CallbackQuery, state: FSMContext, db_user):
    a = _args(c.data, 1)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    try:
        async with db.SessionLocal() as s:
            await ps.get_my_product(s, db_user.id, a[0])
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    await state.clear()
    await state.set_state(EditImages.photos)
    await state.update_data(pid=a[0], images=[])
    await safe_edit(c, "🖼 أرسل الصور الجديدة (حتى 5). ستستبدل الصور الحالية. ثم اضغط «تم».\n"
                       "أو اضغط «حذف كل الصور».", kb.edit_photos_kb())
    await c.answer()


@router.message(EditImages.photos, F.photo)
async def edit_photo(m: Message, state: FSMContext):
    await _collect_photo(m, state, kb.edit_photos_kb())


@router.message(EditImages.photos)
async def edit_photo_text(m: Message):
    await m.answer("أرسل صورة، أو اضغط «تم».", reply_markup=kb.edit_photos_kb())


async def _finish_images(c: CallbackQuery, state: FSMContext, db_user, images: list, pid):
    try:
        await ps.update_product(db.SessionLocal, db_user.id, pid, image_ids=images)
    except ServiceError as e:
        await state.clear()
        return await c.answer(e.message, show_alert=True)
    await state.clear()
    await c.answer("✅ تم")
    await _show_card(c, db_user, pid, "✅ تم تحديث الصور")


@router.callback_query(EditImages.photos, F.data == "spe:imgdone")
async def edit_images_done(c: CallbackQuery, state: FSMContext, db_user):
    data = await state.get_data()
    if not data.get("images") or data.get("pid") is None:
        return await c.answer("أرسل صورة واحدة على الأقل، أو اختر «حذف كل الصور».", show_alert=True)
    await _finish_images(c, state, db_user, data["images"], data["pid"])


@router.callback_query(EditImages.photos, F.data == "spe:imgclear")
async def edit_images_clear(c: CallbackQuery, state: FSMContext, db_user):
    pid = (await state.get_data()).get("pid")
    if pid is None:
        await state.clear()
        return await c.answer(STALE, show_alert=True)
    await _finish_images(c, state, db_user, [], pid)


# ------------------------------------------------------------------ stock
@router.callback_query(F.data.startswith("sp:stkmenu:"))
async def stock_menu(c: CallbackQuery, db_user, state: FSMContext | None = None):
    a = _args(c.data, 1)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    await _clear(state)
    try:
        async with db.SessionLocal() as s:
            product, _ = await ps.get_my_product(s, db_user.id, a[0])
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    await safe_edit(c, f"📦 المخزون الحالي: {product.stock}\n{product.name}", kb.stock_kb(a[0]))
    await c.answer()


@router.callback_query(F.data.startswith("sp:stk:"))
async def stock_adjust(c: CallbackQuery, db_user):
    a = _args(c.data, 2)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    pid, delta = a
    try:
        new_stock = await ps.adjust_stock(db.SessionLocal, db_user.id, pid, delta)
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    await safe_edit(c, f"📦 المخزون الحالي: {new_stock}", kb.stock_kb(pid))
    await c.answer("✅")


# ------------------------------------------------------------------ discount
@router.callback_query(F.data.startswith("sp:disc:"))
async def discount_menu(c: CallbackQuery, db_user, state: FSMContext | None = None):
    a = _args(c.data, 1)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    await _clear(state)
    try:
        async with db.SessionLocal() as s:
            product, _ = await ps.get_my_product(s, db_user.id, a[0])
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    has_discount = bool(product.original_price and product.original_price > product.price)
    text = f"🎁 الخصم\n\nالسعر الحالي: {product.price} {product.currency}"
    if has_discount:
        text += f"\nالسعر الأصلي: {product.original_price}"
    await safe_edit(c, text + "\n\nاختر نسبة الخصم:", kb.discount_kb(a[0], has_discount))
    await c.answer()


@router.callback_query(F.data.startswith("sp:discset:"))
async def discount_set(c: CallbackQuery, db_user):
    a = _args(c.data, 2)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    try:
        await ps.set_discount(db.SessionLocal, db_user.id, a[0], a[1])
        await _show_card(c, db_user, a[0], f"✅ تم تطبيق خصم {a[1]}%")
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    await c.answer()


@router.callback_query(F.data.startswith("sp:discrm:"))
async def discount_remove(c: CallbackQuery, db_user):
    a = _args(c.data, 1)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    try:
        await ps.remove_discount(db.SessionLocal, db_user.id, a[0])
        await _show_card(c, db_user, a[0], "✅ تم إلغاء الخصم")
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    await c.answer()


# ------------------------------------------------------------------ visibility / delete
@router.callback_query(F.data.startswith("sp:vis:"))
async def visibility_toggle(c: CallbackQuery, db_user):
    a = _args(c.data, 1)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    try:
        async with db.SessionLocal() as s:
            product, _ = await ps.get_my_product(s, db_user.id, a[0])
        make_visible = product.status != ps.ACTIVE
        await ps.set_visibility(db.SessionLocal, db_user.id, a[0], make_visible)
        await _show_card(c, db_user, a[0], "👁 أصبح المنتج ظاهراً للمشترين" if make_visible else "🙈 تم إخفاء المنتج")
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    await c.answer()


@router.callback_query(F.data.startswith("sp:del:"))
async def delete_confirm(c: CallbackQuery, db_user):
    a = _args(c.data, 1)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    try:
        async with db.SessionLocal() as s:
            product, _ = await ps.get_my_product(s, db_user.id, a[0])
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    await safe_edit(c, f"🗑 هل تريد حذف «{product.name}»؟\nسيختفي من المتجر والسلات والمفضلة. "
                       "الطلبات السابقة تبقى محفوظة.", kb.delete_confirm_kb(a[0]))
    await c.answer()


@router.callback_query(F.data.startswith("sp:delok:"))
async def delete_do(c: CallbackQuery, db_user):
    a = _args(c.data, 1)
    if a is None:
        return await c.answer(INVALID, show_alert=True)
    try:
        async with db.SessionLocal() as s:
            product, _ = await ps.get_my_product(s, db_user.id, a[0])
        store_id = product.store_id
        await ps.delete_product(db.SessionLocal, db_user.id, a[0])
    except ServiceError as e:
        return await c.answer(e.message, show_alert=True)
    await c.answer("🗑 تم الحذف")
    await _show_list(c, db_user, store_id, 0)


# ------------------------------------------------------------------ stale buttons (must stay LAST)
@router.callback_query(F.data.startswith("spa:") | F.data.startswith("spe:"))
async def stale_flow(c: CallbackQuery):
    await c.answer(STALE, show_alert=True)
