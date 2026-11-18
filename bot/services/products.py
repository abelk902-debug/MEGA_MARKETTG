"""Seller product management.

Every function re-verifies ownership on the server (store.owner_id == DB user id),
validates all input, writes an audit row and is atomic. "Delete" is a soft delete
so order history (which references products) stays intact.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from bot.services.audit import log_action
from bot.services.errors import ProductError, ValidationError
from bot.services.validation import (MAX_STOCK, MIN_PRICE, clean_text, parse_percent, parse_price,
                                     parse_stock, parse_stock_delta)
from config.settings import settings
from database.models import CartItem, Category, Favorite, Product, Store, User
from utils.text import new_code

ACTIVE, HIDDEN, DRAFT, DELETED = "active", "hidden", "draft", "deleted"
MAX_IMAGES = 5
MAX_IMAGE_ID_LEN = 255
FORBIDDEN = "غير مصرح أو المنتج غير موجود"
_UNSET = object()


@dataclass(frozen=True)
class ProductPage:
    items: list
    total: int
    page: int
    pages: int


# ---------- helpers ----------
def parse_image_ids(raw) -> list[str]:
    """Never raises: bad/legacy data simply means 'no images'."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [x for x in data if isinstance(x, str) and x] if isinstance(data, list) else []


def _clean_images(image_ids) -> list[str]:
    cleaned: list[str] = []
    for file_id in image_ids or []:
        if not isinstance(file_id, str) or not 1 <= len(file_id) <= MAX_IMAGE_ID_LEN or any(c.isspace() for c in file_id):
            raise ValidationError("invalid_image", "صورة غير صالحة")
        if file_id not in cleaned:
            cleaned.append(file_id)
    if len(cleaned) > MAX_IMAGES:
        raise ValidationError("too_many_images", f"الحد الأقصى {MAX_IMAGES} صور")
    return cleaned


def _name(value) -> str:
    return clean_text(value, "اسم المنتج", 2, 180)


def _description(value) -> str:
    return clean_text(value, "الوصف", 3, 2000, multiline=True)


def validate_name(value) -> str:
    """Public helpers so the UI can validate step by step with the SAME rules as the service."""
    return _name(value)


def validate_description(value) -> str:
    return _description(value)


async def _load_actor(s, user_id: int) -> User:
    user = await s.get(User, user_id)
    if user is None or user.is_banned or not user.is_active:
        raise ProductError("forbidden", FORBIDDEN)
    return user


async def _check_category(s, category_id) -> int | None:
    if category_id is None:
        return None
    if isinstance(category_id, bool) or not isinstance(category_id, int):
        raise ValidationError("bad_category", "التصنيف غير متاح")
    category = await s.get(Category, category_id)
    if category is None or not category.is_active:
        raise ValidationError("bad_category", "التصنيف غير متاح")
    return category_id


async def _owned_product(s, user_id: int, product_id: int, *, require_approved: bool = True):
    """(actor, product, store) or ProductError. A product of another seller == not found."""
    actor = await _load_actor(s, user_id)
    row = (await s.execute(
        select(Product, Store).join(Store, Store.id == Product.store_id)
        .where(Product.id == product_id, Store.owner_id == user_id)
    )).first()
    if row is None or row[0].status == DELETED:
        raise ProductError("forbidden", FORBIDDEN)
    if require_approved and row[1].status != "approved":
        raise ProductError("store_inactive", "متجرك غير مفعّل حالياً")
    return actor, row[0], row[1]


# ---------- create ----------
async def create_product(session_factory, user_id: int, store_id: int, *, name, description, price, stock,
                         category_id=None, image_ids=()) -> Product:
    name, description = _name(name), _description(description)
    price, stock = parse_price(price), parse_stock(stock)
    images = _clean_images(image_ids)
    async with session_factory() as s:
        actor_tg = (await _load_actor(s, user_id)).telegram_id
        store = (await s.execute(select(Store).where(Store.id == store_id, Store.owner_id == user_id))).scalar_one_or_none()
        if store is None:
            raise ProductError("forbidden", "غير مصرح")
        if store.status != "approved":
            raise ProductError("store_inactive", "متجرك غير مفعّل حالياً")
        category = await _check_category(s, category_id)
        for _ in range(5):  # public_id collision is astronomically unlikely; retry anyway
            product = Product(public_id=new_code("P-", 6), store_id=store_id, category_id=category, name=name,
                              description=description, price=price, currency=settings.default_currency,
                              stock=stock, status=ACTIVE, image_ids_json=json.dumps(images) if images else None)
            s.add(product)
            try:
                await s.flush()
                break
            except IntegrityError:
                await s.rollback()
        else:
            raise ProductError("busy", "تعذر إنشاء المنتج الآن، حاول مرة أخرى")
        log_action(s, actor_tg, "product.create", "product", product.id, product.public_id)
        await s.commit()
        return product


# ---------- update ----------
async def update_product(session_factory, user_id: int, product_id: int, *, name=_UNSET, description=_UNSET,
                         price=_UNSET, category_id=_UNSET, image_ids=_UNSET) -> Product:
    async with session_factory() as s:
        actor, product, _ = await _owned_product(s, user_id, product_id)
        changed: list[str] = []
        if name is not _UNSET:
            product.name = _name(name); changed.append("name")
        if description is not _UNSET:
            product.description = _description(description); changed.append("description")
        if price is not _UNSET:
            product.price = parse_price(price)
            product.original_price = None  # a manual price change ends any discount
            changed.append("price")
        if category_id is not _UNSET:
            product.category_id = await _check_category(s, category_id); changed.append("category")
        if image_ids is not _UNSET:
            images = _clean_images(image_ids)
            product.image_ids_json = json.dumps(images) if images else None; changed.append("images")
        if not changed:
            raise ProductError("nothing", "لا يوجد تغيير")
        log_action(s, actor.telegram_id, "product.update", "product", product_id, ",".join(changed))
        await s.commit()
        return product


# ---------- stock (atomic) ----------
async def set_stock(session_factory, user_id: int, product_id: int, value) -> int:
    quantity = parse_stock(value)
    async with session_factory() as s:
        actor, _, _ = await _owned_product(s, user_id, product_id)
        await s.execute(update(Product).where(Product.id == product_id).values(stock=quantity)
                        .execution_options(synchronize_session=False))
        log_action(s, actor.telegram_id, "product.stock_set", "product", product_id, str(quantity))
        await s.commit()
        return quantity


async def adjust_stock(session_factory, user_id: int, product_id: int, delta) -> int:
    """Relative change done in ONE guarded UPDATE: concurrent sales/edits can never push stock below 0."""
    amount = parse_stock_delta(delta)
    async with session_factory() as s:
        actor, _, _ = await _owned_product(s, user_id, product_id)
        res = await s.execute(
            update(Product)
            .where(Product.id == product_id, Product.stock + amount >= 0, Product.stock + amount <= MAX_STOCK)
            .values(stock=Product.stock + amount).execution_options(synchronize_session=False))
        if res.rowcount != 1:
            raise ProductError("stock_range", "الكمية خارج الحدود المسموحة (لا يمكن أن تقل عن 0)")
        new_stock = await s.scalar(select(Product.stock).where(Product.id == product_id))
        log_action(s, actor.telegram_id, "product.stock_adjust", "product", product_id, f"{amount:+d}")
        await s.commit()
        return new_stock


# ---------- discounts ----------
async def set_discount(session_factory, user_id: int, product_id: int, percent) -> Product:
    pct = parse_percent(percent)
    async with session_factory() as s:
        actor, product, _ = await _owned_product(s, user_id, product_id)
        has_discount = product.original_price is not None and product.original_price > product.price
        base = product.original_price if has_discount else product.price
        new_price = (base * (Decimal(100) - pct) / Decimal(100)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        if new_price < MIN_PRICE or new_price >= base:
            raise ProductError("discount_too_small", "نسبة الخصم صغيرة جداً لهذا السعر")
        product.original_price, product.price = base, new_price
        log_action(s, actor.telegram_id, "product.discount_set", "product", product_id, f"{pct}%")
        await s.commit()
        return product


async def remove_discount(session_factory, user_id: int, product_id: int) -> Product:
    async with session_factory() as s:
        actor, product, _ = await _owned_product(s, user_id, product_id)
        if product.original_price is None or product.original_price <= product.price:
            raise ProductError("no_discount", "لا يوجد خصم على هذا المنتج")
        product.price, product.original_price = product.original_price, None
        log_action(s, actor.telegram_id, "product.discount_remove", "product", product_id)
        await s.commit()
        return product


# ---------- visibility / delete ----------
async def set_visibility(session_factory, user_id: int, product_id: int, visible: bool) -> Product:
    async with session_factory() as s:
        actor, product, _ = await _owned_product(s, user_id, product_id)
        product.status = ACTIVE if visible else HIDDEN
        log_action(s, actor.telegram_id, "product.show" if visible else "product.hide", "product", product_id)
        await s.commit()
        return product


async def delete_product(session_factory, user_id: int, product_id: int) -> None:
    """Soft delete: hidden everywhere, removed from carts/favorites, order history untouched."""
    async with session_factory() as s:
        actor, product, _ = await _owned_product(s, user_id, product_id, require_approved=False)
        product.status, product.is_featured = DELETED, False
        await s.execute(delete(CartItem).where(CartItem.product_id == product_id).execution_options(synchronize_session=False))
        await s.execute(delete(Favorite).where(Favorite.product_id == product_id).execution_options(synchronize_session=False))
        log_action(s, actor.telegram_id, "product.delete", "product", product_id, product.public_id)
        await s.commit()


# ---------- seller-side reads ----------
async def get_my_product(session, user_id: int, product_id: int):
    """(Product, Store) owned by the user (any status except deleted) or ProductError."""
    row = (await session.execute(
        select(Product, Store).join(Store, Store.id == Product.store_id)
        .where(Product.id == product_id, Store.owner_id == user_id))).first()
    if row is None or row[0].status == DELETED:
        raise ProductError("forbidden", FORBIDDEN)
    return row[0], row[1]


async def list_my_products(session, user_id: int, store_id: int, *, page: int = 0, page_size: int = 8) -> ProductPage:
    store = (await session.execute(select(Store).where(Store.id == store_id, Store.owner_id == user_id))).scalar_one_or_none()
    if store is None:
        raise ProductError("forbidden", "غير مصرح")
    size = max(1, min(page_size, 20))
    cond = (Product.store_id == store_id, Product.status != DELETED)
    total = await session.scalar(select(func.count(Product.id)).where(*cond)) or 0
    pages = max(1, math.ceil(total / size))
    page = max(0, min(page, pages - 1))
    items = (await session.execute(select(Product).where(*cond).order_by(Product.id.desc())
                                   .limit(size).offset(page * size))).scalars().all()
    return ProductPage(items, total, page, pages)
