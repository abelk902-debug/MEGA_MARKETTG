
import pytest
from sqlalchemy import select

from bot.services.errors import OrderError
from bot.services.orders import (
    create_order_from_cart,
    list_seller_orders,
    update_suborder_status,
)
from database.models import (
    OrderStatus,
    OrderStatusHistory,
    SubOrder,
)
from tests.helpers import (
    make_product,
    make_store,
    make_user,
    put_in_cart,
)


@pytest.fixture
async def seller_order_world(sf):
    seller1 = await make_user(sf, tg_id=8101, role="seller")
    seller2 = await make_user(sf, tg_id=8102, role="seller")
    buyer = await make_user(sf, tg_id=8103)

    store1 = await make_store(sf, seller1.id, name="SellerStore1")
    store2 = await make_store(sf, seller2.id, name="SellerStore2")

    product1 = await make_product(
        sf, store1.id, price="100", stock=10
    )
    product2 = await make_product(
        sf, store2.id, price="200", stock=10
    )

    await put_in_cart(sf, buyer.id, product1.id)
    await put_in_cart(sf, buyer.id, product2.id)

    checkout = await create_order_from_cart(sf, buyer.id)

    async with sf() as session:
        result = await session.execute(
            select(SubOrder).where(
                SubOrder.order_id == checkout.order_id
            )
        )
        suborders = result.scalars().all()

        suborder1 = next(
            item for item in suborders
            if item.store_id == store1.id
        )
        suborder2 = next(
            item for item in suborders
            if item.store_id == store2.id
        )

        ids = (suborder1.id, suborder2.id)

    return {
        "sf": sf,
        "seller1": seller1,
        "seller2": seller2,
        "buyer": buyer,
        "store1": store1,
        "store2": store2,
        "suborder1_id": ids[0],
        "suborder2_id": ids[1],
        "order_id": checkout.order_id,
    }


async def test_seller_sees_only_own_orders(seller_order_world):
    w = seller_order_world

    async with w["sf"]() as session:
        orders = await list_seller_orders(
            session,
            w["seller1"].id,
        )

    assert len(orders) == 1
    assert orders[0].id == w["suborder1_id"]
    assert orders[0].store_id == w["store1"].id


async def test_seller_can_accept_order(seller_order_world):
    w = seller_order_world

    result = await update_suborder_status(
        w["sf"],
        w["seller1"].id,
        w["suborder1_id"],
        OrderStatus.ACCEPTED.value,
    )

    assert result.old_status == OrderStatus.AWAITING_SELLER_APPROVAL.value
    assert result.new_status == OrderStatus.ACCEPTED.value
    assert result.buyer_telegram_id == 8103
    assert result.store_name == "SellerStore1"

    async with w["sf"]() as session:
        suborder = await session.get(
            SubOrder,
            w["suborder1_id"],
        )

    assert suborder.status == OrderStatus.ACCEPTED.value


async def test_seller_can_reject_order(seller_order_world):
    w = seller_order_world

    result = await update_suborder_status(
        w["sf"],
        w["seller1"].id,
        w["suborder1_id"],
        OrderStatus.REJECTED.value,
    )

    assert result.new_status == OrderStatus.REJECTED.value

    async with w["sf"]() as session:
        suborder = await session.get(
            SubOrder,
            w["suborder1_id"],
        )

    assert suborder.status == OrderStatus.REJECTED.value


async def test_seller_can_progress_order(seller_order_world):
    w = seller_order_world

    transitions = [
        OrderStatus.ACCEPTED.value,
        OrderStatus.PROCESSING.value,
        OrderStatus.SHIPPED.value,
        OrderStatus.COMPLETED.value,
    ]

    for status in transitions:
        await update_suborder_status(
            w["sf"],
            w["seller1"].id,
            w["suborder1_id"],
            status,
        )

    async with w["sf"]() as session:
        suborder = await session.get(
            SubOrder,
            w["suborder1_id"],
        )

    assert suborder.status == OrderStatus.COMPLETED.value


async def test_seller_cannot_update_another_sellers_order(
    seller_order_world,
):
    w = seller_order_world

    with pytest.raises(OrderError) as exc:
        await update_suborder_status(
            w["sf"],
            w["seller1"].id,
            w["suborder2_id"],
            OrderStatus.ACCEPTED.value,
        )

    assert exc.value.code == "forbidden"


async def test_seller_cannot_skip_order_status(seller_order_world):
    w = seller_order_world

    with pytest.raises(OrderError) as exc:
        await update_suborder_status(
            w["sf"],
            w["seller1"].id,
            w["suborder1_id"],
            OrderStatus.SHIPPED.value,
        )

    assert exc.value.code == "invalid_transition"


async def test_nonexistent_suborder_returns_error(seller_order_world):
    w = seller_order_world

    with pytest.raises(OrderError) as exc:
        await update_suborder_status(
            w["sf"],
            w["seller1"].id,
            999999,
            OrderStatus.ACCEPTED.value,
        )

    assert exc.value.code == "not_found"


async def test_seller_status_changes_are_recorded(seller_order_world):
    w = seller_order_world

    await update_suborder_status(
        w["sf"],
        w["seller1"].id,
        w["suborder1_id"],
        OrderStatus.ACCEPTED.value,
        note="seller accepted",
    )

    async with w["sf"]() as session:
        history = (
            await session.execute(
                select(OrderStatusHistory).where(
                    OrderStatusHistory.order_id == w["order_id"],
                    OrderStatusHistory.note == "seller accepted",
                )
            )
        ).scalars().all()

    assert len(history) == 1
    assert history[0].old_status == OrderStatus.AWAITING_SELLER_APPROVAL.value
    assert history[0].new_status == OrderStatus.ACCEPTED.value
    assert history[0].changed_by == w["seller1"].id
