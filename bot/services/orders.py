"""Checkout: turns a cart into one order + one sub-order per store.

Safety model (SQLite, single writer):
1. The very first statement is a write on the user's cart rows. This takes the
   database write lock, so any concurrent checkout waits and then finds an
   EMPTY cart (-> no duplicate order).
2. Everything else is read/validated INSIDE that locked transaction (prices,
   stock, store status), so nothing can change underneath us.
3. Stock is decremented with `UPDATE ... WHERE stock >= qty` and the
   rowcount is verified (and the DB has CHECK stock >= 0) -> no overselling.
4. Any failure rolls back everything: no order, no stock change, cart intact.
A per-user asyncio lock avoids even attempting concurrent checkouts in one process.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import selectinload

from bot.services.errors import CheckoutError, OrderError
from database.models import (CartItem, Order, OrderItem, OrderStatus, OrderStatusHistory, Product,
                             Store, SubOrder, User)
from utils.text import new_code

_locks: dict[tuple[int, int], asyncio.Lock] = {}


def _lock_for(user_id: int) -> asyncio.Lock:
    key = (id(asyncio.get_running_loop()), user_id)
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock


@dataclass(frozen=True)
class SellerNotice:
    store_id: int
    store_name: str
    seller_telegram_id: int
    subtotal: Decimal
    items: list[tuple[str, int]] = field(default_factory=list)


@dataclass(frozen=True)
class CheckoutResult:
    order_id: int
    order_no: str
    total: Decimal
    currency: str
    notices: list[SellerNotice]


async def create_order_from_cart(session_factory, user_id: int, *, payment_method: str = "cod",
                                 recipient_name: str | None = None, recipient_phone: str | None = None,
                                 delivery_address: str | None = None, use_lock: bool = True) -> CheckoutResult:
    kwargs = dict(payment_method=payment_method, recipient_name=recipient_name,
                  recipient_phone=recipient_phone, delivery_address=delivery_address)
    if use_lock:
        async with _lock_for(user_id):
            return await _checkout(session_factory, user_id, **kwargs)
    return await _checkout(session_factory, user_id, **kwargs)


async def _checkout(session_factory, user_id: int, **order_fields) -> CheckoutResult:
    async with session_factory() as s:
        try:
            # (1) write-lock + claim the cart. 0 rows => empty or already checked out.
            claimed = await s.execute(
                update(CartItem).where(CartItem.user_id == user_id)
                .values(quantity=CartItem.quantity).execution_options(synchronize_session=False)
            )
            if claimed.rowcount == 0:
                raise CheckoutError("empty", "السلة فارغة أو تم إنشاء الطلب بالفعل")

            user = await s.get(User, user_id)
            if user is None or user.is_banned or not user.is_active:
                raise CheckoutError("forbidden", "حسابك موقوف")

            # (2) read everything inside the locked transaction
            rows = (await s.execute(
                select(CartItem, Product, Store)
                .join(Product, Product.id == CartItem.product_id)
                .join(Store, Store.id == Product.store_id)
                .where(CartItem.user_id == user_id).order_by(CartItem.id)
            )).all()

            currencies = {p.currency for _, p, _ in rows}
            if len(currencies) > 1:
                raise CheckoutError("mixed_currency", "لا يمكن الجمع بين منتجات بعملات مختلفة في طلب واحد")
            for item, product, store in rows:
                if product.status != "active" or store.status != "approved":
                    raise CheckoutError("unavailable", f"المنتج غير متاح حالياً: {product.name}")
                if store.owner_id == user_id:
                    raise CheckoutError("own_product", f"لا يمكنك شراء منتج من متجرك: {product.name}")
                if item.quantity < 1:
                    raise CheckoutError("bad_quantity", f"كمية غير صالحة: {product.name}")
                if product.stock < item.quantity:
                    raise CheckoutError("stock", f"المخزون غير كافٍ: {product.name}")

            # (3) cart is consumed, order is built from SERVER-SIDE prices
            await s.execute(delete(CartItem).where(CartItem.user_id == user_id)
                            .execution_options(synchronize_session=False))

            groups: dict[int, list] = {}
            for item, product, store in rows:
                groups.setdefault(store.id, []).append((item, product, store))
            total = sum((Decimal(str(p.price)) * i.quantity for i, p, _ in rows), Decimal("0"))
            order = Order(order_no=new_code(), buyer_id=user_id, total=total, currency=currencies.pop(),
                          status=OrderStatus.AWAITING_SELLER_APPROVAL.value, **order_fields)
            s.add(order)
            await s.flush()
            s.add(OrderStatusHistory(order_id=order.id, old_status=None, new_status=OrderStatus.NEW.value,
                                     changed_by=user_id, note="created"))
            s.add(OrderStatusHistory(order_id=order.id, old_status=OrderStatus.NEW.value,
                                     new_status=OrderStatus.AWAITING_SELLER_APPROVAL.value,
                                     changed_by=user_id, note="sent to sellers"))

            notices: list[SellerNotice] = []
            for store_id, group in groups.items():
                subtotal = sum((Decimal(str(p.price)) * i.quantity for i, p, _ in group), Decimal("0"))
                sub = SubOrder(order_id=order.id, store_id=store_id, total=subtotal,
                               status=OrderStatus.AWAITING_SELLER_APPROVAL.value)
                s.add(sub)
                await s.flush()
                for item, product, _store in group:
                    s.add(OrderItem(suborder_id=sub.id, product_id=product.id, product_name=product.name,
                                    unit_price=product.price, quantity=item.quantity, variant_json=item.variant_json))
                    # (4) atomic, guarded stock decrement
                    res = await s.execute(
                        update(Product)
                        .where(Product.id == product.id, Product.stock >= item.quantity, Product.status == "active")
                        .values(stock=Product.stock - item.quantity, sales_count=Product.sales_count + item.quantity)
                        .execution_options(synchronize_session=False)
                    )
                    if res.rowcount != 1:
                        raise CheckoutError("stock", f"المخزون غير كافٍ: {product.name}")
                seller = await s.get(User, group[0][2].owner_id)
                notices.append(SellerNotice(
                    store_id=store_id, store_name=group[0][2].name,
                    seller_telegram_id=seller.telegram_id if seller else 0, subtotal=subtotal,
                    items=[(p.name, i.quantity) for i, p, _ in group]))

            result = CheckoutResult(order_id=order.id, order_no=order.order_no, total=total,
                                    currency=order.currency, notices=notices)
            await s.commit()
            return result
        except CheckoutError:
            await s.rollback()
            raise
        except (OperationalError, IntegrityError) as exc:
            await s.rollback()
            raise CheckoutError("busy", "تعذر إنشاء الطلب الآن، حاول مرة أخرى") from exc


async def list_user_orders(session, user_id: int, limit: int = 20):
    return (await session.execute(
        select(Order).where(Order.buyer_id == user_id).order_by(Order.created_at.desc(), Order.id.desc()).limit(limit)
    )).scalars().all()


async def list_seller_orders(session, seller_user_id: int, limit: int = 20):
    """List only sub-orders belonging to stores owned by this seller."""
    stmt = (
        select(SubOrder)
        .join(Store, Store.id == SubOrder.store_id)
        .where(Store.owner_id == seller_user_id)
        .options(
            selectinload(SubOrder.order),
            selectinload(SubOrder.items),
        )
        .order_by(SubOrder.id.desc())
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()
@dataclass(frozen=True)
class SellerOrderUpdate:
    suborder_id: int
    order_no: str
    store_name: str
    buyer_telegram_id: int
    old_status: str
    new_status: str


SELLER_STATUS_TRANSITIONS = {
    OrderStatus.AWAITING_SELLER_APPROVAL.value: {
        OrderStatus.ACCEPTED.value,
        OrderStatus.REJECTED.value,
    },
    OrderStatus.ACCEPTED.value: {
        OrderStatus.PROCESSING.value,
    },
    OrderStatus.PROCESSING.value: {
        OrderStatus.SHIPPED.value,
    },
    OrderStatus.SHIPPED.value: {
        OrderStatus.COMPLETED.value,
    },
}


async def update_suborder_status(
    session_factory,
    seller_user_id: int,
    suborder_id: int,
    new_status: str,
    *,
    note: str | None = None,
) -> SellerOrderUpdate:
    if new_status not in {
        status
        for transitions in SELLER_STATUS_TRANSITIONS.values()
        for status in transitions
    }:
        raise OrderError("invalid_status", "حالة الطلب غير صالحة.")

    async with session_factory() as session:
        try:
            result = await session.execute(
                select(SubOrder, Store)
                .join(Store, Store.id == SubOrder.store_id)
                .where(SubOrder.id == suborder_id)
                .options(
                    selectinload(SubOrder.order).selectinload(Order.buyer)
                )
            )

            row = result.first()

            if row is None:
                raise OrderError("not_found", "الطلب غير موجود.")

            suborder, store = row

            if store.owner_id != seller_user_id:
                raise OrderError("forbidden", "ليس لديك صلاحية لتعديل هذا الطلب.")

            old_status = suborder.status

            if new_status not in SELLER_STATUS_TRANSITIONS.get(old_status, set()):
                raise OrderError("invalid_transition", "لا يمكن الانتقال إلى هذه الحالة.")

            buyer = suborder.order.buyer

            if buyer is None:
                raise OrderError("buyer_not_found", "معلومات الزبون غير متوفرة.")

            suborder.status = new_status

            session.add(
                OrderStatusHistory(
                    order_id=suborder.order_id,
                    old_status=old_status,
                    new_status=new_status,
                    changed_by=seller_user_id,
                    note=note or f"seller:{suborder.store_id}",
                )
            )

            sibling_result = await session.execute(
                select(SubOrder.status).where(
                    SubOrder.order_id == suborder.order_id
                )
            )
            sibling_statuses = sibling_result.scalars().all()

            if sibling_statuses and all(
                status == new_status for status in sibling_statuses
            ):
                suborder.order.status = new_status

            update_result = SellerOrderUpdate(
                suborder_id=suborder.id,
                order_no=suborder.order.order_no,
                store_name=store.name,
                buyer_telegram_id=buyer.telegram_id,
                old_status=old_status,
                new_status=new_status,
            )

            await session.commit()
            return update_result

        except OrderError:
            await session.rollback()
            raise

        except (OperationalError, IntegrityError) as exc:
            await session.rollback()
            raise OrderError(
                "database_error",
                "تعذر تحديث الطلب، حاول مرة أخرى.",
            ) from exc
