from __future__ import annotations

import math

from sqlalchemy import func, or_, select, update

from bot.services.products import ProductPage
from database.models import Category, Product, Store
from utils.text import escape_like

MODES = ("store", "best", "newest", "featured", "discounts")


def visible_products():
    """Only active products of approved stores are visible to buyers."""
    return (
        select(Product)
        .join(Store, Store.id == Product.store_id)
        .where(Product.status == "active", Store.status == "approved")
    )


async def list_products(session, mode: str = "store", category_id: int | None = None, limit: int = 20):
    q = visible_products()
    if category_id is not None:
        q = q.where(Product.category_id == category_id)
    if mode == "best":
        q = q.order_by(Product.sales_count.desc(), Product.id.desc())
    elif mode == "featured":
        q = q.where(Product.is_featured.is_(True)).order_by(Product.id.desc())
    elif mode == "discounts":
        q = q.where(Product.original_price.is_not(None), Product.original_price > Product.price).order_by(Product.id.desc())
    else:  # store / newest
        q = q.order_by(Product.created_at.desc(), Product.id.desc())
    return (await session.execute(q.limit(limit))).scalars().all()


async def search_products(session, query: str, limit: int = 20):
    term = (query or "").strip()[:100]
    if not term:
        return []
    pattern = f"%{escape_like(term)}%"
    q = visible_products().where(
        or_(Product.name.ilike(pattern, escape="\\"), func.upper(Product.public_id) == term.upper())
    ).order_by(Product.id.desc())
    return (await session.execute(q.limit(limit))).scalars().all()


async def get_visible_product(session, product_id: int):
    return (await session.execute(visible_products().where(Product.id == product_id))).scalar_one_or_none()


async def record_view(session, product_id: int) -> None:
    await session.execute(
        update(Product).where(Product.id == product_id).values(views=Product.views + 1)
        .execution_options(synchronize_session=False)
    )


async def list_categories(session):
    return (await session.execute(select(Category).where(Category.is_active.is_(True)).order_by(Category.name))).scalars().all()


async def get_public_store(session, store_id: int):
    """Only approved stores are publicly visible."""
    return (await session.execute(select(Store).where(Store.id == store_id, Store.status == "approved"))).scalar_one_or_none()


async def list_store_products_public(session, store_id: int, page: int = 0, page_size: int = 8) -> ProductPage:
    """What a BUYER sees of one seller: active products of an approved store only."""
    size = max(1, min(page_size, 20))
    base = visible_products().where(Product.store_id == store_id)
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    pages = max(1, math.ceil(total / size))
    page = max(0, min(page, pages - 1))
    items = (await session.execute(base.order_by(Product.id.desc()).limit(size).offset(page * size))).scalars().all()
    return ProductPage(items, total, page, pages)
