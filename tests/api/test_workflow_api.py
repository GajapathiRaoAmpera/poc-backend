async def test_full_flow_over_http(owner_client, project_id, work):
    c, base = owner_client, f"/api/v1/projects/{project_id}"

    wf = (await c.get(f"{base}/workflow")).json()
    assert [s["stage"] for s in wf] == ["requirements", "architecture", "plan", "budget", "code"]
    assert wf[0]["status"] == "NOT_STARTED" and wf[1]["status"] == "BLOCKED"

    r = await c.post(f"{base}/stages/requirements/runs", json={"mode": "generate"})
    assert r.status_code == 202
    run = r.json()
    assert run["status"] == "QUEUED" and run["agent"] == "requirements_analyst"
    again = await c.post(f"{base}/stages/requirements/runs", json={"mode": "generate"})
    assert again.status_code == 200 and again.json()["id"] == run["id"]

    assert await work()
    detail = (await c.get(f"{base}/runs/{run['id']}")).json()
    assert (
        detail["status"] == "SUCCEEDED"
        and detail["is_mock"] is True
        and detail["provider"] == "mock"
    )
    assert [e["seq"] for e in detail["events"]] == sorted(e["seq"] for e in detail["events"])
    assert (await c.get(f"{base}/workflow")).json()[0]["status"] == "WAITING_FOR_INPUT"

    art = (await c.get(f"{base}/artifacts/requirements")).json()
    assert (
        art["latest_revision"] == 1
        and art["revision"]["content"]["open_questions"][0]["id"] == "Q-001"
    )
    r = await c.put(
        f"{base}/artifacts/requirements/answers",
        json={"answers": {"Q-001": "About 200 staff"}, "base_revision": 1},
    )
    assert r.status_code == 201 and r.json()["revision"] == 2

    r = await c.post(
        f"{base}/artifacts/requirements/revisions/2/approve", json={"comment": "Looks right"}
    )
    assert r.status_code == 200 and r.json()["decision"] == "APPROVED"
    assert (await c.get(f"{base}/workflow")).json()[1]["status"] == "NOT_STARTED"

    run = (await c.post(f"{base}/stages/architecture/runs", json={"mode": "generate"})).json()
    assert await work()
    assert (await c.get(f"{base}/runs/{run['id']}")).json()["status"] == "SUCCEEDED"
    arch = (await c.get(f"{base}/artifacts/architecture")).json()
    assert arch["latest_revision"] == 1

    runs = (await c.get(f"{base}/runs")).json()
    assert runs["total"] == 2 and runs["items"][0]["stage"] == "architecture"
    approvals = (await c.get(f"{base}/approvals")).json()
    assert [a["decision"] for a in approvals] == ["APPROVED"]


async def test_stage_blocked_and_unknown_stage(owner_client, project_id):
    base = f"/api/v1/projects/{project_id}"
    r = await owner_client.post(f"{base}/stages/architecture/runs", json={"mode": "generate"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "STAGE_BLOCKED"
    assert r.json()["error"]["details"]["reasons"]
    r = await owner_client.post(f"{base}/stages/plan/runs", json={"mode": "generate"})
    assert r.status_code == 409
    r = await owner_client.post(f"{base}/stages/nonsense/runs", json={"mode": "generate"})
    assert r.status_code == 422


async def test_cancel_and_retry(owner_client, project_id):
    base = f"/api/v1/projects/{project_id}"
    run = (
        await owner_client.post(f"{base}/stages/requirements/runs", json={"mode": "generate"})
    ).json()
    r = await owner_client.post(f"{base}/runs/{run['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    r = await owner_client.post(f"{base}/runs/{run['id']}/retry")
    assert r.status_code == 202 and r.json()["retry_of"] == run["id"]
