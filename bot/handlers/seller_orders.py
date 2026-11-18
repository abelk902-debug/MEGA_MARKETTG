
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.keyboards.main import btn, inline
from bot.services.errors import OrderError
from bot.services.orders import (
    list_seller_orders,
    update_suborder_status,
)
from bot.services.permissions import is_seller
from database import db
from database.models import OrderStatus, Store, SubOrder
from utils.telegram import safe_edit

router = Router()
logger = logging.getLogger(__name__)


STATUS_LABELS = {
    "AWAITING_SELLER_APPROVAL": "بانتظار موافقة البائع",
    "ACCEPTED": "تم قبول الطلب",
    "PROCESSING": "قيد التجهيز",
    "SHIPPED": "تم الشحن",
    "COMPLETED": "مكتمل",
    "REJECTED": "مرفوض",
    "CANCELLED": "ملغى",
}

NEXT_ACTIONS = {
    OrderStatus.AWAITING_SELLER_APPROVAL.value: [
        (OrderStatus.ACCEPTED.value, "✅ قبول الطلب"),
        (OrderStatus.REJECTED.value, "❌ رفض الطلب"),
    ],
    OrderStatus.ACCEPTED.value: [
        (OrderStatus.PROCESSING.value, "📦 بدء التجهيز"),
    ],
    OrderStatus.PROCESSING.value: [
        (OrderStatus.SHIPPED.value, "🚚 تم الشحن"),
    ],
    OrderStatus.SHIPPED.value: [
        (OrderStatus.COMPLETED.value, "✅ إكمال الطلب"),
    ],
}


def _parse_id(data: str, action: str) -> int | None:
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[:2] != ["so", action]:
        return None
    try:
        value = int(parts[2])
        return value if value > 0 else None
    except ValueError:
        return None

def _orders_keyboard(orders):
    rows = []

    for order in orders:
        status = STATUS_LABELS.get(order.status, order.status)
        rows.append([
            btn(
                f"📦 #{order.order.order_no} | {status}",
                f"so:view:{order.id}",
            )
        ])

    rows.append([btn("🔙 رجوع إلى لوحة البائع", "seller_menu")])
    return inline(rows)


def _details_keyboard(suborder):
    rows = []

    for status, label in NEXT_ACTIONS.get(suborder.status, []):
        rows.append([
            btn(label, f"so:status:{suborder.id}:{status}")
        ])

    rows.append([
        btn("🔙 رجوع إلى طلباتي", "seller_orders")
    ])

    return inline(rows)


async def _get_owned_suborder(seller_id: int, suborder_id: int):
    async with db.SessionLocal() as session:
        result = await session.execute(
            select(SubOrder)
            .join(Store, Store.id == SubOrder.store_id)
            .where(
                SubOrder.id == suborder_id,
                Store.owner_id == seller_id,
            )
            .options(
                selectinload(SubOrder.order),
                selectinload(SubOrder.items),
            )
        )
        return result.scalar_one_or_none()


@router.callback_query(F.data == "seller_orders")
async def seller_orders(c: CallbackQuery, db_user):
    if not is_seller(db_user):
        return await c.answer("هذه الخدمة خاصة بالبائعين.", show_alert=True)

    async with db.SessionLocal() as session:
        orders = await list_seller_orders(session, db_user.id)

    if not orders:
        await safe_edit(
            c,
            "📦 لا توجد طلبات في متاجرك حالياً.",
            inline([
                [btn("🔙 رجوع إلى لوحة البائع", "seller_menu")]
            ]),
        )
        return await c.answer()

    await safe_edit(
        c,
        f"📦 طلبات متاجرك\n\nعدد الطلبات: {len(orders)}\n\nاختر طلباً لعرض تفاصيله:",
        _orders_keyboard(orders),
    )
    await c.answer()


@router.callback_query(F.data.startswith("so:view:"))
async def seller_order_details(c: CallbackQuery, db_user):
    if not is_seller(db_user):
        return await c.answer("غير مصرح لك.", show_alert=True)

    suborder_id = _parse_id(c.data, "view")
    if suborder_id is None:
        return await c.answer("رقم الطلب غير صالح.", show_alert=True)

    suborder = await _get_owned_suborder(db_user.id, suborder_id)

    if suborder is None:
        return await c.answer("الطلب غير موجود أو لا تملك صلاحية الوصول إليه.", show_alert=True)

    order = suborder.order
    status = STATUS_LABELS.get(suborder.status, suborder.status)

    lines = [
        f"📦 الطلب: #{order.order_no}",
        f"الحالة: {status}",
        f"المجموع: {suborder.total} {order.currency}",
        "",
        "👤 معلومات الزبون:",
        f"الاسم: {order.recipient_name or 'غير متوفر'}",
        f"الهاتف: {order.recipient_phone or 'غير متوفر'}",
        f"العنوان: {order.delivery_address or 'غير متوفر'}",
        "",
        "🛒 المنتجات:",
    ]

    for item in suborder.items:
        lines.append(
            f"• {item.product_name}\n"
            f"  الكمية: {item.quantity} | السعر: {item.unit_price}"
        )

    await safe_edit(
        c,
        "\n".join(lines),
        _details_keyboard(suborder),
    )
    await c.answer()


@router.callback_query(F.data.startswith("so:status:"))
async def seller_update_order(c: CallbackQuery, db_user):
    if not is_seller(db_user):
        return await c.answer("غير مصرح لك.", show_alert=True)

    parts = (c.data or "").split(":")
    if len(parts) != 4:
        return await c.answer("الطلب غير صالح.", show_alert=True)

    try:
        suborder_id = int(parts[2])
    except ValueError:
        return await c.answer("رقم الطلب غير صالح.", show_alert=True)

    if suborder_id <= 0:
        return await c.answer("رقم الطلب غير صالح.", show_alert=True)

    new_status = parts[3]

    try:
        result = await update_suborder_status(
            db.SessionLocal,
            db_user.id,
            suborder_id,
            new_status,
        )
    except OrderError as exc:
        return await c.answer(exc.message, show_alert=True)

    old_label = STATUS_LABELS.get(result.old_status, result.old_status)
    new_label = STATUS_LABELS.get(result.new_status, result.new_status)

    notification = (
        f"📦 تحديث طلبك #{result.order_no}\n\n"
        f"المتجر: {result.store_name}\n"
        f"الحالة السابقة: {old_label}\n"
        f"الحالة الجديدة: {new_label}"
    )

    try:
        await c.bot.send_message(
            result.buyer_telegram_id,
            notification,
        )
        notify_text = "تم إشعار المشتري."
    except TelegramAPIError:
        logger.exception(
            "Could not notify buyer for order %s",
            result.order_no,
        )
        notify_text = "تعذر إرسال إشعار المشتري."

    await c.answer("تم تحديث حالة الطلب بنجاح.")

    suborder = await _get_owned_suborder(db_user.id, suborder_id)

    if suborder is not None:
        order = suborder.order
        lines = [
            f"📦 الطلب: #{order.order_no}",
            f"الحالة: {STATUS_LABELS.get(suborder.status, suborder.status)}",
            f"المجموع: {suborder.total} {order.currency}",
            "",
            notify_text,
        ]

        await safe_edit(
            c,
            "\n".join(lines),
            _details_keyboard(suborder),
        )
