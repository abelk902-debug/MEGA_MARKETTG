
"""Category management (platform owner only)."""
from __future__ import annotations

import re
import secrets

from sqlalchemy import func, select, update

from bot.services.audit import log_action
from bot.services.errors import CategoryError
from bot.services.permissions import is_owner_id
from bot.services.validation import clean_text
from database.models import Category, Product


NOT_ALLOWED = "غير مصرح."


def slugify(name: str) -> str:
    slug = re.sub(r"[\W_]+", "-", name.lower(), flags=re.UNICODE).strip("-")
    return slug or "cat"


async def list_all_categories(session):
    return (
        await session.execute(
            select(Category).order_by(Category.name)
        )
    ).scalars().all()


async def get_category(session, category_id: int):
    return await session.get(Category, category_id)


async def count_category_products(session, category_id: int) -> int:
    return (
        await session.scalar(
            select(func.count(Product.id)).where(
                Product.category_id == category_id
            )
        )
        or 0
    )


async def create_category(
    session_factory,
    actor_tg_id: int,
    name,
) -> Category:
    if not is_owner_id(actor_tg_id):
        raise CategoryError("forbidden", NOT_ALLOWED)

    name = clean_text(name, "اسم التصنيف", 2, 100)

    async with session_factory() as s:
        exists = await s.scalar(
            select(func.count(Category.id)).where(
                func.lower(Category.name) == name.lower()
            )
        )

        if exists:
            raise CategoryError(
                "exists",
                "يوجد تصنيف بهذا الاسم.",
            )

        slug = slugify(name)

        if await s.scalar(
            select(func.count(Category.id)).where(
                Category.slug == slug
            )
        ):
            slug = f"{slug}-{secrets.token_hex(3)}"

        category = Category(
            name=name,
            slug=slug,
            is_active=True,
        )

        s.add(category)
        await s.flush()

        log_action(
            s,
            actor_tg_id,
            "category.create",
            "category",
            category.id,
            name,
        )

        await s.commit()
        return category


async def set_category_active(
    session_factory,
    actor_tg_id: int,
    category_id: int,
    active: bool,
) -> Category:
    if not is_owner_id(actor_tg_id):
        raise CategoryError("forbidden", NOT_ALLOWED)

    async with session_factory() as s:
        category = await s.get(Category, category_id)

        if category is None:
            raise CategoryError(
                "not_found",
                "التصنيف غير موجود.",
            )

        category.is_active = active

        log_action(
            s,
            actor_tg_id,
            "category.show" if active else "category.hide",
            "category",
            category_id,
        )

        await s.commit()
        return category


async def rename_category(
    session_factory,
    actor_tg_id: int,
    category_id: int,
    name,
) -> Category:
    if not is_owner_id(actor_tg_id):
        raise CategoryError("forbidden", NOT_ALLOWED)

    name = clean_text(name, "اسم التصنيف", 2, 100)

    async with session_factory() as s:
        category = await s.get(Category, category_id)

        if category is None:
            raise CategoryError(
                "not_found",
                "التصنيف غير موجود.",
            )

        clash = await s.scalar(
            select(func.count(Category.id)).where(
                func.lower(Category.name) == name.lower(),
                Category.id != category_id,
            )
        )

        if clash:
            raise CategoryError(
                "exists",
                "يوجد تصنيف بهذا الاسم.",
            )

        old_name = category.name
        category.name = name

        log_action(
            s,
            actor_tg_id,
            "category.rename",
            "category",
            category_id,
            f"{old_name} -> {name}",
        )

        await s.commit()
        return category


async def delete_category(
    session_factory,
    actor_tg_id: int,
    category_id: int,
) -> None:
    """
    Deletes the category and detaches products from it.
    Products are never deleted.
    """
    if not is_owner_id(actor_tg_id):
        raise CategoryError("forbidden", NOT_ALLOWED)

    async with session_factory() as s:
        category = await s.get(Category, category_id)

        if category is None:
            raise CategoryError(
                "not_found",
                "التصنيف غير موجود.",
            )

        name = category.name

        await s.execute(
            update(Product)
            .where(Product.category_id == category_id)
            .values(category_id=None)
            .execution_options(
                synchronize_session=False
            )
        )

        await s.delete(category)

        log_action(
            s,
            actor_tg_id,
            "category.delete",
            "category",
            category_id,
            name,
        )

        await s.commit()
