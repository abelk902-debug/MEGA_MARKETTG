"""Phase 2C: public storefront, product images, owner category management and wiring."""
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import func, select

from bot.handlers import admin as admin_h
from bot.handlers import cart as cart_h
from bot.handlers import catalog as catalog_h
from bot.handlers import menu as menu_h
from bot.handlers import seller as seller_h
from bot.handlers import seller_products as seller_products_h
from bot.handlers import start as start_h
from bot.keyboards import admin as admin_kb
from bot.keyboards import catalog as store_kb
from bot.services import catalog as cs
from bot.services import categories as cat_service
from bot.services.errors import CategoryError, ValidationError
from database.models import AuditLog, CartItem, Category, Favorite, Product
from tests.helpers import (callback_is_handled, fake_tg_user, make_category, make_cb, make_product,
                           make_store, make_user, put_in_cart)

OWNER = 8399658960
ROUTERS = [start_h.router, admin_h.router, menu_h.router, catalog_h.router, cart_h.router,
           seller_h.router, seller_products_h.router]


@pytest.fixture
async def shop(patched_db):
    sf = patched_db
    owner = await make_user(sf, tg_id=OWNER, role="owner")
    seller = await make_user(sf, tg_id=7101, role="seller")
    buyer = await make_user(sf, tg_id=7102)
    return SimpleNamespace(
        sf=sf, owner=owner, seller=seller, buyer=buyer,
        store=await make_store(sf, seller.id, name="Mega Shop"),
        other_store=await make_store(sf, seller.id, name="Second Shop"),
        pending=await make_store(sf, seller.id, status="pending", name="Pending Shop"),
        suspended=await make_store(sf, seller.id, status="suspended", name="Suspended Shop"))


def cb(user, data):
    return make_cb(user.telegram_id, data)


def msg(user, text):
    return SimpleNamespace(from_user=fake_tg_user(user.telegram_id), text=text, answer=AsyncMock())


def state_for(user):
    return FSMContext(storage=MemoryStorage(),
                      key=StorageKey(bot_id=1, chat_id=user.telegram_id, user_id=user.telegram_id))


def shown(c):
    return c.message.edit_text.call_args.args[0]


def markup_of(c):
    return c.message.edit_text.call_args.kwargs['reply_markup']


def callbacks(markup):
    return {b.callback_data for row in markup.inline_keyboard for b in row}


def alerts(c):
    return [call.args[0] for call in c.answer.call_args_list if call.kwargs.get('show_alert')]


def toasts(c):
    return [call.args[0] for call in c.answer.call_args_list if call.args]


def button_labels(markup):
    return ' | '.join(b.text for row in markup.inline_keyboard for b in row)


def images(*file_ids):
    return json.dumps(list(file_ids))


# ================================================================== public store page
async def test_public_store_page_lists_only_visible_products(shop):
    visible = await make_product(shop.sf, shop.store.id, name='Visible Item')
    await make_product(shop.sf, shop.store.id, name='Hidden Item', status='hidden')
    await make_product(shop.sf, shop.other_store.id, name='Other Store Item')
    c = cb(shop.buyer, f'shop:{shop.store.id}:0')
    await catalog_h.store_page(c)
    text = shown(c)
    labels = button_labels(markup_of(c))
    assert 'Mega Shop' in text and 'Visible Item' in labels
    assert 'Hidden Item' not in labels and 'Other Store Item' not in labels
    assert '📦 1 منتج' in text and 'صفحة 1/1' in text
    assert f'product:{visible.id}' in callbacks(markup_of(c))


@pytest.mark.parametrize('attribute', ['pending', 'suspended'])
async def test_public_store_page_requires_an_approved_store(shop, attribute):
    store = getattr(shop, attribute)
    await make_product(shop.sf, store.id, name='Ghost')
    c = cb(shop.buyer, f'shop:{store.id}:0')
    await catalog_h.store_page(c)
    assert alerts(c) == [catalog_h.STORE_UNAVAILABLE]
    c.message.edit_text.assert_not_called()


async def test_public_store_page_shows_empty_state(shop):
    c = cb(shop.buyer, f'shop:{shop.store.id}:0')
    await catalog_h.store_page(c)
    assert 'لا توجد منتجات معروضة' in shown(c)


@pytest.mark.parametrize('data', ['shop:', 'shop:x:0', 'shop:1:y', 'shop:-1:0', 'shop:0:0', 'shop:1:0:0'])
async def test_store_page_rejects_malformed_callbacks(shop, data):
    c = cb(shop.buyer, data)
    await catalog_h.store_page(c)
    assert alerts(c) == [catalog_h.INVALID]
    c.message.edit_text.assert_not_called()


async def test_store_page_pagination(shop):
    for i in range(10):
        await make_product(shop.sf, shop.store.id, name=f'Item {i:02d}')
    c = cb(shop.buyer, f'shop:{shop.store.id}:0')
    await catalog_h.store_page(c)
    assert 'صفحة 1/2' in shown(c) and f'shop:{shop.store.id}:1' in callbacks(markup_of(c))
    c = cb(shop.buyer, f'shop:{shop.store.id}:1')
    await catalog_h.store_page(c)
    assert 'صفحة 2/2' in shown(c) and f'shop:{shop.store.id}:0' in callbacks(markup_of(c))
    # out-of-range pages are clamped, never crash
    c = cb(shop.buyer, f'shop:{shop.store.id}:99')
    await catalog_h.store_page(c)
    assert 'صفحة 2/2' in shown(c)


# ================================================================== product page + images
async def test_product_page_shows_the_first_image_with_caption(shop):
    p = await make_product(shop.sf, shop.store.id, name='Photo Item', price='50.00',
                           image_ids_json=images('AgAC-one', 'AgAC-two'))
    c = cb(shop.buyer, f'product:{p.id}')
    await catalog_h.product(c)
    c.message.answer_photo.assert_awaited_once()
    args, kwargs = c.message.answer_photo.call_args
    assert args[0] == 'AgAC-one'
    assert 'Photo Item' in kwargs['caption'] and 'Mega Shop' in kwargs['caption']
    buttons = callbacks(kwargs['reply_markup'])
    assert {f'add:{p.id}', f'fav:{p.id}', f'pimg:{p.id}', f'shop:{shop.store.id}:0'} <= buttons
    c.message.edit_text.assert_not_called()


async def test_single_image_product_has_no_all_photos_button(shop):
    p = await make_product(shop.sf, shop.store.id, image_ids_json=images('only'))
    c = cb(shop.buyer, f'product:{p.id}')
    await catalog_h.product(c)
    c.message.answer_photo.assert_awaited_once()
    assert f'pimg:{p.id}' not in callbacks(c.message.answer_photo.call_args.kwargs['reply_markup'])


async def test_product_without_images_is_rendered_as_text(shop):
    p = await make_product(shop.sf, shop.store.id, name='Text Item')
    c = cb(shop.buyer, f'product:{p.id}')
    await catalog_h.product(c)
    c.message.answer_photo.assert_not_called()
    assert 'Text Item' in shown(c) and f'shop:{shop.store.id}:0' in callbacks(markup_of(c))


async def test_public_product_page_shows_discount_and_out_of_stock(shop):
    p = await make_product(shop.sf, shop.store.id, name='Sale Item', price='80.00',
                           stock=0, original_price=100)
    c = cb(shop.buyer, f'product:{p.id}')
    await catalog_h.product(c)
    text = shown(c)
    assert 'خصم 20%' in text and 'نفد المخزون' in text


async def test_long_description_is_truncated_to_the_photo_caption_limit(shop):
    p = await make_product(shop.sf, shop.store.id, description='و' * 2000,
                           image_ids_json=images('pic'))
    c = cb(shop.buyer, f'product:{p.id}')
    await catalog_h.product(c)
    assert len(c.message.answer_photo.call_args.kwargs['caption']) <= 1024


@pytest.mark.parametrize('status', ['hidden', 'draft', 'deleted'])
async def test_inactive_products_are_not_public(shop, status):
    p = await make_product(shop.sf, shop.store.id, status=status)
    c = cb(shop.buyer, f'product:{p.id}')
    await catalog_h.product(c)
    assert alerts(c) == [catalog_h.PRODUCT_UNAVAILABLE]
    c.message.edit_text.assert_not_called()
    c.message.answer_photo.assert_not_called()


async def test_products_of_suspended_stores_are_not_public(shop):
    p = await make_product(shop.sf, shop.suspended.id, name='Ghost')
    c = cb(shop.buyer, f'product:{p.id}')
    await catalog_h.product(c)
    assert alerts(c) == [catalog_h.PRODUCT_UNAVAILABLE]


@pytest.mark.parametrize('data', ['product:', 'product:x', 'product:0', 'product:-3'])
async def test_product_page_rejects_malformed_callbacks(shop, data):
    c = cb(shop.buyer, data)
    await catalog_h.product(c)
    assert alerts(c) == [catalog_h.INVALID]
    c.message.edit_text.assert_not_called()


async def test_all_photos_sends_a_media_group_in_order(shop):
    p = await make_product(shop.sf, shop.store.id, image_ids_json=images('a', 'b', 'c'))
    c = cb(shop.buyer, f'pimg:{p.id}')
    await catalog_h.product_images(c)
    c.message.answer_media_group.assert_awaited_once()
    media = c.message.answer_media_group.call_args.args[0]
    assert [m.media for m in media] == ['a', 'b', 'c']


async def test_single_photo_opens_as_a_photo(shop):
    p = await make_product(shop.sf, shop.store.id, image_ids_json=images('solo'))
    c = cb(shop.buyer, f'pimg:{p.id}')
    await catalog_h.product_images(c)
    c.message.answer_photo.assert_awaited_once_with('solo')


async def test_all_photos_rejects_missing_images_and_hidden_products(shop):
    none = await make_product(shop.sf, shop.store.id, name='No Pics')
    hidden = await make_product(shop.sf, shop.store.id, status='hidden', image_ids_json=images('x'))
    for pid, expected in ((none.id, 'لا توجد صور لهذا المنتج'), (hidden.id, catalog_h.PRODUCT_UNAVAILABLE)):
        c = cb(shop.buyer, f'pimg:{pid}')
        await catalog_h.product_images(c)
        assert alerts(c) == [expected]
        c.message.answer_photo.assert_not_called()
        c.message.answer_media_group.assert_not_called()


async def test_product_view_is_counted(shop):
    p = await make_product(shop.sf, shop.store.id)
    await catalog_h.product(cb(shop.buyer, f'product:{p.id}'))
    async with shop.sf() as s:
        assert (await s.get(Product, p.id)).views == 1


# ================================================================== category services
async def test_rename_category_updates_name_and_audits(sf):
    category = await make_category(sf, 'Shoes')
    old_slug = category.slug
    updated = await cat_service.rename_category(sf, OWNER, category.id, '  أحذية  ')
    assert updated.name == 'أحذية'
    async with sf() as s:
        log = (await s.execute(select(AuditLog).where(AuditLog.action == 'category.rename'))).scalar_one()
        assert log.actor_id == OWNER and log.details == 'Shoes -> أحذية'
        stored = await s.get(Category, category.id)
        assert stored.name == 'أحذية' and stored.slug == old_slug


async def test_rename_rejects_duplicate_names_case_insensitively(sf):
    await make_category(sf, 'Shoes')
    other = await make_category(sf, 'Bags')
    with pytest.raises(CategoryError) as e:
        await cat_service.rename_category(sf, OWNER, other.id, 'shoes')
    assert e.value.code == 'exists'
    async with sf() as s:
        assert (await s.get(Category, other.id)).name == 'Bags'


async def test_rename_to_same_name_with_different_case_is_allowed(sf):
    category = await make_category(sf, 'shoes')
    updated = await cat_service.rename_category(sf, OWNER, category.id, 'Shoes')
    assert updated.name == 'Shoes'


@pytest.mark.parametrize('name', ['x', '   ', None, 'a' * 101])
async def test_rename_rejects_invalid_names(sf, name):
    category = await make_category(sf, 'Shoes')
    with pytest.raises(ValidationError):
        await cat_service.rename_category(sf, OWNER, category.id, name)
    async with sf() as s:
        assert (await s.get(Category, category.id)).name == 'Shoes'


async def test_rename_requires_owner_and_existing_category(sf):
    category = await make_category(sf, 'Shoes')
    with pytest.raises(CategoryError) as e:
        await cat_service.rename_category(sf, 12345, category.id, 'Other')
    assert e.value.code == 'forbidden'
    with pytest.raises(CategoryError) as e:
        await cat_service.rename_category(sf, OWNER, 9999, 'Other')
    assert e.value.code == 'not_found'
    async with sf() as s:
        assert (await s.get(Category, category.id)).name == 'Shoes'


async def test_delete_category_detaches_products_but_keeps_them(sf):
    seller = await make_user(sf, role='seller')
    store = await make_store(sf, seller.id)
    category = await make_category(sf, 'Toys')
    active = await make_product(sf, store.id, category_id=category.id)
    hidden = await make_product(sf, store.id, category_id=category.id, status='hidden')
    buyer = await make_user(sf)
    await put_in_cart(sf, buyer.id, active.id)
    async with sf() as s:
        s.add(Favorite(user_id=buyer.id, product_id=active.id))
        await s.commit()

    await cat_service.delete_category(sf, OWNER, category.id)

    async with sf() as s:
        assert await s.get(Category, category.id) is None
        assert (await s.get(Product, active.id)).category_id is None
        assert (await s.get(Product, hidden.id)).category_id is None
        assert await s.scalar(select(func.count(CartItem.id))) == 1
        assert await s.scalar(select(func.count(Favorite.id))) == 1
        log = (await s.execute(select(AuditLog).where(AuditLog.action == 'category.delete'))).scalar_one()
        assert log.actor_id == OWNER and log.target_id == str(category.id) and log.details == 'Toys'


async def test_delete_category_permissions_and_missing(sf):
    category = await make_category(sf, 'Toys')
    with pytest.raises(CategoryError) as e:
        await cat_service.delete_category(sf, 12345, category.id)
    assert e.value.code == 'forbidden'
    with pytest.raises(CategoryError) as e:
        await cat_service.delete_category(sf, OWNER, 9999)
    assert e.value.code == 'not_found'
    async with sf() as s:
        assert await s.get(Category, category.id) is not None


# ================================================================== owner category UI
async def test_owner_categories_screen_lists_every_category(shop):
    await make_category(shop.sf, 'Visible')
    await make_category(shop.sf, 'Hidden', active=False)
    c = cb(shop.owner, 'admin:cats')
    await admin_h.admin_categories(c)
    text = shown(c)
    assert 'العدد: 2' in text and 'الظاهرة للمشترين: 1' in text
    assert {'admin:catadd', 'owner'} <= callbacks(markup_of(c))


async def test_owner_adds_category_through_the_ui(shop):
    st = state_for(shop.owner)
    c = cb(shop.owner, 'admin:catadd')
    await admin_h.category_add_start(c, st)
    assert await st.get_state() == admin_h.CategoryForm.add.state
    m = msg(shop.owner, '  إلكترونيات  ')
    await admin_h.category_add_name(m, st)
    assert await st.get_state() is None
    text = m.answer.call_args.args[0]
    labels = button_labels(m.answer.call_args.kwargs['reply_markup'])
    assert 'تمت إضافة التصنيف' in text and 'إلكترونيات' in labels
    async with shop.sf() as s:
        assert [x.name for x in await cs.list_categories(s)] == ['إلكترونيات']


@pytest.mark.parametrize('value', ['shoes', 'x'])
async def test_add_category_errors_keep_the_form_open(shop, value):
    await make_category(shop.sf, 'Shoes')
    st = state_for(shop.owner)
    await admin_h.category_add_start(cb(shop.owner, 'admin:catadd'), st)
    m = msg(shop.owner, value)
    await admin_h.category_add_name(m, st)
    assert m.answer.call_args.args[0].startswith('⚠️')
    assert await st.get_state() == admin_h.CategoryForm.add.state
    async with shop.sf() as s:
        assert await s.scalar(select(func.count(Category.id))) == 1


async def test_owner_renames_category_through_the_ui(shop):
    category = await make_category(shop.sf, 'Old')
    st = state_for(shop.owner)
    c = cb(shop.owner, f'admin:catren:{category.id}')
    await admin_h.category_rename_start(c, st)
    assert await st.get_state() == admin_h.CategoryForm.rename.state
    m = msg(shop.owner, 'New')
    await admin_h.category_rename_name(m, st)
    assert await st.get_state() is None
    assert 'تم تحديث اسم التصنيف' in m.answer.call_args.args[0]
    async with shop.sf() as s:
        assert (await s.get(Category, category.id)).name == 'New'


async def test_rename_typo_keeps_the_form_open(shop):
    category = await make_category(shop.sf, 'Old')
    st = state_for(shop.owner)
    await admin_h.category_rename_start(cb(shop.owner, f'admin:catren:{category.id}'), st)
    m = msg(shop.owner, 'x')
    await admin_h.category_rename_name(m, st)
    assert m.answer.call_args.args[0].startswith('⚠️')
    assert await st.get_state() == admin_h.CategoryForm.rename.state
    async with shop.sf() as s:
        assert (await s.get(Category, category.id)).name == 'Old'


async def test_rename_with_lost_state_is_safe(shop):
    st = state_for(shop.owner)
    await st.set_state(admin_h.CategoryForm.rename)
    m = msg(shop.owner, 'Whatever')
    await admin_h.category_rename_name(m, st)
    assert 'انتهت الجلسة' in m.answer.call_args.args[0] and await st.get_state() is None


async def test_owner_hides_and_shows_category(shop):
    category = await make_category(shop.sf, 'Toys')
    c = cb(shop.owner, f'admin:cattog:{category.id}')
    await admin_h.category_toggle(c)
    assert toasts(c) == ['🙈 تم إخفاء التصنيف']
    async with shop.sf() as s:
        assert not (await s.get(Category, category.id)).is_active
        assert await cs.list_categories(s) == []
    actions = cb(shop.owner, f'admin:cat:{category.id}')
    await admin_h.category_actions(actions)
    assert 'مخفي' in shown(actions) and f'admin:catren:{category.id}' in callbacks(markup_of(actions))
    await admin_h.category_toggle(cb(shop.owner, f'admin:cattog:{category.id}'))
    async with shop.sf() as s:
        assert (await s.get(Category, category.id)).is_active


async def test_owner_deletes_category_after_confirmation(shop):
    category = await make_category(shop.sf, 'Old')
    p = await make_product(shop.sf, shop.store.id, category_id=category.id)
    c = cb(shop.owner, f'admin:catdel:{category.id}')
    await admin_h.category_delete_confirm(c)
    assert 'يوجد 1 منتج' in shown(c)
    assert {f'admin:catdelok:{category.id}', f'admin:cat:{category.id}'} <= callbacks(markup_of(c))
    c = cb(shop.owner, f'admin:catdelok:{category.id}')
    await admin_h.category_delete_do(c)
    assert toasts(c) == ['🗑 تم حذف التصنيف']
    assert 'تم حذف التصنيف' in shown(c)
    async with shop.sf() as s:
        assert await s.get(Category, category.id) is None
        assert (await s.get(Product, p.id)).category_id is None


async def test_category_delete_confirm_without_products_has_no_warning(shop):
    category = await make_category(shop.sf, 'Empty')
    c = cb(shop.owner, f'admin:catdel:{category.id}')
    await admin_h.category_delete_confirm(c)
    assert 'يوجد' not in shown(c) and 'لا يمكن التراجع' in shown(c)


CATEGORY_CALLBACKS = [
    ('admin_categories', 'admin:cats'),
    ('category_add_start', 'admin:catadd'),
    ('category_rename_start', 'admin:catren:{c}'),
    ('category_toggle', 'admin:cattog:{c}'),
    ('category_delete_confirm', 'admin:catdel:{c}'),
    ('category_delete_do', 'admin:catdelok:{c}')]


@pytest.mark.parametrize('handler_name,template', CATEGORY_CALLBACKS)
async def test_every_category_callback_rejects_non_owners(shop, handler_name, template):
    category = await make_category(shop.sf, 'Keep')
    st = state_for(shop.buyer)
    c = cb(shop.buyer, template.format(c=category.id))
    handler = getattr(admin_h, handler_name)
    kwargs = {'state': st} if 'state' in inspect.signature(handler).parameters else {}
    await handler(c, **kwargs)
    assert alerts(c) == ['غير مصرح']
    c.message.edit_text.assert_not_called()
    assert await st.get_state() is None
    async with shop.sf() as s:
        stored = await s.get(Category, category.id)
        assert stored is not None and stored.name == 'Keep' and stored.is_active


async def test_missing_category_callbacks_show_an_alert(shop):
    for handler, data in ((admin_h.category_rename_start, 'admin:catren:9999'),
                          (admin_h.category_toggle, 'admin:cattog:9999'),
                          (admin_h.category_delete_confirm, 'admin:catdel:9999'),
                          (admin_h.category_delete_do, 'admin:catdelok:9999')):
        c = cb(shop.owner, data)
        kwargs = {'state': state_for(shop.owner)} if 'state' in inspect.signature(handler).parameters else {}
        await handler(c, **kwargs)
        assert 'غير موجود' in alerts(c)[0]


async def test_malformed_category_callbacks_are_rejected(shop):
    for handler, data in ((admin_h.category_rename_start, 'admin:catren:abc'),
                          (admin_h.category_toggle, 'admin:cattog:abc'),
                          (admin_h.category_delete_confirm, 'admin:catdel:abc'),
                          (admin_h.category_delete_do, 'admin:catdelok:abc')):
        c = cb(shop.owner, data)
        kwargs = {'state': state_for(shop.owner)} if 'state' in inspect.signature(handler).parameters else {}
        await handler(c, **kwargs)
        assert alerts(c)
        c.message.edit_text.assert_not_called()


async def test_owner_dashboard_links_to_categories(shop):
    c = cb(shop.owner, 'owner')
    await admin_h.owner(c)
    assert 'admin:cats' in callbacks(markup_of(c))


# ================================================================== wiring (no dead buttons)
def test_every_phase_2c_button_has_a_real_handler():
    p = SimpleNamespace(id=5, name='n', price=1, currency='DZD', store_id=1, status='active')
    page = SimpleNamespace(items=[p], page=0, pages=2)
    category = SimpleNamespace(id=7, name='c', is_active=True)
    markups = [
        store_kb.store_page_kb(page, 1),
        store_kb.product_page_kb(p, 3),
        admin_kb.categories_list_kb([category]),
        admin_kb.category_actions_kb(category),
        admin_kb.category_delete_kb(7),
        admin_kb.category_cancel_kb(),
    ]
    for markup in markups:
        for data in callbacks(markup):
            assert callback_is_handled(ROUTERS, data), f'dead button: {data}'


async def test_owner_dashboard_buttons_are_all_wired(shop):
    c = cb(shop.owner, 'owner')
    await admin_h.owner(c)
    for data in callbacks(markup_of(c)):
        assert callback_is_handled(ROUTERS, data), f'dead button: {data}'


def test_category_wizard_states_have_message_handlers():
    found = set()
    for h in admin_h.router.message.handlers:
        for f in h.filters or []:
            obj = f.callback
            if isinstance(obj, State):
                found.add(obj.state)
            for s in getattr(obj, 'states', ()) or ():
                found.add(s.state if isinstance(s, State) else str(s))
    if not found:
        pytest.skip('could not introspect state filters on this aiogram version')
    assert {admin_h.CategoryForm.add.state, admin_h.CategoryForm.rename.state} <= found
