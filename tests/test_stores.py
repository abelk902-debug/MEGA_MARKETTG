import asyncio

import pytest
from sqlalchemy import func, select

from bot.services import stores as ss
from bot.services.errors import StoreError
from database.models import AuditLog, Store, StoreApplication, User
from tests.helpers import make_store, make_user


async def _apply(sf, user, name="My Shop"):
    return await ss.submit_application(sf, user.id, name, "desc")


async def test_submit_validation(sf):
    u = await make_user(sf)
    with pytest.raises(StoreError) as e:
        await ss.submit_application(sf, u.id, "x", "d")
    assert e.value.code == "bad_name"
    with pytest.raises(StoreError) as e:
        await ss.submit_application(sf, u.id, "Good name", "d" * (ss.MAX_DESC + 1))
    assert e.value.code == "bad_desc"


async def test_only_one_pending_application_per_user(sf):
    u = await make_user(sf)
    await _apply(sf, u)
    with pytest.raises(StoreError) as e:
        await _apply(sf, u, "Another")
    assert e.value.code == "pending_exists"


async def test_approve_creates_store_promotes_customer_and_audits(sf):
    u = await make_user(sf, tg_id=555)
    app = await _apply(sf, u)
    d = await ss.approve_application(sf, app.id, reviewer_tg_id=8399658960)
    assert d.applicant_telegram_id == 555 and d.store_id
    async with sf() as s:
        store = await s.get(Store, d.store_id)
        assert store.status == "approved" and store.owner_id == u.id
        assert (await s.get(User, u.id)).role == "seller"
        assert (await s.get(StoreApplication, app.id)).status == "approved"
        log = (await s.execute(select(AuditLog))).scalar_one()
        assert log.action == "store_application.approve" and log.actor_id == 8399658960


async def test_double_approval_creates_only_one_store(sf):
    u = await make_user(sf)
    app = await _apply(sf, u)
    await ss.approve_application(sf, app.id, 1)
    with pytest.raises(StoreError) as e:
        await ss.approve_application(sf, app.id, 1)
    assert e.value.code == "not_pending"
    async with sf() as s:
        assert await s.scalar(select(func.count(Store.id))) == 1


async def test_concurrent_approvals_only_one_wins(sf):
    u = await make_user(sf)
    app = await _apply(sf, u)
    res = await asyncio.gather(*[ss.approve_application(sf, app.id, 1) for _ in range(3)], return_exceptions=True)
    assert sum(isinstance(r, ss.ApplicationDecision) for r in res) == 1
    assert all(isinstance(r, (ss.ApplicationDecision, StoreError)) for r in res)
    async with sf() as s:
        assert await s.scalar(select(func.count(Store.id))) == 1


async def test_approving_owner_application_does_not_demote_owner(sf):
    owner = await make_user(sf, tg_id=8399658960, role="owner")
    app = await _apply(sf, owner)
    await ss.approve_application(sf, app.id, 8399658960)
    async with sf() as s:
        assert (await s.get(User, owner.id)).role == "owner"


async def test_approving_moderator_keeps_role(sf):
    mod = await make_user(sf, role="moderator")
    app = await _apply(sf, mod)
    await ss.approve_application(sf, app.id, 1)
    async with sf() as s:
        assert (await s.get(User, mod.id)).role == "moderator"


async def test_reject_and_cannot_approve_afterwards(sf):
    u = await make_user(sf)
    app = await _apply(sf, u)
    d = await ss.reject_application(sf, app.id, 1, "no")
    assert d.store_id is None
    with pytest.raises(StoreError):
        await ss.approve_application(sf, app.id, 1)
    async with sf() as s:
        assert await s.scalar(select(func.count(Store.id))) == 0
        assert (await s.get(StoreApplication, app.id)).status == "rejected"
        assert (await s.execute(select(AuditLog.action))).scalar_one() == "store_application.reject"


async def test_store_ownership_is_checked_by_db_id_not_telegram_id(sf):
    seller = await make_user(sf, tg_id=900001, role="seller")
    attacker = await make_user(sf, tg_id=900002)
    store = await make_store(sf, seller.id)
    async with sf() as s:
        assert (await ss.get_owned_store(s, store.id, seller.id)).id == store.id
        assert await ss.get_owned_store(s, store.id, attacker.id) is None
        assert await ss.get_owned_store(s, store.id, seller.telegram_id) is None
