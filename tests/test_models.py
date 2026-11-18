from database.db import Base
from database.models import *  # noqa: F403


def test_tables():
    expected = {'users', 'stores', 'categories', 'products', 'cart_items', 'favorites', 'orders', 'suborders',
                'order_items', 'order_status_history', 'payments', 'ledger_entries', 'coupons', 'reviews',
                'tickets', 'ticket_messages', 'store_applications', 'audit_logs', 'platform_settings'}
    assert expected.issubset(Base.metadata.tables)


def test_owner():
    from config.settings import settings
    assert settings.owner_id == 8399658960


def test_order_statuses_match_specification():
    assert {s.value for s in OrderStatus} == {  # noqa: F405
        'NEW', 'AWAITING_SELLER_APPROVAL', 'ACCEPTED', 'PROCESSING', 'SHIPPED', 'READY_FOR_PICKUP',
        'COMPLETED', 'CANCELLED', 'REJECTED', 'DISPUTED'}
