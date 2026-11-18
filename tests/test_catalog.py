import pytest

from bot.services import catalog as cs
from bot.services.cart import add_to_cart, toggle_favorite
from bot.services.errors import CartError
from tests.helpers import make_product, make_store, make_user


@pytest.fixture
async def shop(sf):
    seller = await make_user(sf, role="seller")
    ok = await make_store(sf, seller.id, "approved")
    bad = await make_store(sf, seller.id, "suspended")
    return sf, seller, ok, bad


async def test_only_active_products_of_approved_stores_are_visible(shop):
    sf, _, ok, bad = shop
    vis = await make_product(sf, ok.id, name="Visible")
    await make_product(sf, ok.id, name="HiddenProd", status="hidden")
    await make_product(sf, ok.id, name="DraftProd", status="draft")
    await make_product(sf, bad.id, name="SuspendedStoreProd")
    async with sf() as s:
        names = {p.name for p in await cs.list_products(s, "store")}
        assert names == {"Visible"}
        assert await cs.get_visible_product(s, vis.id) is not None
        assert {p.name for p in await cs.search_products(s, "Suspended")} == set()


async def test_search_by_name_and_by_public_id_case_insensitive(shop):
    sf, _, ok, _ = shop
    await make_product(sf, ok.id, name="Blue Shirt", public_id="PRD-ABC123")
    async with sf() as s:
        assert [p.name for p in await cs.search_products(s, "shirt")] == ["Blue Shirt"]
        assert [p.name for p in await cs.search_products(s, "prd-abc123")] == ["Blue Shirt"]
        assert await cs.search_products(s, "   ") == []


async def test_search_treats_wildcards_literally(shop):
    sf, _, ok, _ = shop
    await make_product(sf, ok.id, name="100% Cotton")
    await make_product(sf, ok.id, name="Plain Tee")
    await make_product(sf, ok.id, name="a_b item")
    await make_product(sf, ok.id, name="axb item")
    async with sf() as s:
        assert [p.name for p in await cs.search_products(s, "%")] == ["100% Cotton"]
        assert [p.name for p in await cs.search_products(s, "a_b")] == ["a_b item"]


async def test_modes_best_featured_discounts(shop):
    sf, _, ok, _ = shop
    await make_product(sf, ok.id, name="Low", sales_count=1)
    await make_product(sf, ok.id, name="Top", sales_count=9, is_featured=True)
    await make_product(sf, ok.id, name="Sale", price="5.00", original_price=10)
    async with sf() as s:
        assert (await cs.list_products(s, "best"))[0].name == "Top"
        assert [p.name for p in await cs.list_products(s, "featured")] == ["Top"]
        assert [p.name for p in await cs.list_products(s, "discounts")] == ["Sale"]


async def test_cart_rules(shop):
    sf, seller, ok, bad = shop
    buyer = await make_user(sf)
    p = await make_product(sf, ok.id, stock=2)
    assert await add_to_cart(sf, buyer.id, p.id) == 1
    assert await add_to_cart(sf, buyer.id, p.id) == 2
    with pytest.raises(CartError) as e:
        await add_to_cart(sf, buyer.id, p.id)
    assert e.value.code == "stock_limit"


@pytest.mark.parametrize("kind,code", [("own", "own_product"), ("soldout", "out_of_stock"),
                                       ("inactive", "not_found"), ("suspended_store", "not_found"),
                                       ("missing", "not_found")])
async def test_cart_rejections(shop, kind, code):
    sf, seller, ok, bad = shop
    buyer = await make_user(sf)
    p = {"own": lambda: make_product(sf, ok.id),
         "soldout": lambda: make_product(sf, ok.id, stock=0),
         "inactive": lambda: make_product(sf, ok.id, status="hidden"),
         "suspended_store": lambda: make_product(sf, bad.id),
         "missing": None}[kind]
    pid = 424242 if p is None else (await p()).id
    user_id = seller.id if kind == "own" else buyer.id
    with pytest.raises(CartError) as e:
        await add_to_cart(sf, user_id, pid)
    assert e.value.code == code


async def test_favorites_toggle_and_reject_inactive(shop):
    sf, _, ok, _ = shop
    buyer = await make_user(sf)
    p = await make_product(sf, ok.id)
    assert await toggle_favorite(sf, buyer.id, p.id) is True
    assert await toggle_favorite(sf, buyer.id, p.id) is False
    hidden = await make_product(sf, ok.id, status="hidden")
    with pytest.raises(CartError):
        await toggle_favorite(sf, buyer.id, hidden.id)
