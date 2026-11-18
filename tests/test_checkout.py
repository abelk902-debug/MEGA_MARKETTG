import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from bot.services.errors import CheckoutError
from bot.services.orders import CheckoutResult, create_order_from_cart
from database.models import (CartItem, Order, OrderItem, OrderStatusHistory, Product, SubOrder)
from tests.helpers import make_product, make_store, make_user, put_in_cart


async def _count(sf, model):
    async with sf() as s:
        return await s.scalar(select(func.count(model.id)))


async def _stock(sf, pid):
    async with sf() as s:
        return (await s.get(Product, pid)).stock


@pytest.fixture
async def world(sf):
    seller1 = await make_user(sf, tg_id=7001, role="seller")
    seller2 = await make_user(sf, tg_id=7002, role="seller")
    buyer = await make_user(sf, tg_id=7003)
    st1 = await make_store(sf, seller1.id, name="S1")
    st2 = await make_store(sf, seller2.id, name="S2")
    return sf, buyer, seller1, seller2, st1, st2


async def test_success_single_store(world):
    sf, buyer, s1, _, st1, _ = world
    p = await make_product(sf, st1.id, price="10.50", stock=5)
    await put_in_cart(sf, buyer.id, p.id, 2)
    r = await create_order_from_cart(sf, buyer.id, recipient_name="Ali", delivery_address="Oran")
    assert isinstance(r, CheckoutResult)
    assert r.total == Decimal("21.00") and r.order_no.startswith("MM-")
    assert await _stock(sf, p.id) == 3
    assert await _count(sf, CartItem) == 0
    async with sf() as s:
        o = (await s.execute(select(Order))).scalar_one()
        assert o.status == "AWAITING_SELLER_APPROVAL" and o.payment_method == "cod"
        assert o.recipient_name == "Ali" and o.delivery_address == "Oran"
        subs = (await s.execute(select(SubOrder))).scalars().all()
        assert len(subs) == 1 and subs[0].total == Decimal("21.00")
        hist = (await s.execute(select(OrderStatusHistory).order_by(OrderStatusHistory.id))).scalars().all()
        assert [(h.old_status, h.new_status) for h in hist] == [
            (None, "NEW"), ("NEW", "AWAITING_SELLER_APPROVAL")]
        assert (await s.get(Product, p.id)).sales_count == 2
    assert r.notices[0].seller_telegram_id == 7001 and r.notices[0].items == [(p.name, 2)]


async def test_multi_vendor_split(world):
    sf, buyer, _, _, st1, st2 = world
    a = await make_product(sf, st1.id, price="10.00", stock=5)
    b = await make_product(sf, st1.id, price="2.50", stock=5)
    c = await make_product(sf, st2.id, price="100.00", stock=5)
    await put_in_cart(sf, buyer.id, a.id, 1)
    await put_in_cart(sf, buyer.id, b.id, 2)
    await put_in_cart(sf, buyer.id, c.id, 3)
    r = await create_order_from_cart(sf, buyer.id)
    assert r.total == Decimal("315.00")
    async with sf() as s:
        subs = {x.store_id: x for x in (await s.execute(select(SubOrder))).scalars().all()}
        assert subs[st1.id].total == Decimal("15.00") and subs[st2.id].total == Decimal("300.00")
        assert await s.scalar(select(func.count(OrderItem.id))) == 3
        assert await s.scalar(select(func.count(Order.id))) == 1
    assert {n.seller_telegram_id for n in r.notices} == {7001, 7002}


async def test_insufficient_stock_rolls_back_everything(world):
    sf, buyer, _, _, st1, st2 = world
    ok = await make_product(sf, st1.id, stock=5)
    short = await make_product(sf, st2.id, stock=1)
    await put_in_cart(sf, buyer.id, ok.id, 2)
    await put_in_cart(sf, buyer.id, short.id, 3)
    with pytest.raises(CheckoutError) as e:
        await create_order_from_cart(sf, buyer.id)
    assert e.value.code == "stock"
    assert await _count(sf, Order) == 0 and await _count(sf, SubOrder) == 0 and await _count(sf, OrderItem) == 0
    assert await _stock(sf, ok.id) == 5 and await _stock(sf, short.id) == 1
    assert await _count(sf, CartItem) == 2  # cart intact so the user can fix it


async def test_empty_cart_and_second_checkout(world):
    sf, buyer, _, _, st1, _ = world
    with pytest.raises(CheckoutError) as e:
        await create_order_from_cart(sf, buyer.id)
    assert e.value.code == "empty"
    p = await make_product(sf, st1.id)
    await put_in_cart(sf, buyer.id, p.id)
    await create_order_from_cart(sf, buyer.id)
    with pytest.raises(CheckoutError):
        await create_order_from_cart(sf, buyer.id)
    assert await _count(sf, Order) == 1


@pytest.mark.parametrize("case,code", [("hidden", "unavailable"), ("suspended", "unavailable"),
                                       ("own", "own_product"), ("banned", "forbidden"),
                                       ("currency", "mixed_currency")])
async def test_blocked_cases_leave_no_trace(world, case, code):
    sf, buyer, s1, _, st1, st2 = world
    susp = await make_store(sf, s1.id, status="suspended")
    p = await make_product(sf, st1.id, stock=5)
    who = buyer
    if case == "hidden":
        p = await make_product(sf, st1.id, status="hidden")
    elif case == "suspended":
        p = await make_product(sf, susp.id)
    elif case == "own":
        who = s1
    elif case == "banned":
        who = await make_user(sf, banned=True)
    await put_in_cart(sf, who.id, p.id)
    if case == "currency":
        other = await make_product(sf, st2.id, currency="USD")
        await put_in_cart(sf, who.id, other.id)
    with pytest.raises(CheckoutError) as e:
        await create_order_from_cart(sf, who.id)
    assert e.value.code == code
    assert await _count(sf, Order) == 0
    assert await _stock(sf, p.id) == p.stock


async def test_prices_come_from_server_at_checkout_time(world):
    sf, buyer, _, _, st1, _ = world
    p = await make_product(sf, st1.id, price="10.00", stock=5)
    await put_in_cart(sf, buyer.id, p.id, 1)
    async with sf() as s:  # seller changes the price after the item is in the cart
        (await s.get(Product, p.id)).price = Decimal("12.00")
        await s.commit()
    r = await create_order_from_cart(sf, buyer.id)
    assert r.total == Decimal("12.00")
    async with sf() as s:
        assert (await s.execute(select(OrderItem.unit_price))).scalar_one() == Decimal("12.00")


async def test_no_overselling_with_concurrent_buyers(world):
    sf, _, _, _, st1, _ = world
    p = await make_product(sf, st1.id, stock=1)
    buyers = [await make_user(sf) for _ in range(5)]
    for b in buyers:
        await put_in_cart(sf, b.id, p.id, 1)
    res = await asyncio.gather(*[create_order_from_cart(sf, b.id) for b in buyers], return_exceptions=True)
    ok = [r for r in res if isinstance(r, CheckoutResult)]
    errs = [r for r in res if not isinstance(r, CheckoutResult)]
    assert len(ok) == 1
    assert all(isinstance(e, CheckoutError) for e in errs), errs
    assert await _stock(sf, p.id) == 0
    assert await _count(sf, Order) == 1


@pytest.mark.parametrize("use_lock", [True, False])
async def test_double_tap_creates_only_one_order(world, use_lock):
    """use_lock=False proves the DATABASE alone prevents duplicates."""
    sf, buyer, _, _, st1, _ = world
    p = await make_product(sf, st1.id, stock=10)
    await put_in_cart(sf, buyer.id, p.id, 1)
    res = await asyncio.gather(*[create_order_from_cart(sf, buyer.id, use_lock=use_lock) for _ in range(4)],
                               return_exceptions=True)
    assert sum(isinstance(r, CheckoutResult) for r in res) == 1
    assert all(isinstance(r, (CheckoutResult, CheckoutError)) for r in res), res
    assert await _count(sf, Order) == 1
    assert await _stock(sf, p.id) == 9
