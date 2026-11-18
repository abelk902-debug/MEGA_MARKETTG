"""Seller panel (Phase 2B): real aiogram FSM + fake Telegram objects, real database."""
import asyncio
import inspect
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import func, select

from bot.handlers import admin, cart, catalog, menu, seller, start
from bot.handlers import seller_products as sph
from bot.keyboards import seller as kb
from bot.services import catalog as cs
from bot.services import products as ps
from database.models import Product
from tests.helpers import callback_is_handled, fake_tg_user, make_category, make_cb, make_store, make_user

FORBIDDEN = ps.FORBIDDEN
VALID = dict(name="Blue Shirt", description="A nice blue shirt", price="100.00", stock=5)


# ------------------------------------------------------------------ helpers
@pytest.fixture
async def world(patched_db):
    sf = patched_db
    seller_u = await make_user(sf, tg_id=6001, role="seller")
    attacker = await make_user(sf, tg_id=6002, role="seller")
    return SimpleNamespace(
        sf=sf, seller=seller_u, attacker=attacker,
        store=await make_store(sf, seller_u.id, name="Mine"),
        other=await make_store(sf, attacker.id, name="Theirs"))


def state_for(user):
    return FSMContext(storage=MemoryStorage(),
                      key=StorageKey(bot_id=1, chat_id=user.telegram_id, user_id=user.telegram_id))


def cb(user, data):
    return make_cb(user.telegram_id, data)


def msg(user, text=None, photo=None, group=None):
    photos = [SimpleNamespace(file_id=photo + "_s"), SimpleNamespace(file_id=photo)] if photo else None
    return SimpleNamespace(from_user=fake_tg_user(user.telegram_id), text=text, photo=photos,
                           media_group_id=group, answer=AsyncMock())


def shown(c):
    return c.message.edit_text.call_args.args[0]


def markup_of(c):
    return c.message.edit_text.call_args.kwargs["reply_markup"]


def callbacks(markup):
    return {b.callback_data for row in markup.inline_keyboard for b in row}


def alerts(c):
    return [call.args[0] for call in c.answer.call_args_list if call.kwargs.get("show_alert")]


async def product(w, **over):
    return await ps.create_product(w.sf, w.seller.id, w.store.id, **{**VALID, **over})


async def row(sf, pid):
    async with sf() as s:
        return await s.get(Product, pid)


async def call(handler, c, user, st):
    params = inspect.signature(handler).parameters
    kwargs = {}
    if "db_user" in params:
        kwargs["db_user"] = user
    if "state" in params:
        kwargs["state"] = st
    return await handler(c, **kwargs)


# ------------------------------------------------------------------ store panel
async def test_store_panel_has_working_buttons(world):
    w = world
    c = cb(w.seller, f"mystore:{w.store.id}")
    await seller.mystore(c, db_user=w.seller)
    assert {f"sp:list:{w.store.id}:0", f"sp:add:{w.store.id}"} <= callbacks(markup_of(c))


async def test_navigation_clears_half_finished_flows(world):
    w, st = world, None
    st = state_for(w.seller)
    await st.set_state(sph.AddProduct.price)
    await seller.mystore(cb(w.seller, f"mystore:{w.store.id}"), db_user=w.seller, state=st)
    assert await st.get_state() is None
    await st.set_state(sph.EditProduct.value)
    await menu.home(cb(w.seller, "home"), db_user=w.seller, state=st)
    assert await st.get_state() is None
    await st.set_state(sph.EditProduct.value)
    await seller.seller(cb(w.seller, "seller_menu"), db_user=w.seller, state=st)
    assert await st.get_state() is None


async def test_cancel_command_clears_any_flow(world):
    st = state_for(world.seller)
    await st.set_state(sph.AddProduct.name)
    m = msg(world.seller)
    await start.cancel_command(m, st)
    assert await st.get_state() is None and "إلغاء" in m.answer.call_args.args[0]
    m = msg(world.seller)
    await start.cancel_command(m, st)
    assert "لا توجد" in m.answer.call_args.args[0]


# ------------------------------------------------------------------ add wizard
async def test_full_add_wizard_creates_the_product(world):
    w = world
    cat = await make_category(w.sf, "Clothes")
    st = state_for(w.seller)
    await sph.add_start(cb(w.seller, f"sp:add:{w.store.id}"), st, db_user=w.seller)
    assert await st.get_state() == sph.AddProduct.name.state
    await sph.add_name(msg(w.seller, "  Blue   Shirt "), st)
    assert await st.get_state() == sph.AddProduct.description.state
    await sph.add_description(msg(w.seller, "Nice cotton shirt"), st)
    await sph.add_price(msg(w.seller, "١٢٥٠٫٥٠"), st)
    await sph.add_stock(msg(w.seller, "8"), st)
    assert await st.get_state() == sph.AddProduct.category.state
    await sph.add_category(cb(w.seller, f"spa:cat:{cat.id}"), st)
    assert await st.get_state() == sph.AddProduct.photos.state
    for photo in ("A", "B", "A"):  # duplicate ignored
        await sph.add_photo(msg(w.seller, photo=photo), st)
    assert (await st.get_data())["images"] == ["A", "B"]
    done = cb(w.seller, "spa:photodone")
    await sph.add_photos_done(done, st)
    assert await st.get_state() == sph.AddProduct.confirm.state
    assert "Blue Shirt" in shown(done) and "Clothes" in shown(done) and callbacks(markup_of(done)) == {"spa:save", "spx:cancel"}
    save = cb(w.seller, "spa:save")
    await sph.add_save(save, st, db_user=w.seller)
    assert await st.get_state() is None
    async with w.sf() as s:
        p = (await s.execute(select(Product))).scalar_one()
    assert (p.name, p.price, p.stock, p.category_id, p.store_id) == ("Blue Shirt", Decimal("1250.50"), 8, cat.id, w.store.id)
    assert ps.parse_image_ids(p.image_ids_json) == ["A", "B"] and p.status == "active"
    assert "تم نشر المنتج" in shown(save)


@pytest.mark.parametrize("state_name,handler_name,bad", [
    ("name", "add_name", "x"), ("name", "add_name", None), ("description", "add_description", "ab"),
    ("price", "add_price", "abc"), ("price", "add_price", "-5"), ("price", "add_price", "1e3"),
    ("stock", "add_stock", "-1"), ("stock", "add_stock", "1.5"), ("stock", "add_stock", "9999999")])
async def test_invalid_input_keeps_the_step_and_explains(world, state_name, handler_name, bad):
    st = state_for(world.seller)
    await st.set_state(getattr(sph.AddProduct, state_name))
    m = msg(world.seller, bad)
    await getattr(sph, handler_name)(m, st)
    assert await st.get_state() == getattr(sph.AddProduct, state_name).state
    assert m.answer.call_args.args[0].startswith("⚠️")


async def test_without_categories_the_category_step_is_skipped(world):
    st = state_for(world.seller)
    await st.set_state(sph.AddProduct.stock)
    await sph.add_stock(msg(world.seller, "3"), st)
    assert await st.get_state() == sph.AddProduct.photos.state
    assert (await st.get_data())["category_id"] is None


async def test_add_start_is_denied_for_foreign_or_inactive_stores(world):
    w = world
    st = state_for(w.attacker)
    c = cb(w.attacker, f"sp:add:{w.store.id}")  # someone else's store
    await sph.add_start(c, st, db_user=w.attacker)
    assert "غير مصرح" in alerts(c) and await st.get_state() is None
    async with w.sf() as s:
        (await s.get(type(w.store), w.store.id)).status = "suspended"
        await s.commit()
    st = state_for(w.seller)
    c = cb(w.seller, f"sp:add:{w.store.id}")
    await sph.add_start(c, st, db_user=w.seller)
    assert "متجرك غير مفعّل حالياً" in alerts(c) and await st.get_state() is None


async def test_photo_limit_is_enforced(world):
    st = state_for(world.seller)
    await st.set_state(sph.AddProduct.photos)
    last = None
    for i in range(7):
        last = msg(world.seller, photo=f"P{i}")
        await sph.add_photo(last, st)
    assert len((await st.get_data())["images"]) == ps.MAX_IMAGES
    assert "الحد الأقصى" in last.answer.call_args.args[0]


async def test_album_replies_only_once(world):
    st = state_for(world.seller)
    await st.set_state(sph.AddProduct.photos)
    msgs = [msg(world.seller, photo=f"G{i}", group="album1") for i in range(3)]
    for m in msgs:
        await sph.add_photo(m, st)
    assert len((await st.get_data())["images"]) == 3
    msgs[0].answer.assert_awaited_once()
    msgs[1].answer.assert_not_called()
    msgs[2].answer.assert_not_called()


async def test_non_photo_text_during_photo_step_is_guided(world):
    m = msg(world.seller, "hello")
    await sph.add_photo_text(m)
    assert "أرسل صورة" in m.answer.call_args.args[0]


async def test_double_tap_on_publish_creates_one_product(world):
    w = world
    st = state_for(w.seller)
    await st.set_state(sph.AddProduct.confirm)
    await st.update_data(store_id=w.store.id, name="Once", description="Only once", price="10.00", stock=3, images=[])
    c1, c2 = cb(w.seller, "spa:save"), cb(w.seller, "spa:save")
    await asyncio.gather(sph.add_save(c1, st, db_user=w.seller), sph.add_save(c2, st, db_user=w.seller))
    async with w.sf() as s:
        assert await s.scalar(select(func.count(Product.id))) == 1
    assert sph.STALE in alerts(c1) + alerts(c2)


async def test_save_with_missing_data_is_rejected(world):
    st = state_for(world.seller)
    await st.set_state(sph.AddProduct.confirm)
    c = cb(world.seller, "spa:save")
    await sph.add_save(c, st, db_user=world.seller)
    assert sph.STALE in alerts(c) and await st.get_state() is None


async def test_stale_wizard_buttons_get_an_answer_not_a_hang(world):
    c = cb(world.seller, "spa:save")
    await sph.stale_flow(c)
    assert alerts(c) == [sph.STALE]


async def test_cancel_button_clears_state(world):
    st = state_for(world.seller)
    await st.set_state(sph.AddProduct.price)
    await sph.cancel_flow(cb(world.seller, "spx:cancel"), st)
    assert await st.get_state() is None


# ------------------------------------------------------------------ list / view
async def test_product_list_pagination_and_buttons(world):
    w = world
    made = [await product(w, name=f"Item {i:02d}") for i in range(10)]
    c = cb(w.seller, f"sp:list:{w.store.id}:0")
    await sph.products_list(c, db_user=w.seller)
    assert "صفحة 1/2" in shown(c)
    cbs = callbacks(markup_of(c))
    assert f"sp:view:{made[-1].id}" in cbs and f"sp:list:{w.store.id}:1" in cbs and f"sp:add:{w.store.id}" in cbs
    c = cb(w.seller, f"sp:list:{w.store.id}:1")
    await sph.products_list(c, db_user=w.seller)
    assert "صفحة 2/2" in shown(c) and f"sp:list:{w.store.id}:0" in callbacks(markup_of(c))


async def test_empty_store_invites_first_product(world):
    c = cb(world.seller, f"sp:list:{world.store.id}:0")
    await sph.products_list(c, db_user=world.seller)
    assert "لا توجد منتجات" in shown(c)


@pytest.mark.parametrize("data", ["sp:list:abc:0", "sp:list:1", "sp:list:1:2:3", "sp:view:x", "sp:view:"])
async def test_malformed_callbacks_are_rejected(world, data):
    c = cb(world.seller, data)
    handler = sph.products_list if data.startswith("sp:list") else sph.product_view
    await handler(c, db_user=world.seller)
    assert alerts(c) == [sph.INVALID]
    c.message.edit_text.assert_not_called()


async def test_each_seller_sees_only_their_own_store_list(world):
    w = world
    await product(w, name="Mine only")
    c = cb(w.attacker, f"sp:list:{w.store.id}:0")  # attacker asks for the seller's store
    await sph.products_list(c, db_user=w.attacker)
    assert alerts(c) == ["غير مصرح"]
    c.message.edit_text.assert_not_called()
    c = cb(w.attacker, f"sp:list:{w.other.id}:0")
    await sph.products_list(c, db_user=w.attacker)
    assert "Mine only" not in shown(c)


async def test_product_card_shows_real_data(world):
    w = world
    p = await product(w, price="100.00", stock=0)
    await ps.set_discount(w.sf, w.seller.id, p.id, 20)
    c = cb(w.seller, f"sp:view:{p.id}")
    await sph.product_view(c, db_user=w.seller)
    text = shown(c)
    assert "Blue Shirt" in text and "80.00" in text and "خصم 20%" in text and "نفد المخزون" in text
    assert {f"sp:edit:{p.id}", f"sp:stkmenu:{p.id}", f"sp:disc:{p.id}", f"sp:vis:{p.id}", f"sp:del:{p.id}"} <= callbacks(markup_of(c))


# ------------------------------------------------------------------ edit
@pytest.mark.parametrize("field,text,expected", [
    ("name", "New Name", {"name": "New Name"}),
    ("description", "A brand new description", {"description": "A brand new description"}),
    ("price", "150", {"price": Decimal("150.00")}),
    ("stock", "42", {"stock": 42}),
    ("discount", "20", {"price": Decimal("80.00"), "original_price": Decimal("100.00")})])
async def test_edit_each_field_through_the_ui(world, field, text, expected):
    w = world
    p = await product(w)
    st = state_for(w.seller)
    await sph.edit_field_start(cb(w.seller, f"sp:ef:{p.id}:{field}"), st, db_user=w.seller)
    assert await st.get_state() == sph.EditProduct.value.state
    m = msg(w.seller, text)
    await sph.edit_field_value(m, st, db_user=w.seller)
    assert await st.get_state() is None and "تم التحديث" in m.answer.call_args.args[0]
    after = await row(w.sf, p.id)
    for attr, value in expected.items():
        assert getattr(after, attr) == value


async def test_edit_typo_lets_the_seller_retry(world):
    w = world
    p = await product(w)
    st = state_for(w.seller)
    await sph.edit_field_start(cb(w.seller, f"sp:ef:{p.id}:price"), st, db_user=w.seller)
    bad = msg(w.seller, "abc")
    await sph.edit_field_value(bad, st, db_user=w.seller)
    assert bad.answer.call_args.args[0].startswith("⚠️") and await st.get_state() == sph.EditProduct.value.state
    assert (await row(w.sf, p.id)).price == Decimal("100.00")
    await sph.edit_field_value(msg(w.seller, "٢٥٠"), st, db_user=w.seller)
    assert (await row(w.sf, p.id)).price == Decimal("250.00")


@pytest.mark.parametrize("data", ["sp:ef:1:hacker", "sp:ef:abc:name", "sp:ef:1", "sp:ef:1:name:x"])
async def test_edit_start_rejects_bad_callbacks(world, data):
    st = state_for(world.seller)
    c = cb(world.seller, data)
    await sph.edit_field_start(c, st, db_user=world.seller)
    assert alerts(c) == [sph.INVALID] and await st.get_state() is None


async def test_edit_value_with_lost_state_is_safe(world):
    st = state_for(world.seller)
    await st.set_state(sph.EditProduct.value)  # no pid/field stored
    m = msg(world.seller, "whatever")
    await sph.edit_field_value(m, st, db_user=world.seller)
    assert m.answer.call_args.args[0] == sph.STALE and await st.get_state() is None


async def test_category_edit_set_clear_and_reject_inactive(world):
    w = world
    p = await product(w)
    cat = await make_category(w.sf, "Shoes")
    dead = await make_category(w.sf, "Hidden", active=False)
    c = cb(w.seller, f"sp:ecat:{p.id}")
    await sph.edit_category_list(c, db_user=w.seller)
    assert {f"sp:ecatset:{p.id}:{cat.id}", f"sp:ecatset:{p.id}:0"} <= callbacks(markup_of(c))
    await sph.edit_category_set(cb(w.seller, f"sp:ecatset:{p.id}:{cat.id}"), db_user=w.seller)
    assert (await row(w.sf, p.id)).category_id == cat.id
    bad = cb(w.seller, f"sp:ecatset:{p.id}:{dead.id}")
    await sph.edit_category_set(bad, db_user=w.seller)
    assert "التصنيف غير متاح" in alerts(bad) and (await row(w.sf, p.id)).category_id == cat.id
    await sph.edit_category_set(cb(w.seller, f"sp:ecatset:{p.id}:0"), db_user=w.seller)
    assert (await row(w.sf, p.id)).category_id is None


async def test_image_replace_and_clear(world):
    w = world
    p = await product(w, image_ids=["old"])
    st = state_for(w.seller)
    await sph.edit_images_start(cb(w.seller, f"sp:eimg:{p.id}"), st, db_user=w.seller)
    assert await st.get_state() == sph.EditImages.photos.state
    empty = cb(w.seller, "spe:imgdone")
    await sph.edit_images_done(empty, st, db_user=w.seller)  # nothing sent yet
    assert len(alerts(empty)) == 1 and ps.parse_image_ids((await row(w.sf, p.id)).image_ids_json) == ["old"]
    for photo in ("n1", "n2"):
        await sph.edit_photo(msg(w.seller, photo=photo), st)
    await sph.edit_images_done(cb(w.seller, "spe:imgdone"), st, db_user=w.seller)
    assert ps.parse_image_ids((await row(w.sf, p.id)).image_ids_json) == ["n1", "n2"] and await st.get_state() is None
    await sph.edit_images_start(cb(w.seller, f"sp:eimg:{p.id}"), st, db_user=w.seller)
    await sph.edit_images_clear(cb(w.seller, "spe:imgclear"), st, db_user=w.seller)
    assert (await row(w.sf, p.id)).image_ids_json is None


async def test_photo_preview(world):
    w = world
    none, one, two = await product(w), await product(w, image_ids=["X"]), await product(w, image_ids=["X", "Y"])
    c = cb(w.seller, f"sp:photos:{none.id}")
    await sph.photos_preview(c, db_user=w.seller)
    assert alerts(c)
    c = cb(w.seller, f"sp:photos:{one.id}")
    c.message.answer_photo = AsyncMock()
    await sph.photos_preview(c, db_user=w.seller)
    c.message.answer_photo.assert_awaited_once_with("X")
    c = cb(w.seller, f"sp:photos:{two.id}")
    c.message.answer_media_group = AsyncMock()
    await sph.photos_preview(c, db_user=w.seller)
    assert len(c.message.answer_media_group.call_args.args[0]) == 2


# ------------------------------------------------------------------ stock / discount / visibility / delete
async def test_stock_buttons(world):
    w = world
    p = await product(w, stock=5)
    c = cb(w.seller, f"sp:stkmenu:{p.id}")
    await sph.stock_menu(c, db_user=w.seller)
    assert "5" in shown(c) and f"sp:stk:{p.id}:+5" in callbacks(markup_of(c))
    c = cb(w.seller, f"sp:stk:{p.id}:+5")
    await sph.stock_adjust(c, db_user=w.seller)
    assert "10" in shown(c) and (await row(w.sf, p.id)).stock == 10
    await sph.stock_adjust(cb(w.seller, f"sp:stk:{p.id}:-1"), db_user=w.seller)
    assert (await row(w.sf, p.id)).stock == 9
    bad = cb(w.seller, f"sp:stk:{p.id}:-10")
    await sph.stock_adjust(bad, db_user=w.seller)
    assert len(alerts(bad)) == 1 and (await row(w.sf, p.id)).stock == 9


async def test_discount_buttons(world):
    w = world
    p = await product(w, price="100.00")
    c = cb(w.seller, f"sp:disc:{p.id}")
    await sph.discount_menu(c, db_user=w.seller)
    assert f"sp:discrm:{p.id}" not in callbacks(markup_of(c))  # nothing to remove yet
    c = cb(w.seller, f"sp:discset:{p.id}:20")
    await sph.discount_set(c, db_user=w.seller)
    assert "خصم 20%" in shown(c) and (await row(w.sf, p.id)).price == Decimal("80.00")
    c = cb(w.seller, f"sp:disc:{p.id}")
    await sph.discount_menu(c, db_user=w.seller)
    assert f"sp:discrm:{p.id}" in callbacks(markup_of(c))
    await sph.discount_remove(cb(w.seller, f"sp:discrm:{p.id}"), db_user=w.seller)
    assert (await row(w.sf, p.id)).price == Decimal("100.00")
    again = cb(w.seller, f"sp:discrm:{p.id}")
    await sph.discount_remove(again, db_user=w.seller)
    assert "لا يوجد خصم على هذا المنتج" in alerts(again)


async def test_visibility_toggle_affects_buyers(world):
    w = world
    p = await product(w)
    await sph.visibility_toggle(cb(w.seller, f"sp:vis:{p.id}"), db_user=w.seller)
    assert (await row(w.sf, p.id)).status == "hidden"
    async with w.sf() as s:
        assert await cs.get_visible_product(s, p.id) is None
    await sph.visibility_toggle(cb(w.seller, f"sp:vis:{p.id}"), db_user=w.seller)
    assert (await row(w.sf, p.id)).status == "active"
    async with w.sf() as s:
        assert await cs.get_visible_product(s, p.id) is not None


async def test_delete_needs_confirmation(world):
    w = world
    p = await product(w)
    c = cb(w.seller, f"sp:del:{p.id}")
    await sph.delete_confirm(c, db_user=w.seller)
    assert "Blue Shirt" in shown(c) and (await row(w.sf, p.id)).status == "active"
    assert {f"sp:delok:{p.id}", f"sp:view:{p.id}"} <= callbacks(markup_of(c))
    c = cb(w.seller, f"sp:delok:{p.id}")
    await sph.delete_do(c, db_user=w.seller)
    assert (await row(w.sf, p.id)).status == "deleted" and "منتجات Mine" in shown(c)


# ------------------------------------------------------------------ ownership across EVERY seller callback
MATRIX = [
    ("product_view", "sp:view:{p}"), ("edit_menu", "sp:edit:{p}"), ("edit_field_start", "sp:ef:{p}:name"),
    ("edit_category_list", "sp:ecat:{p}"), ("edit_category_set", "sp:ecatset:{p}:0"),
    ("edit_images_start", "sp:eimg:{p}"), ("stock_menu", "sp:stkmenu:{p}"), ("stock_adjust", "sp:stk:{p}:+5"),
    ("discount_menu", "sp:disc:{p}"), ("discount_set", "sp:discset:{p}:20"), ("discount_remove", "sp:discrm:{p}"),
    ("visibility_toggle", "sp:vis:{p}"), ("delete_confirm", "sp:del:{p}"), ("delete_do", "sp:delok:{p}"),
    ("photos_preview", "sp:photos:{p}")]


@pytest.mark.parametrize("who", ["attacker", "customer"])
@pytest.mark.parametrize("handler_name,template", MATRIX)
async def test_no_seller_callback_works_on_someone_elses_product(world, handler_name, template, who):
    w = world
    p = await product(w, image_ids=["img"])
    await ps.set_discount(w.sf, w.seller.id, p.id, 20)
    before = await row(w.sf, p.id)
    intruder = w.attacker if who == "attacker" else await make_user(w.sf)
    st = state_for(intruder)
    c = cb(intruder, template.format(p=p.id))
    await call(getattr(sph, handler_name), c, intruder, st)
    assert alerts(c) and alerts(c)[0] in (FORBIDDEN, "غير مصرح")
    c.message.edit_text.assert_not_called()
    assert await st.get_state() is None
    after = await row(w.sf, p.id)
    assert (after.name, after.stock, after.status, after.price, after.original_price, after.category_id,
            after.image_ids_json) == (before.name, before.stock, before.status, before.price,
                                      before.original_price, before.category_id, before.image_ids_json)


# ------------------------------------------------------------------ wiring (no dead buttons)
def test_every_seller_panel_button_has_a_real_handler():
    p = SimpleNamespace(id=5, status="active", name="n", price=1, store_id=1)
    markups = [kb.store_panel_kb(1), kb.products_list_kb([p], 1, 1, 3), kb.product_card_kb(p, True),
               kb.edit_menu_kb(5), kb.stock_kb(5), kb.discount_kb(5, True), kb.cancel_kb(), kb.add_photos_kb(),
               kb.edit_photos_kb(), kb.confirm_add_kb(), kb.delete_confirm_kb(5),
               kb.categories_kb([SimpleNamespace(id=2, name="c")], lambda i: f"spa:cat:{i}", "spa:cat:0", "spx:cancel"),
               kb.categories_kb([SimpleNamespace(id=2, name="c")], lambda i: f"sp:ecatset:5:{i}", "sp:ecatset:5:0", "sp:edit:5")]
    routers = [m.router for m in (start, admin, menu, catalog, cart, seller, sph)]
    for markup in markups:
        for data in callbacks(markup):
            assert callback_is_handled(routers, data), f"dead button: {data}"


def test_stale_fallback_is_registered_last_and_every_wizard_state_has_a_message_handler():
    assert sph.router.callback_query.handlers[-1].callback.__name__ == "stale_flow"
    found = set()
    for h in sph.router.message.handlers:
        for f in h.filters or []:
            obj = f.callback
            if isinstance(obj, State):
                found.add(obj.state)
            for s in getattr(obj, "states", ()) or ():
                found.add(s.state if isinstance(s, State) else str(s))
    if not found:
        pytest.skip("could not introspect state filters on this aiogram version")
    expected = {s.state for s in (sph.AddProduct.name, sph.AddProduct.description, sph.AddProduct.price,
                                  sph.AddProduct.stock, sph.AddProduct.category, sph.AddProduct.photos,
                                  sph.AddProduct.confirm, sph.EditProduct.value, sph.EditImages.photos)}
    assert expected <= found, expected - found
