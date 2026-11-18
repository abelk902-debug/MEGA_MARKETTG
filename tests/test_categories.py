import pytest
from sqlalchemy import select

from bot.services import catalog as cs
from bot.services import categories as cat
from bot.services.errors import CategoryError, ValidationError
from database.models import AuditLog
from tests.helpers import make_category

OWNER = 8399658960


async def test_owner_creates_category_and_it_is_logged(sf):
    c = await cat.create_category(sf, OWNER, "  إلكترونيات  ")
    assert c.name == "إلكترونيات" and c.is_active and c.slug == "إلكترونيات"
    async with sf() as s:
        assert [x.name for x in await cs.list_categories(s)] == ["إلكترونيات"]
        log = (await s.execute(select(AuditLog))).scalar_one()
        assert log.action == "category.create" and log.actor_id == OWNER


async def test_non_owner_cannot_create_or_toggle(sf):
    with pytest.raises(CategoryError) as e:
        await cat.create_category(sf, 12345, "Shoes")
    assert e.value.code == "forbidden"
    c = await make_category(sf)
    with pytest.raises(CategoryError) as e:
        await cat.set_category_active(sf, 12345, c.id, False)
    assert e.value.code == "forbidden"
    async with sf() as s:
        assert (await cs.list_categories(s))[0].is_active


async def test_duplicate_names_are_rejected_case_insensitively(sf):
    await cat.create_category(sf, OWNER, "Shoes")
    with pytest.raises(CategoryError) as e:
        await cat.create_category(sf, OWNER, "shoes")
    assert e.value.code == "exists"


async def test_slug_collisions_get_a_suffix(sf):
    a = await cat.create_category(sf, OWNER, "A b")
    b = await cat.create_category(sf, OWNER, "a-b")
    assert a.slug == "a-b" and b.slug.startswith("a-b-") and a.slug != b.slug


@pytest.mark.parametrize("name", ["x", "   ", "a" * 101, None])
async def test_invalid_names(sf, name):
    with pytest.raises(ValidationError):
        await cat.create_category(sf, OWNER, name)


async def test_hide_and_show_category(sf):
    c = await make_category(sf, "Toys")
    await cat.set_category_active(sf, OWNER, c.id, False)
    async with sf() as s:
        assert await cs.list_categories(s) == []
        assert [x.name for x in await cat.list_all_categories(s)] == ["Toys"]
    await cat.set_category_active(sf, OWNER, c.id, True)
    async with sf() as s:
        assert [x.name for x in await cs.list_categories(s)] == ["Toys"]


async def test_toggle_unknown_category(sf):
    with pytest.raises(CategoryError) as e:
        await cat.set_category_active(sf, OWNER, 9999, False)
    assert e.value.code == "not_found"
