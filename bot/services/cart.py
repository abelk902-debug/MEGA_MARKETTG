from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from bot.services.errors import CartError
from database.models import CartItem, Favorite, Product, Store


async def get_cart(session, user_id: int):
    """[(CartItem, Product)] for the user."""
    return (await session.execute(
        select(CartItem, Product).join(Product, Product.id == CartItem.product_id)
        .where(CartItem.user_id == user_id).order_by(CartItem.id)
    )).all()


def cart_total(rows) -> Decimal:
    return sum((Decimal(str(p.price)) * i.quantity for i, p in rows), Decimal("0"))


async def add_to_cart(session_factory, user_id: int, product_id: int, _retry: bool = True) -> int:
    """Add 1 unit. Returns the new quantity. Never lets quantity exceed stock."""
    async with session_factory() as s:
        row = (await s.execute(
            select(Product, Store).join(Store, Store.id == Product.store_id).where(Product.id == product_id)
        )).first()
        if row is None or row[0].status != "active" or row[1].status != "approved":
            raise CartError("not_found", "المنتج غير متاح")
        product, store = row
        if store.owner_id == user_id:
            raise CartError("own_product", "لا يمكنك شراء منتج من متجرك")
        if product.stock < 1:
            raise CartError("out_of_stock", "نفد المخزون")
        item = (await s.execute(
            select(CartItem).where(CartItem.user_id == user_id, CartItem.product_id == product_id)
        )).scalar_one_or_none()
        try:
            if item is None:
                s.add(CartItem(user_id=user_id, product_id=product_id, quantity=1))
                await s.commit()
                return 1
            res = await s.execute(
                update(CartItem).where(CartItem.id == item.id, CartItem.quantity < product.stock)
                .values(quantity=CartItem.quantity + 1).execution_options(synchronize_session=False)
            )
            if res.rowcount != 1:
                raise CartError("stock_limit", "لا يمكن تجاوز المخزون")
            await s.commit()
            return item.quantity + 1
        except IntegrityError:  # concurrent first insert of the same product
            await s.rollback()
            if _retry:
                return await add_to_cart(session_factory, user_id, product_id, _retry=False)
            raise


async def toggle_favorite(session_factory, user_id: int, product_id: int) -> bool:
    """Returns True if added, False if removed."""
    async with session_factory() as s:
        product = (await s.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
        if product is None or product.status != "active":
            raise CartError("not_found", "المنتج غير موجود")
        fav = (await s.execute(
            select(Favorite).where(Favorite.user_id == user_id, Favorite.product_id == product_id)
        )).scalar_one_or_none()
        if fav:
            await s.delete(fav)
            await s.commit()
            return False
        s.add(Favorite(user_id=user_id, product_id=product_id))
        try:
            await s.commit()
        except IntegrityError:  # double tap: already added
            await s.rollback()
        return True


async def list_favorites(session, user_id: int):
    return (await session.execute(
        select(Product).join(Favorite, Favorite.product_id == Product.id).where(Favorite.user_id == user_id)
    )).scalars().all()
