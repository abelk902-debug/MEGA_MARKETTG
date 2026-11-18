import httpx
import pytest

from admin_panel.app import app, get_session_factory
from config.settings import settings
from database.models import Order
from tests.helpers import make_user

KEY = "test-admin-key-0123456789"


@pytest.fixture
async def client(sf):
    app.dependency_overrides[get_session_factory] = lambda: sf
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_health_is_public(client):
    assert (await client.get("/health")).status_code == 200


@pytest.mark.parametrize("path", ["/stats", "/users"])
async def test_protected_endpoints_need_the_right_key(client, path):
    assert (await client.get(path)).status_code == 401
    assert (await client.get(path, headers={"X-API-Key": "wrong"})).status_code == 401
    assert (await client.get(path, headers={"X-API-Key": KEY})).status_code == 200


@pytest.mark.parametrize("bad", ["change-me", "short"])
async def test_default_or_weak_key_disables_api(client, monkeypatch, bad):
    monkeypatch.setattr(settings, "admin_api_key", bad)
    r = await client.get("/stats", headers={"X-API-Key": bad})
    assert r.status_code == 503


async def test_stats_exclude_cancelled_and_rejected(client, sf):
    buyer = await make_user(sf)
    async with sf() as s:
        for i, (st, total) in enumerate([("COMPLETED", 100), ("AWAITING_SELLER_APPROVAL", 50),
                                         ("CANCELLED", 999), ("REJECTED", 999)]):
            s.add(Order(order_no=f"T-{i}", buyer_id=buyer.id, total=total, status=st))
        await s.commit()
    data = (await client.get("/stats", headers={"X-API-Key": KEY})).json()
    assert data["orders"] == 4 and data["sales"] == 150.0 and data["users"] == 1


async def test_users_limit_is_clamped(client, sf):
    for _ in range(3):
        await make_user(sf)
    r = await client.get("/users?limit=-5", headers={"X-API-Key": KEY})
    assert r.status_code == 200 and len(r.json()) == 1
