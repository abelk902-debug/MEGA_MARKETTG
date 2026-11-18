"""Handler-level tests with fake Telegram objects (no network needed)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from bot.handlers import admin as admin_h
from bot.handlers import cart as cart_h
from bot.handlers import catalog as catalog_h
from bot.handlers import menu as menu_h
from bot.handlers import seller as seller_h
from bot.handlers import start as start_h
from bot.keyboards.main import main_menu
from bot.middlewares.user import BANNED_TEXT, UserMiddleware
from bot.services import stores as ss
from database.models import Store, User
from tests.helpers import (fake_tg_user, make_cb, make_product, make_store, make_user, put_in_cart)

OWNER = 8399658960


def alert_text(cb):
    return cb.answer.call_args.args[0] if cb.answer.call_args and cb.answer.call_args.args else None


def shown_text(cb):
    return cb.message.edit_text.call_args.args[0]


# ---------- seller / store ownership (the Telegram-ID vs DB-ID bug) ----------
async def test_mystore_allows_real_owner_and_denies_others(patched_db):
    sf = patched_db
    seller = await make_user(sf, tg_id=900001, role="seller")
    other = await make_user(sf, tg_id=900002, role="seller")
    store = await make_store(sf, seller.id, name="Mine")

    ok = make_cb(seller.telegram_id, f"mystore:{store.id}")
    await seller_h.mystore(ok, db_user=seller)
    assert "Mine" in shown_text(ok)

    bad = make_cb(other.telegram_id, f"mystore:{store.id}")
    await seller_h.mystore(bad, db_user=other)
    bad.message.edit_text.assert_not_called()
    assert alert_text(bad) == "غير مصرح"


async def test_seller_menu_requires_seller_role_and_lists_only_own_stores(patched_db):
    sf = patched_db
    cust = await make_user(sf)
    c = make_cb(cust.telegram_id, "seller_menu")
    await seller_h.seller(c, db_user=cust)
    assert alert_text(c) == "غير مصرح"

    s1, s2 = await make_user(sf, role="seller"), await make_user(sf, role="seller")
    await make_store(sf, s1.id, name="Alpha")
    await make_store(sf, s2.id, name="Beta")
    c = make_cb(s1.telegram_id, "seller_menu")
    await seller_h.seller(c, db_user=s1)
    labels = [b.text for row in c.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert any("Alpha" in x for x in labels) and not any("Beta" in x for x in labels)


async def test_malformed_callback_data_is_rejected_not_crashing(patched_db):
    u = await make_user(patched_db)
    c = make_cb(u.telegram_id, "mystore:abc")
    await seller_h.mystore(c, db_user=u)
    assert alert_text(c) == "طلب غير صالح"


# ---------- owner permissions ----------
@pytest.mark.parametrize("handler,data", [
    (admin_h.owner, "owner"), (admin_h.apps, "admin:apps"), (admin_h.app, "admin:app:1"),
    (admin_h.approve, "admin:approve:1"), (admin_h.reject, "admin:reject:1"),
    (admin_h.users, "admin:users"), (admin_h.admin_orders, "admin:orders"),
    (admin_h.admin_products, "admin:products"), (admin_h.admin_settings, "admin:settings")])
async def test_every_owner_callback_rejects_non_owner(patched_db, handler, data):
    # even a user whose DB role says 'owner' is rejected unless the Telegram ID matches
    c = make_cb(123456, data)
    await handler(c)
    assert alert_text(c) == "غير مصرح"
    c.message.edit_text.assert_not_called()


async def test_owner_dashboard_opens_for_owner(patched_db):
    c = make_cb(OWNER, "owner")
    await admin_h.owner(c)
    assert "لوحة المالك" in shown_text(c)


async def test_owner_approves_application_once_and_notifies(patched_db):
    sf = patched_db
    applicant = await make_user(sf, tg_id=4242)
    app = await ss.submit_application(sf, applicant.id, "Nice Shop", "d")

    c = make_cb(OWNER, f"admin:approve:{app.id}")
    await admin_h.approve(c)
    c.bot.send_message.assert_awaited_once()
    assert c.bot.send_message.call_args.args[0] == 4242

    c2 = make_cb(OWNER, f"admin:approve:{app.id}")
    await admin_h.approve(c2)
    assert alert_text(c2) == "الطلب غير موجود أو تمت معالجته مسبقاً"
    async with sf() as s:
        assert await s.scalar(select(func.count(Store.id))) == 1


# ---------- cart / checkout ----------
async def test_checkout_handler_success_and_seller_notification(patched_db):
    sf = patched_db
    seller = await make_user(sf, tg_id=7001, role="seller")
    buyer = await make_user(sf, tg_id=7003)
    st = await make_store(sf, seller.id, name="ShopX")
    p = await make_product(sf, st.id, price="20.00", stock=3)
    await put_in_cart(sf, buyer.id, p.id, 2)

    c = make_cb(buyer.telegram_id, "checkout")
    await cart_h.checkout(c, db_user=buyer)
    assert "تم إنشاء الطلب" in shown_text(c) and "MM-" in shown_text(c)
    assert c.bot.send_message.call_args.args[0] == 7001
    assert "ShopX" in c.bot.send_message.call_args.args[1]

    again = make_cb(buyer.telegram_id, "checkout")
    await cart_h.checkout(again, db_user=buyer)
    assert "السلة فارغة" in alert_text(again)
    again.message.edit_text.assert_not_called()


async def test_add_to_cart_handler_out_of_stock_alert(patched_db):
    sf = patched_db
    seller = await make_user(sf, role="seller")
    buyer = await make_user(sf)
    p = await make_product(sf, (await make_store(sf, seller.id)).id, stock=0)
    c = make_cb(buyer.telegram_id, f"add:{p.id}")
    await cart_h.add(c, db_user=buyer)
    assert alert_text(c) == "نفد المخزون"


async def test_cart_and_orders_handlers_render(patched_db):
    sf = patched_db
    seller = await make_user(sf, role="seller")
    buyer = await make_user(sf)
    p = await make_product(sf, (await make_store(sf, seller.id)).id, name="Widget", price="5.00")
    await put_in_cart(sf, buyer.id, p.id, 2)
    c = make_cb(buyer.telegram_id, "cart")
    await cart_h.cart(c, db_user=buyer)
    assert "Widget × 2" in shown_text(c) and "10.00" in shown_text(c)
    c = make_cb(buyer.telegram_id, "orders")
    await cart_h.orders(c, db_user=buyer)
    assert "لا توجد طلبات" in shown_text(c)


# ---------- search ----------
async def test_search_ignores_non_text_messages():
    m = SimpleNamespace(text=None, answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock())
    await catalog_h.search_do(m, state)
    m.answer.assert_awaited_once()
    state.clear.assert_not_called()


async def test_search_returns_results(patched_db):
    sf = patched_db
    seller = await make_user(sf, role="seller")
    await make_product(sf, (await make_store(sf, seller.id)).id, name="Red Lamp")
    m = SimpleNamespace(text="lamp", answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock())
    await catalog_h.search_do(m, state)
    state.clear.assert_awaited_once()
    assert "Red Lamp" in m.answer.call_args.args[0]


# ---------- middleware / start / honest texts ----------
async def test_middleware_blocks_banned_user(patched_db):
    sf = patched_db
    banned = await make_user(sf, tg_id=666, banned=True)
    event = SimpleNamespace(from_user=fake_tg_user(banned.telegram_id), answer=AsyncMock())
    handler = AsyncMock()
    await UserMiddleware()(handler, event, {})
    handler.assert_not_called()
    event.answer.assert_awaited_once_with(BANNED_TEXT)


async def test_middleware_creates_user_and_injects_db_user(patched_db):
    sf = patched_db
    event = SimpleNamespace(from_user=fake_tg_user(31337), answer=AsyncMock())
    data = {}
    handler = AsyncMock(return_value="done")
    assert await UserMiddleware()(handler, event, data) == "done"
    assert data["db_user"].telegram_id == 31337 and data["db_user"].role == "customer"
    async with sf() as s:
        assert await s.scalar(select(func.count(User.id)).where(User.telegram_id == 31337)) == 1


async def test_start_shows_owner_button_only_to_owner(patched_db):
    sf = patched_db
    owner = await make_user(sf, tg_id=OWNER, role="owner")
    cust = await make_user(sf)

    def callbacks(msg):
        kb = msg.answer.call_args.kwargs["reply_markup"].inline_keyboard
        return {b.callback_data for row in kb for b in row}

    m = SimpleNamespace(answer=AsyncMock())
    await start_h.start(m, db_user=owner)
    assert "owner" in callbacks(m)
    m = SimpleNamespace(answer=AsyncMock())
    await start_h.start(m, db_user=cust)
    assert "owner" not in callbacks(m) and "apply_store" in callbacks(m)


async def test_support_and_terms_do_not_promise_missing_features():
    c = make_cb(1, "support")
    await menu_h.support(c)
    assert "قيد التطوير" in shown_text(c)
    c = make_cb(1, "terms")
    await menu_h.terms(c)
    assert "المراجعات" not in shown_text(c)


# ---------- wiring ----------
def test_dispatcher_builds_and_every_main_menu_button_has_a_handler():
    from main import build_dispatcher
    from tests.helpers import callback_is_handled
    dp = build_dispatcher()
    routers = dp.sub_routers
    assert len(routers) == 7
    for row in main_menu(is_seller=True, is_owner=True).inline_keyboard:
        for b in row:
            assert callback_is_handled(routers, b.callback_data), f"no handler for {b.callback_data}"
