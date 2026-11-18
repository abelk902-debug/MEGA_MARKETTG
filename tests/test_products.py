"""Seller product management: create / edit / stock / discount / visibility / delete / listing / ownership."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from bot.services import catalog as cs
from bot.services import products as ps
from bot.services.cart import add_to_cart, toggle_favorite
from bot.services.errors import CartError, ProductError, ValidationError
from bot.services.orders import create_order_from_cart
from config.settings import settings
from database.models import AuditLog, CartItem, Favorite, OrderItem, Product
from tests.helpers import make_category, make_store, make_user, put_in_cart

VALID = dict(name="Blue Shirt", description="A nice blue shirt", price="100.00", stock=5)


@pytest.fixture
async def shop(sf):
    seller = await make_user(sf, tg_id=5001, role="seller")
    attacker = await make_user(sf, tg_id=5002, role="seller")
    return SimpleNamespace(
        sf=sf, seller=seller, attacker=attacker,
        store=await make_store(sf, seller.id, name="Mine"),
        other_store=await make_store(sf, attacker.id, name="Theirs"))


async def new(shop, **over):
    return await ps.create_product(shop.sf, shop.seller.id, shop.store.id, **{**VALID, **over})


async def fetch(sf, pid):
    async with sf() as s:
        return await s.get(Product, pid)


async def actions(sf):
    async with sf() as s:
        return [a for a in (await s.execute(select(AuditLog.action).order_by(AuditLog.id))).scalars()]


# ------------------------------------------------------------------ create
async def test_create_product_success(shop):
    cat = await make_category(shop.sf)
    p = await new(shop, name="  Blue   Shirt ", price="١٢٫٥٠", stock="7", category_id=cat.id,
                  image_ids=["AgAC1", "AgAC2", "AgAC1"])
    assert p.name == "Blue Shirt" and p.price == Decimal("12.50") and p.stock == 7
    assert p.status == "active" and p.public_id.startswith("P-") and p.currency == settings.default_currency
    assert p.category_id == cat.id and p.original_price is None
    assert ps.parse_image_ids(p.image_ids_json) == ["AgAC1", "AgAC2"]
    async with shop.sf() as s:
        assert await cs.get_visible_product(s, p.id) is not None
        log = (await s.execute(select(AuditLog))).scalar_one()
        assert log.action == "product.create" and log.actor_id == 5001 and log.target_id == str(p.id)


async def test_public_ids_are_unique(shop):
    ids = {(await new(shop)).public_id for _ in range(5)}
    assert len(ids) == 5


@pytest.mark.parametrize("over", [
    dict(name="x"), dict(name="a" * 181), dict(name="   "), dict(description=""), dict(description="ab"),
    dict(description="d" * 2001), dict(price="abc"), dict(price="0"), dict(price="-1"), dict(price="1.234"),
    dict(price="1e3"), dict(price="nan"), dict(price=100000000), dict(stock="-1"), dict(stock=1000001),
    dict(stock="x"), dict(image_ids=[f"id{i}" for i in range(6)]), dict(image_ids=[""]),
    dict(image_ids=["has space"]), dict(image_ids=[123]), dict(category_id=999), dict(category_id=True)])
async def test_create_rejects_invalid_input_and_saves_nothing(shop, over):
    with pytest.raises(ValidationError):
        await new(shop, **over)
    async with shop.sf() as s:
        assert await s.scalar(select(func.count(Product.id))) == 0
        assert await s.scalar(select(func.count(AuditLog.id))) == 0


async def test_create_rejects_inactive_category(shop):
    cat = await make_category(shop.sf, active=False)
    with pytest.raises(ValidationError) as e:
        await new(shop, category_id=cat.id)
    assert e.value.code == "bad_category"


async def test_create_permissions(shop):
    with pytest.raises(ProductError) as e:  # someone else's store
        await ps.create_product(shop.sf, shop.attacker.id, shop.store.id, **VALID)
    assert e.value.code == "forbidden"
    with pytest.raises(ProductError) as e:  # unknown store
        await ps.create_product(shop.sf, shop.seller.id, 99999, **VALID)
    assert e.value.code == "forbidden"
    customer = await make_user(shop.sf)
    with pytest.raises(ProductError) as e:  # a plain customer has no store
        await ps.create_product(shop.sf, customer.id, shop.store.id, **VALID)
    assert e.value.code == "forbidden"
    banned = await make_user(shop.sf, role="seller", banned=True)
    banned_store = await make_store(shop.sf, banned.id)
    with pytest.raises(ProductError) as e:
        await ps.create_product(shop.sf, banned.id, banned_store.id, **VALID)
    assert e.value.code == "forbidden"
    pending = await make_store(shop.sf, shop.seller.id, status="suspended")
    with pytest.raises(ProductError) as e:
        await ps.create_product(shop.sf, shop.seller.id, pending.id, **VALID)
    assert e.value.code == "store_inactive"
    async with shop.sf() as s:
        assert await s.scalar(select(func.count(Product.id))) == 0


# ------------------------------------------------------------------ update
async def test_update_fields(shop):
    cat = await make_category(shop.sf)
    p = await new(shop, image_ids=["a1"])
    u = await ps.update_product(shop.sf, shop.seller.id, p.id, name="New  name", description="New description",
                                category_id=cat.id, image_ids=["b1", "b2"])
    assert u.name == "New name" and u.description == "New description" and u.category_id == cat.id
    assert ps.parse_image_ids(u.image_ids_json) == ["b1", "b2"]
    u = await ps.update_product(shop.sf, shop.seller.id, p.id, category_id=None, image_ids=[])
    assert u.category_id is None and u.image_ids_json is None
    assert "product.update" in await actions(shop.sf)


async def test_update_is_all_or_nothing(shop):
    p = await new(shop)
    with pytest.raises(ValidationError):
        await ps.update_product(shop.sf, shop.seller.id, p.id, name="Valid new name", price="abc")
    row = await fetch(shop.sf, p.id)
    assert row.name == "Blue Shirt" and row.price == Decimal("100.00")


async def test_update_with_no_changes_is_an_error(shop):
    p = await new(shop)
    with pytest.raises(ProductError) as e:
        await ps.update_product(shop.sf, shop.seller.id, p.id)
    assert e.value.code == "nothing"


async def test_manual_price_change_ends_the_discount(shop):
    p = await new(shop)
    await ps.set_discount(shop.sf, shop.seller.id, p.id, 20)
    u = await ps.update_product(shop.sf, shop.seller.id, p.id, price="90")
    assert u.price == Decimal("90.00") and u.original_price is None


# ------------------------------------------------------------------ discounts
async def test_discount_lifecycle(shop):
    p = await new(shop, price="100.00")
    d = await ps.set_discount(shop.sf, shop.seller.id, p.id, "20")
    assert d.price == Decimal("80.00") and d.original_price == Decimal("100.00")
    async with shop.sf() as s:
        assert [x.id for x in await cs.list_products(s, "discounts")] == [p.id]
    d = await ps.set_discount(shop.sf, shop.seller.id, p.id, 50)  # always computed from the ORIGINAL price
    assert d.price == Decimal("50.00") and d.original_price == Decimal("100.00")
    d = await ps.remove_discount(shop.sf, shop.seller.id, p.id)
    assert d.price == Decimal("100.00") and d.original_price is None
    with pytest.raises(ProductError) as e:
        await ps.remove_discount(shop.sf, shop.seller.id, p.id)
    assert e.value.code == "no_discount"
    async with shop.sf() as s:
        assert await cs.list_products(s, "discounts") == []


async def test_discount_rounding(shop):
    p = await new(shop, price="33.33")
    assert (await ps.set_discount(shop.sf, shop.seller.id, p.id, 10)).price == Decimal("30.00")


@pytest.mark.parametrize("pct", ["0", "91", "-5", "abc", "10.5", None])
async def test_discount_percent_validation(shop, pct):
    p = await new(shop)
    with pytest.raises(ValidationError):
        await ps.set_discount(shop.sf, shop.seller.id, p.id, pct)
    row = await fetch(shop.sf, p.id)
    assert row.price == Decimal("100.00") and row.original_price is None


async def test_discount_too_small_for_price(shop):
    p = await new(shop, price="0.01")
    with pytest.raises(ProductError) as e:
        await ps.set_discount(shop.sf, shop.seller.id, p.id, 1)
    assert e.value.code == "discount_too_small"


# ------------------------------------------------------------------ stock
async def test_set_and_adjust_stock(shop):
    p = await new(shop, stock=5)
    assert await ps.set_stock(shop.sf, shop.seller.id, p.id, "20") == 20
    assert await ps.adjust_stock(shop.sf, shop.seller.id, p.id, "+5") == 25
    assert await ps.adjust_stock(shop.sf, shop.seller.id, p.id, -25) == 0
    assert (await fetch(shop.sf, p.id)).stock == 0
    assert {"product.stock_set", "product.stock_adjust"} <= set(await actions(shop.sf))


async def test_stock_cannot_go_below_zero_or_above_max(shop):
    p = await new(shop, stock=3)
    with pytest.raises(ProductError) as e:
        await ps.adjust_stock(shop.sf, shop.seller.id, p.id, -4)
    assert e.value.code == "stock_range"
    await ps.set_stock(shop.sf, shop.seller.id, p.id, 1_000_000)
    with pytest.raises(ProductError) as e:
        await ps.adjust_stock(shop.sf, shop.seller.id, p.id, 1)
    assert e.value.code == "stock_range"
    for bad in ("-1", "x", 1_000_001):
        with pytest.raises(ValidationError):
            await ps.set_stock(shop.sf, shop.seller.id, p.id, bad)
    assert (await fetch(shop.sf, p.id)).stock == 1_000_000


async def test_concurrent_stock_adjustments_never_go_negative(shop):
    p = await new(shop, stock=5)
    res = await asyncio.gather(*[ps.adjust_stock(shop.sf, shop.seller.id, p.id, -1) for _ in range(10)],
                               return_exceptions=True)
    assert sum(isinstance(r, int) for r in res) == 5
    assert all(isinstance(r, (int, ProductError)) for r in res), res
    assert (await fetch(shop.sf, p.id)).stock == 0


async def test_stock_zero_product_stays_listed_but_cannot_be_bought(shop):
    p = await new(shop, stock=1)
    await ps.set_stock(shop.sf, shop.seller.id, p.id, 0)
    buyer = await make_user(shop.sf)
    with pytest.raises(CartError) as e:
        await add_to_cart(shop.sf, buyer.id, p.id)
    assert e.value.code == "out_of_stock"


# ------------------------------------------------------------------ visibility / delete
async def test_hide_and_show(shop):
    p = await new(shop, name="Visible Thing")
    assert (await ps.set_visibility(shop.sf, shop.seller.id, p.id, False)).status == "hidden"
    async with shop.sf() as s:
        assert await cs.get_visible_product(s, p.id) is None
        assert await cs.search_products(s, "Visible") == []
    assert (await ps.set_visibility(shop.sf, shop.seller.id, p.id, True)).status == "active"
    async with shop.sf() as s:
        assert [x.id for x in await cs.search_products(s, "Visible")] == [p.id]


async def test_delete_is_soft_and_cleans_cart_and_favorites(shop):
    p = await new(shop)
    buyer = await make_user(shop.sf)
    await add_to_cart(shop.sf, buyer.id, p.id)
    await toggle_favorite(shop.sf, buyer.id, p.id)
    await ps.delete_product(shop.sf, shop.seller.id, p.id)
    row = await fetch(shop.sf, p.id)
    assert row is not None and row.status == "deleted"
    async with shop.sf() as s:
        assert await s.scalar(select(func.count(CartItem.id))) == 0
        assert await s.scalar(select(func.count(Favorite.id))) == 0
        assert await cs.get_visible_product(s, p.id) is None
    with pytest.raises(CartError):
        await add_to_cart(shop.sf, buyer.id, p.id)
    for op in (lambda: ps.delete_product(shop.sf, shop.seller.id, p.id),
               lambda: ps.update_product(shop.sf, shop.seller.id, p.id, name="Zombie"),
               lambda: ps.set_visibility(shop.sf, shop.seller.id, p.id, True)):
        with pytest.raises(ProductError) as e:
            await op()
        assert e.value.code == "forbidden"
    assert "product.delete" in await actions(shop.sf)


async def test_delete_keeps_order_history(shop):
    p = await new(shop, stock=5)
    buyer = await make_user(shop.sf)
    await put_in_cart(shop.sf, buyer.id, p.id, 2)
    await create_order_from_cart(shop.sf, buyer.id)
    await ps.delete_product(shop.sf, shop.seller.id, p.id)
    async with shop.sf() as s:
        item = (await s.execute(select(OrderItem))).scalar_one()
        assert item.product_id == p.id and item.quantity == 2


async def test_suspended_store_cannot_edit_but_can_still_delete(shop):
    p = await new(shop)
    async with shop.sf() as s:
        (await s.get(type(shop.store), shop.store.id)).status = "suspended"
        await s.commit()
    with pytest.raises(ProductError) as e:
        await ps.update_product(shop.sf, shop.seller.id, p.id, name="Changed name")
    assert e.value.code == "store_inactive"
    await ps.delete_product(shop.sf, shop.seller.id, p.id)
    assert (await fetch(shop.sf, p.id)).status == "deleted"


# ------------------------------------------------------------------ ownership (the security core)
OPS = {
    "update": lambda sf, u, pid: ps.update_product(sf, u, pid, name="Hacked name"),
    "set_stock": lambda sf, u, pid: ps.set_stock(sf, u, pid, 999),
    "adjust_stock": lambda sf, u, pid: ps.adjust_stock(sf, u, pid, 5),
    "set_discount": lambda sf, u, pid: ps.set_discount(sf, u, pid, 50),
    "remove_discount": lambda sf, u, pid: ps.remove_discount(sf, u, pid),
    "hide": lambda sf, u, pid: ps.set_visibility(sf, u, pid, False),
    "delete": lambda sf, u, pid: ps.delete_product(sf, u, pid),
}


@pytest.mark.parametrize("op", list(OPS))
@pytest.mark.parametrize("who", ["attacker", "customer"])
async def test_nobody_else_can_modify_a_sellers_product(shop, op, who):
    p = await new(shop)
    await ps.set_discount(shop.sf, shop.seller.id, p.id, 20)  # so remove_discount has a target
    before = await fetch(shop.sf, p.id)
    intruder = shop.attacker if who == "attacker" else await make_user(shop.sf)
    logs_before = await actions(shop.sf)
    with pytest.raises(ProductError) as e:
        await OPS[op](shop.sf, intruder.id, p.id)
    assert e.value.code == "forbidden"
    after = await fetch(shop.sf, p.id)
    assert (after.name, after.stock, after.status, after.price, after.original_price) == \
           (before.name, before.stock, before.status, before.price, before.original_price)
    assert await actions(shop.sf) == logs_before  # failed attempts are not recorded as changes


async def test_unknown_product_id_is_forbidden_not_a_crash(shop):
    for op in OPS.values():
        with pytest.raises(ProductError) as e:
            await op(shop.sf, shop.seller.id, 424242)
        assert e.value.code == "forbidden"


async def test_get_my_product_checks_ownership(shop):
    p = await new(shop)
    async with shop.sf() as s:
        product, store = await ps.get_my_product(s, shop.seller.id, p.id)
        assert product.id == p.id and store.id == shop.store.id
        with pytest.raises(ProductError):
            await ps.get_my_product(s, shop.attacker.id, p.id)


# ------------------------------------------------------------------ listings
async def test_seller_listing_pagination_and_scope(shop):
    made = [await new(shop, name=f"Item {i:02d}") for i in range(10)]
    await ps.set_visibility(shop.sf, shop.seller.id, made[0].id, False)  # hidden products stay listed
    await ps.delete_product(shop.sf, shop.seller.id, made[1].id)  # deleted ones do not
    await ps.create_product(shop.sf, shop.attacker.id, shop.other_store.id, **VALID)
    async with shop.sf() as s:
        page0 = await ps.list_my_products(s, shop.seller.id, shop.store.id, page=0, page_size=5)
        assert page0.total == 9 and page0.pages == 2 and len(page0.items) == 5
        assert page0.items[0].id == made[-1].id  # newest first
        page1 = await ps.list_my_products(s, shop.seller.id, shop.store.id, page=1, page_size=5)
        assert len(page1.items) == 4
        last = await ps.list_my_products(s, shop.seller.id, shop.store.id, page=99, page_size=5)
        assert last.page == 1 and len(last.items) == 4
        negative = await ps.list_my_products(s, shop.seller.id, shop.store.id, page=-3)
        assert negative.page == 0
        ids = {p.id for p in page0.items + page1.items}
        assert made[0].id in ids and made[1].id not in ids
        assert all(p.store_id == shop.store.id for p in page0.items + page1.items)


async def test_seller_cannot_list_another_sellers_store(shop):
    async with shop.sf() as s:
        with pytest.raises(ProductError) as e:
            await ps.list_my_products(s, shop.attacker.id, shop.store.id)
        assert e.value.code == "forbidden"


async def test_each_stores_public_page_is_independent(shop):
    mine = await new(shop, name="Mine A")
    hidden = await new(shop, name="Mine hidden")
    await ps.set_visibility(shop.sf, shop.seller.id, hidden.id, False)
    theirs = await ps.create_product(shop.sf, shop.attacker.id, shop.other_store.id, **{**VALID, "name": "Theirs B"})
    async with shop.sf() as s:
        a = await cs.list_store_products_public(s, shop.store.id)
        b = await cs.list_store_products_public(s, shop.other_store.id)
        assert [p.id for p in a.items] == [mine.id] and a.total == 1
        assert [p.id for p in b.items] == [theirs.id]
        assert (await cs.get_public_store(s, shop.store.id)).name == "Mine"
        suspended = await make_store(shop.sf, shop.seller.id, status="suspended")
        assert await cs.get_public_store(s, suspended.id) is None
        assert (await cs.list_store_products_public(s, suspended.id)).total == 0


def test_parse_image_ids_is_robust():
    assert ps.parse_image_ids(None) == [] and ps.parse_image_ids("") == []
    assert ps.parse_image_ids("not json") == [] and ps.parse_image_ids('{"a": 1}') == []
    assert ps.parse_image_ids('["a", 5, "", "b"]') == ["a", "b"]
