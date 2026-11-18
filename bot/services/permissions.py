"""Server-side permission checks. Never trust callback data or client input."""
from __future__ import annotations

from config.settings import settings

SELLER_ROLES = ("seller", "owner")


def is_owner_id(telegram_id: int | None) -> bool:
    """Platform owner is decided ONLY by Telegram ID from the server settings."""
    return telegram_id is not None and telegram_id == settings.owner_id


def is_seller(user) -> bool:
    return user is not None and user.role in SELLER_ROLES


def is_store_owner(user, store) -> bool:
    """`store.owner_id` is the DB id of the user (NOT the Telegram id)."""
    return user is not None and store is not None and store.owner_id == user.id
