import copy

import pytest


@pytest.fixture
async def with_architecture(owner_client, project_id, work):
    c, base = owner_client, f"/api/v1/projects/{project_id}"
    await c.post(f"{base}/stages/requirements/runs", json={"mode": "generate"})
    await work()
    await c.put(
        f"{base}/artifacts/requirements/answers",
        json={"answers": {"Q-001": "200"}, "base_revision": 1},
    )
    await c.post(f"{base}/artifacts/requirements/revisions/2/approve", json={})
    await c.post(f"{base}/stages/architecture/runs", json={"mode": "generate"})
    await work()
    return base


async def test_missing_artifact_is_404(owner_client, project_id):
    r = await owner_client.get(f"/api/v1/projects/{project_id}/artifacts/architecture")
    assert r.status_code == 404


async def test_save_reload_conflict_compare_restore(owner_client, with_architecture):
    c, base = owner_client, with_architecture
    rev1 = (await c.get(f"{base}/artifacts/architecture/revisions/1")).json()
    content = copy.deepcopy(rev1["content"])
    content["nodes"][0]["label"] = "Customer portal"
    content["nodes"][0]["position"] = {"x": 999, "y": 5}
    r = await c.post(
        f"{base}/artifacts/architecture/revisions",
        json={"base_revision": 1, "content": content, "summary": "Renamed web"},
    )
    assert r.status_code == 201 and r.json()["revision"] == 2 and r.json()["origin"] == "user"

    reloaded = (await c.get(f"{base}/artifacts/architecture")).json()["revision"]["content"]
    assert reloaded["nodes"][0]["label"] == "Customer portal"
    assert reloaded["nodes"][0]["position"] == {"x": 999, "y": 5}

    stale = await c.post(
        f"{base}/artifacts/architecture/revisions",
        json={"base_revision": 1, "content": content, "summary": "x"},
    )
    assert stale.status_code == 409 and stale.json()["error"]["details"] == {"latest_revision": 2}

    d = (await c.get(f"{base}/artifacts/architecture/compare", params={"from": 1, "to": 2})).json()
    assert d["nodes"]["modified"][0]["fields"] == {
        "label": {"from": rev1["content"]["nodes"][0]["label"], "to": "Customer portal"}
    }

    r = await c.post(f"{base}/artifacts/architecture/revisions/1/restore")
    assert r.status_code == 201 and r.json()["revision"] == 3 and r.json()["restored_from"] == 1

    hist = (await c.get(f"{base}/artifacts/architecture/revisions")).json()
    assert [h["revision"] for h in hist] == [3, 2, 1] and "content" not in hist[0]


async def test_save_with_dangling_edge_is_422(owner_client, with_architecture):
    c, base = owner_client, with_architecture
    content = (await c.get(f"{base}/artifacts/architecture/revisions/1")).json()["content"]
    content["edges"].append(
        {
            "id": "e_bad",
            "source": "n_api",
            "target": "n_ghost",
            "label": "",
            "protocol": None,
            "data": None,
            "requirement_ids": [],
        }
    )
    r = await c.post(
        f"{base}/artifacts/architecture/revisions",
        json={"base_revision": 1, "content": content, "summary": "x"},
    )
    assert r.status_code == 422 and "no longer exists" in r.json()["error"]["message"]


async def test_validate_endpoint_does_not_write(owner_client, with_architecture):
    c, base = owner_client, with_architecture
    content = (await c.get(f"{base}/artifacts/architecture/revisions/1")).json()["content"]
    content["nodes"][1]["requirement_ids"] = ["FR-404"]
    r = await c.post(f"{base}/artifacts/architecture/validate", json={"content": content})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert any(i["code"] == "UNKNOWN_REQUIREMENT" for i in r.json()["issues"])
    assert (await c.get(f"{base}/artifacts/architecture")).json()["latest_revision"] == 1


async def test_approve_and_request_changes(owner_client, with_architecture):
    c, base = owner_client, with_architecture
    r = await c.post(
        f"{base}/artifacts/architecture/revisions/1/request-changes", json={"comment": "Add cache"}
    )
    assert r.status_code == 200 and r.json()["decision"] == "CHANGES_REQUESTED"
    wf = (await c.get(f"{base}/workflow")).json()
    assert wf[1]["status"] == "NEEDS_REVISION"
    r = await c.post(f"{base}/artifacts/architecture/revisions/1/approve", json={})
    assert r.status_code == 200
    assert (await c.get(f"{base}/workflow")).json()[1]["status"] == "APPROVED"


async def test_spec_change_marks_requirements_and_architecture_stale(
    owner_client, with_architecture, work
):
    c, base = owner_client, with_architecture
    await c.post(f"{base}/artifacts/architecture/revisions/1/approve", json={})
    await c.put(f"{base}/specification", json={"basics": {"name": "Portal v2"}})
    wf = {s["stage"]: s for s in (await c.get(f"{base}/workflow")).json()}
    assert wf["requirements"]["status"] == "STALE"
    assert "Specification changed" in wf["requirements"]["reasons"][0]
    # Re-run, answer, approve requirements → architecture becomes stale.
    await c.post(f"{base}/stages/requirements/runs", json={"mode": "revise"})
    await work()
    latest = (await c.get(f"{base}/artifacts/requirements")).json()["latest_revision"]
    r = await c.post(f"{base}/artifacts/requirements/revisions/{latest}/approve", json={})
    assert r.status_code == 200, r.text
    wf = {s["stage"]: s for s in (await c.get(f"{base}/workflow")).json()}
    assert wf["architecture"]["status"] == "STALE"
    assert "Requirements changed" in wf["architecture"]["reasons"][0]
