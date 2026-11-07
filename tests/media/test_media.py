import pytest

from app.core.errors import AppError
from app.modules.media import service
from app.modules.projects.models import Project
from tests.factories import login, make_user

PNG = b"\x89PNG\r\n\x1a\nxx"


@pytest.fixture
async def setup(client, make_client, db):
    ana = await make_user(db, email="ana@x.com")
    await make_user(db, email="bob@x.com")
    await login(client, "ana@x.com")
    bob = make_client()
    await login(bob, "bob@x.com")
    p = Project(owner_id=ana.id, name="P")
    await db.projects.insert_one(p.to_doc())
    return client, bob, p


async def test_round_trip(db, setup):
    _, _, p = setup
    mid = await service.put(db, p, PNG, "image/png")
    assert await service.get(db, p, mid) == (PNG, "image/png")


async def test_too_large(db, setup):
    _, _, p = setup
    await service.put(db, p, b"x" * 2 * 1024 * 1024, "image/png")
    with pytest.raises(AppError) as e:
        await service.put(db, p, b"x" * (2 * 1024 * 1024 + 1), "image/png")
    assert e.value.code == "MEDIA_TOO_LARGE"


async def test_api_owner_only(db, setup):
    ana, bob, p = setup
    mid = await service.put(db, p, PNG, "image/png")
    url = f"/api/v1/projects/{p.id}/media/{mid}"
    r = await ana.get(url)
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["cache-control"] == "private, max-age=86400"
    assert r.headers["content-type"] == "image/png"
    assert (await bob.get(url)).status_code == 404
    assert (await ana.get(f"/api/v1/projects/{p.id}/media/nope")).status_code == 404
