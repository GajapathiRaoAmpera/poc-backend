from tests.api.test_artifacts_api import with_architecture  # noqa: F401


async def test_change_request_preview_apply(owner_client, with_architecture, work):  # noqa: F811
    c, base = owner_client, with_architecture
    r = await c.post(
        f"{base}/artifacts/architecture/change-requests",
        json={"instruction": "Add Redis between the API and the worker", "base_revision": 1},
    )
    assert r.status_code == 202 and r.json()["mode"] == "change_request"
    run_id = r.json()["id"]
    await work()
    run = (await c.get(f"{base}/runs/{run_id}")).json()
    cs_id = run["output"]["change_set_id"]
    cs = (await c.get(f"{base}/change-sets/{cs_id}")).json()
    assert cs["status"] == "PROPOSED" and "n_redis" in cs["impact"]["added"] and cs["ops"]
    pending = (await c.get(f"{base}/change-sets", params={"status": "PROPOSED"})).json()
    assert [p["id"] for p in pending] == [cs_id]

    r = await c.post(f"{base}/change-sets/{cs_id}/apply")
    assert r.status_code == 201 and r.json()["revision"] == 2 and r.json()["origin"] == "change_set"
    assert (await c.get(f"{base}/change-sets/{cs_id}")).json()["status"] == "APPLIED"


async def test_change_request_on_stale_base_is_conflict(owner_client, with_architecture):  # noqa: F811
    c, base = owner_client, with_architecture
    await c.post(f"{base}/artifacts/architecture/revisions/1/restore")
    r = await c.post(
        f"{base}/artifacts/architecture/change-requests",
        json={"instruction": "Add Redis", "base_revision": 1},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "REVISION_CONFLICT"


async def test_reject(owner_client, with_architecture, work):  # noqa: F811
    c, base = owner_client, with_architecture
    run = (
        await c.post(
            f"{base}/artifacts/architecture/change-requests",
            json={"instruction": "Add a cache", "base_revision": 1},
        )
    ).json()
    await work()
    cs_id = (await c.get(f"{base}/runs/{run['id']}")).json()["output"]["change_set_id"]
    r = await c.post(f"{base}/change-sets/{cs_id}/reject")
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    assert (await c.get(f"{base}/artifacts/architecture")).json()["latest_revision"] == 1
