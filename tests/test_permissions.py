from types import SimpleNamespace

from bot.services.permissions import is_owner_id, is_seller, is_store_owner


def test_owner_is_decided_by_telegram_id_only():
    assert is_owner_id(8399658960)
    assert not is_owner_id(1)
    assert not is_owner_id(None)


def test_is_seller_roles():
    assert is_seller(SimpleNamespace(role="seller"))
    assert is_seller(SimpleNamespace(role="owner"))
    assert not is_seller(SimpleNamespace(role="customer"))
    assert not is_seller(None)


def test_store_ownership_uses_db_id_not_telegram_id():
    user = SimpleNamespace(id=5, telegram_id=900001)
    assert is_store_owner(user, SimpleNamespace(owner_id=5))
    assert not is_store_owner(user, SimpleNamespace(owner_id=900001))  # the old bug
    assert not is_store_owner(None, SimpleNamespace(owner_id=5))
