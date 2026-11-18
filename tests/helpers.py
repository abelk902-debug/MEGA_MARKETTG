import itertools
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from database.models import CartItem, Category, Product, Store, User

_counter = itertools.count(1)


async def make_user(sf, tg_id=None, role="customer", banned=False, name="User"):
    async with sf() as s:
        u = User(telegram_id=tg_id or 100000 + next(_counter), first_name=name, role=role, is_banned=banned)
        s.add(u)
        await s.commit()
        return u


async def make_store(sf, owner_id, status="approved", name=None):
    async with sf() as s:
        st = Store(owner_id=owner_id, name=name or f"Store{next(_counter)}", status=status)
        s.add(st)
        await s.commit()
        return st


async def make_product(sf, store_id, name=None, price="10.00", stock=5, status="active",
                       currency="DZD", public_id=None, description="desc", **extra):
    async with sf() as s:
        p = Product(store_id=store_id, name=name or f"Prod{next(_counter)}", description=description,
                    price=Decimal(price), stock=stock, status=status, currency=currency,
                    public_id=public_id or f"P{next(_counter):06d}", **extra)
        s.add(p)
        await s.commit()
        return p


async def make_category(sf, name=None, active=True):
    async with sf() as s:
        n = name or f"Cat{next(_counter)}"
        c = Category(name=n, slug=f"cat-{next(_counter)}", is_active=active)
        s.add(c)
        await s.commit()
        return c


async def put_in_cart(sf, user_id, product_id, qty=1):
    async with sf() as s:
        s.add(CartItem(user_id=user_id, product_id=product_id, quantity=qty))
        await s.commit()


def fake_tg_user(tg_id, username=None, first_name="X"):
    return SimpleNamespace(id=tg_id, username=username, first_name=first_name, last_name=None, is_bot=False)


def make_cb(tg_id, data):
    """Minimal stand-in for aiogram CallbackQuery."""
    c = SimpleNamespace(from_user=fake_tg_user(tg_id), data=data)
    c.message = SimpleNamespace(edit_text=AsyncMock(), edit_caption=AsyncMock(), answer=AsyncMock(),
                                answer_photo=AsyncMock(), answer_media_group=AsyncMock())
    c.answer = AsyncMock()
    c.bot = SimpleNamespace(send_message=AsyncMock())
    return c


def callback_is_handled(routers, data, ignore=("stale_flow",)):
    """True if some real (non-fallback) callback handler's F-filters accept this callback_data."""
    from magic_filter import MagicFilter
    probe = SimpleNamespace(data=data)
    for router in routers:
        for h in router.callback_query.handlers:
            if h.callback.__name__ in ignore:
                continue
            magic = [f for f in (h.filters or []) if isinstance(getattr(f.callback, "__self__", None), MagicFilter)]
            if magic and all(f.callback(probe) for f in magic):
                return True
    return False
