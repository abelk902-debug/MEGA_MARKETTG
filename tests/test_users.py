import asyncio

from sqlalchemy import func, select

from bot.services.users import get_or_create_user
from database.models import User
from tests.helpers import fake_tg_user, make_user


async def test_creates_customer(sf):
    async with sf() as s:
        u = await get_or_create_user(s, fake_tg_user(111, "bob"))
        await s.commit()
    assert u.role == "customer" and u.username == "bob"


async def test_owner_telegram_id_gets_owner_role(sf):
    async with sf() as s:
        u = await get_or_create_user(s, fake_tg_user(8399658960))
        await s.commit()
    assert u.role == "owner"


async def test_existing_owner_user_with_wrong_role_is_repaired(sf):
    await make_user(sf, tg_id=8399658960, role="customer")
    async with sf() as s:
        u = await get_or_create_user(s, fake_tg_user(8399658960))
        await s.commit()
    assert u.role == "owner"


async def test_non_owner_role_is_kept_and_profile_updated(sf):
    await make_user(sf, tg_id=222, role="seller")
    async with sf() as s:
        u = await get_or_create_user(s, fake_tg_user(222, "new_name"))
        await s.commit()
    assert u.role == "seller" and u.username == "new_name"


async def test_concurrent_first_updates_create_one_user(sf):
    async def one():
        async with sf() as s:
            u = await get_or_create_user(s, fake_tg_user(333))
            await s.commit()
            return u.id
    ids = await asyncio.gather(one(), one(), one())
    assert len(set(ids)) == 1
    async with sf() as s:
        assert await s.scalar(select(func.count(User.id)).where(User.telegram_id == 333)) == 1
