import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.db import Base, init_db, normalize_legacy_statuses
from database.models import CartItem, Order, Product, User
from tests.helpers import make_product, make_store, make_user


def test_user_insert():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(telegram_id=12345, first_name="Test"))
        session.commit()
        user = session.query(User).filter_by(telegram_id=12345).one()
        assert user.first_name == "Test"
        assert user.role == "customer"
    engine.dispose()


async def test_sqlite_pragmas_enabled(engine):
    async with engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 1
        assert (await conn.execute(text("PRAGMA journal_mode"))).scalar().lower() == "wal"


async def test_init_db_creates_tables(tmp_path):
    from database.db import make_engine
    eng = make_engine("sqlite+aiosqlite:///" + (tmp_path / "fresh.db").as_posix())
    await init_db(eng)
    async with eng.connect() as conn:
        names = {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")))}
    await eng.dispose()
    assert {"users", "stores", "products", "orders", "suborders", "cart_items"} <= names


async def test_foreign_keys_are_enforced(sf):
    u = await make_user(sf)
    async with sf() as s:
        s.add(CartItem(user_id=u.id, product_id=999999, quantity=1))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_stock_cannot_go_negative_in_db(sf):
    """The CHECK constraint must reject stock < 0; the failed transaction must leave no trace."""
    seller = await make_user(sf)
    store = await make_store(sf, seller.id)
    p = await make_product(sf, store.id, stock=1)

    async with sf() as s:
        with pytest.raises(IntegrityError):
            # the constraint fires when the statement executes, so it must be inside raises
            await s.execute(text("UPDATE products SET stock = -1 WHERE id = :i"), {"i": p.id})
        await s.rollback()  # close the failed transaction cleanly

    async with sf() as s:  # a fresh session still works and stock is unchanged
        assert (await s.get(Product, p.id)).stock == 1


async def test_legacy_statuses_are_normalized_and_idempotent(sf, engine):
    buyer = await make_user(sf)
    async with sf() as s:
        s.add(Order(order_no="OLD-1", buyer_id=buyer.id, total=1, status="awaiting_seller"))
        await s.commit()
    async with engine.begin() as conn:
        await normalize_legacy_statuses(conn)
        await normalize_legacy_statuses(conn)
    async with sf() as s:
        assert (await s.execute(select(Order.status))).scalar_one() == "AWAITING_SELLER_APPROVAL"