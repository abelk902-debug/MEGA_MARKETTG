"""Cart, favorites, checkout and order history."""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from bot.keyboards.main import back_menu, btn, inline
from bot.services import cart as cart_service
from bot.services.errors import CartError, CheckoutError
from bot.services.orders import create_order_from_cart, list_user_orders
from config.settings import settings
from database import db
from utils.telegram import notify, safe_edit
from utils.text import parse_id

log = logging.getLogger(__name__)
router = Router()


class CheckoutForm(StatesGroup):
    name = State()
    phone = State()
    address = State()
    confirm = State()


def _phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 إرسال رقم هاتفي", request_contact=True)],
            [KeyboardButton(text="❌ إلغاء")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="❌ إلغاء")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _confirm_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ تأكيد الطلب")],
            [KeyboardButton(text="❌ إلغاء")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


@router.callback_query(F.data.startswith("add:"))
async def add(c: CallbackQuery, db_user):
    pid = parse_id(c.data)
    if pid is None:
        return await c.answer("طلب غير صالح", show_alert=True)
    try:
        await cart_service.add_to_cart(db.SessionLocal, db_user.id, pid)
    except CartError as e:
        return await c.answer(e.message, show_alert=True)
    await c.answer("✅ أضيفت للسلة")


@router.callback_query(F.data == "cart")
async def cart(c: CallbackQuery, db_user):
    async with db.SessionLocal() as s:
        rows = await cart_service.get_cart(s, db_user.id)

    total = cart_service.cart_total(rows)

    text = (
        "🛒 السلة\n\n"
        + (
            "\n".join(f"• {p.name} × {i.quantity}" for i, p in rows)
            if rows
            else "فارغة"
        )
        + f"\n\n💰 {total} {settings.default_currency}"
    )

    kb = [[btn("✅ إنشاء الطلب", "checkout")]] if rows else []
    kb.append([btn("⬅️ رجوع", "home")])

    await safe_edit(c, text, inline(kb))
    await c.answer()


@router.callback_query(F.data.startswith("fav:"))
async def fav(c: CallbackQuery, db_user):
    pid = parse_id(c.data)
    if pid is None:
        return await c.answer("طلب غير صالح", show_alert=True)

    try:
        added = await cart_service.toggle_favorite(
            db.SessionLocal,
            db_user.id,
            pid,
        )
    except CartError as e:
        return await c.answer(e.message, show_alert=True)

    await c.answer("❤️ تمت الإضافة" if added else "أزيلت من المفضلة")


@router.callback_query(F.data == "favorites")
async def favorites(c: CallbackQuery, db_user):
    async with db.SessionLocal() as s:
        products = await cart_service.list_favorites(s, db_user.id)

    await safe_edit(
        c,
        "❤️ المفضلة:\n"
        + ("\n".join(p.name for p in products) if products else "فارغة"),
        back_menu(),
    )
    await c.answer()


@router.callback_query(F.data == "checkout")
@router.callback_query(F.data == "checkout")
async def checkout(
    c: CallbackQuery,
    db_user,
    state: FSMContext | None = None,
):
    async with db.SessionLocal() as s:
        rows = await cart_service.get_cart(s, db_user.id)

    if not rows:
        return await c.answer("السلة فارغة", show_alert=True)

    # Compatibility with tests/older callers that don't provide FSM.
    if state is None:
        result = await create_order_from_cart(
            db.SessionLocal,
            db_user.id,
            payment_method="cod",
            recipient_name=(
                " ".join(
                    x for x in [db_user.first_name, db_user.last_name]
                    if x
                ).strip()
                or db_user.username
                or None
            ),
        )

        await c.message.edit_text(
            "✅ تم إنشاء الطلب بنجاح\n\n"
            f"📦 رقم الطلب: {result.order_no}\n"
            f"💰 المجموع: {result.total} {result.currency}\n"
            "💳 الدفع: عند الاستلام"
        )

        for n in result.notices:
            if n.seller_telegram_id:
                lines = "\n".join(
                    f"• {name} × {qty}"
                    for name, qty in n.items
                )

                await notify(
                    c.bot,
                    n.seller_telegram_id,
                    f"📥 طلب جديد في متجر {n.store_name}\n"
                    f"رقم الطلب: {result.order_no}\n"
                    f"{lines}\n"
                    f"💰 {n.subtotal} {result.currency}",
                )

        await c.answer()
        return

    await state.clear()
    await state.update_data(
        name=(
            " ".join(
                x for x in [db_user.first_name, db_user.last_name]
                if x
            ).strip()
            or db_user.username
            or ""
        )
    )

    await state.set_state(CheckoutForm.name)

    await c.message.answer(
        "🛒 إنشاء الطلب\n\n"
        "👤 اكتب اسم المستلم:",
        reply_markup=_cancel_keyboard(),
    )
    await c.answer()

@router.message(CheckoutForm.name)
async def checkout_name(message: Message, state: FSMContext):
    if message.text == "❌ إلغاء":
        await state.clear()
        return await message.answer(
            "❌ تم إلغاء إنشاء الطلب.",
            reply_markup=ReplyKeyboardRemove(),
        )

    name = (message.text or "").strip()

    if len(name) < 2:
        return await message.answer(
            "⚠️ الاسم قصير جداً. اكتب اسم المستلم.",
            reply_markup=_cancel_keyboard(),
        )

    await state.update_data(name=name)
    await state.set_state(CheckoutForm.phone)

    await message.answer(
        "📱 أرسل رقم هاتف المستلم:",
        reply_markup=_phone_keyboard(),
    )


@router.message(CheckoutForm.phone)
async def checkout_phone(message: Message, state: FSMContext):
    if message.text == "❌ إلغاء":
        await state.clear()
        return await message.answer(
            "❌ تم إلغاء إنشاء الطلب.",
            reply_markup=ReplyKeyboardRemove(),
        )

    phone = None

    if message.contact:
        if message.contact.user_id and message.contact.user_id != message.from_user.id:
            return await message.answer(
                "⚠️ أرسل رقم هاتفك أنت باستعمال الزر.",
                reply_markup=_phone_keyboard(),
            )
        phone = message.contact.phone_number
    elif message.text:
        phone = message.text.strip()

    if not phone:
        return await message.answer(
            "⚠️ أرسل رقم الهاتف أو اضغط على «📱 إرسال رقم هاتفي».",
            reply_markup=_phone_keyboard(),
        )

    await state.update_data(phone=phone)
    await state.set_state(CheckoutForm.address)

    await message.answer(
        "📍 اكتب عنوان التوصيل بالتفصيل:",
        reply_markup=_cancel_keyboard(),
    )


@router.message(CheckoutForm.address)
async def checkout_address(message: Message, state: FSMContext):
    if message.text == "❌ إلغاء":
        await state.clear()
        return await message.answer(
            "❌ تم إلغاء إنشاء الطلب.",
            reply_markup=ReplyKeyboardRemove(),
        )

    address = (message.text or "").strip()

    if len(address) < 5:
        return await message.answer(
            "⚠️ العنوان قصير جداً. اكتب عنوان التوصيل بالتفصيل.",
            reply_markup=_cancel_keyboard(),
        )

    await state.update_data(address=address)
    data = await state.get_data()

    await state.set_state(CheckoutForm.confirm)

    await message.answer(
        "📋 تأكيد بيانات الطلب\n\n"
        f"👤 الاسم: {data['name']}\n"
        f"📱 الهاتف: {data['phone']}\n"
        f"📍 العنوان: {data['address']}\n"
        "💳 الدفع: عند الاستلام\n\n"
        "هل تريد تأكيد الطلب؟",
        reply_markup=_confirm_keyboard(),
    )


@router.message(CheckoutForm.confirm)
async def checkout_confirm(
    message: Message,
    state: FSMContext,
    db_user,
):
    if message.text == "❌ إلغاء":
        await state.clear()
        return await message.answer(
            "❌ تم إلغاء إنشاء الطلب.",
            reply_markup=ReplyKeyboardRemove(),
        )

    if message.text != "✅ تأكيد الطلب":
        return await message.answer(
            "اختر «✅ تأكيد الطلب» أو «❌ إلغاء».",
            reply_markup=_confirm_keyboard(),
        )

    data = await state.get_data()

    try:
        result = await create_order_from_cart(
            db.SessionLocal,
            db_user.id,
            payment_method="cod",
            recipient_name=data["name"],
            recipient_phone=data["phone"],
            delivery_address=data["address"],
        )
    except CheckoutError as e:
        await state.clear()
        return await message.answer(
            f"⚠️ {e.message}",
            reply_markup=ReplyKeyboardRemove(),
        )

    await state.clear()

    await message.answer(
        "✅ تم إنشاء الطلب بنجاح\n\n"
        f"📦 رقم الطلب: {result.order_no}\n"
        f"💰 المجموع: {result.total} {result.currency}\n"
        "💳 الدفع: عند الاستلام",
        reply_markup=ReplyKeyboardRemove(),
    )

    for n in result.notices:
        if n.seller_telegram_id:
            lines = "\n".join(
                f"• {name} × {qty}"
                for name, qty in n.items
            )

            await notify(
                message.bot,
                n.seller_telegram_id,
                f"📥 طلب جديد في متجر {n.store_name}\n"
                f"رقم الطلب: {result.order_no}\n"
                f"{lines}\n"
                f"💰 {n.subtotal} {result.currency}\n"
                f"👤 الزبون: {data['name']}\n"
                f"📱 الهاتف: {data['phone']}\n"
                f"📍 العنوان: {data['address']}",
            )


@router.callback_query(F.data == "orders")
async def orders(c: CallbackQuery, db_user):
    async with db.SessionLocal() as s:
        rows = await list_user_orders(s, db_user.id)

    await safe_edit(
        c,
        "📦 طلباتي:\n\n"
        + (
            "\n".join(
                f"• {o.order_no} — {o.total} — {o.status}"
                for o in rows
            )
            if rows
            else "لا توجد طلبات"
        ),
        back_menu(),
    )
    await c.answer()