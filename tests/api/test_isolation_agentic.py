import uuid

import pytest

from tests.factories import login, make_user

ROUTES = [
    ("get", "/workflow", None),
    ("post", "/stages/requirements/runs", {"mode": "generate"}),
    ("get", "/runs", None),
    ("get", "/runs/{rid}", None),
    ("post", "/runs/{rid}/cancel", None),
    ("post", "/runs/{rid}/retry", None),
    ("get", "/events?max_wait=0.1", None),
    ("get", "/artifacts/requirements", None),
    ("get", "/artifacts/requirements/revisions", None),
    ("get", "/artifacts/requirements/revisions/1", None),
    (
        "post",
        "/artifacts/requirements/revisions",
        {"base_revision": 1, "content": {}, "summary": "x"},
    ),
    ("post", "/artifacts/requirements/validate", {"content": {}}),
    ("post", "/artifacts/requirements/revisions/1/restore", None),
    ("post", "/artifacts/requirements/revisions/1/approve", {}),
    ("post", "/artifacts/requirements/revisions/1/request-changes", {}),
    ("put", "/artifacts/requirements/answers", {"answers": {}, "base_revision": 1}),
    ("get", "/artifacts/architecture/compare?from=1&to=2", None),
    (
        "post",
        "/artifacts/architecture/change-requests",
        {"instruction": "Add a cache", "base_revision": 1},
    ),
    ("get", "/change-sets", None),
    ("get", "/change-sets/{cid}", None),
    ("post", "/change-sets/{cid}/apply", None),
    ("post", "/change-sets/{cid}/reject", None),
    ("get", "/approvals", None),
    ("get", "/delivery", None),
    ("post", "/delivery/resume", None),
    ("post", "/delivery/cancel", None),
    ("get", "/plans", None),
    ("get", "/plans/1", None),
    ("put", "/preferences", {"deployment_preference": "Azure"}),
    ("get", "/conversations/intake/messages", None),
    ("post", "/conversations/intake/messages", {"text": "hi"}),
    ("post", "/conversations/requirements/messages", {"text": "hi"}),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES, ids=[f"{m} {p}" for m, p, _ in ROUTES])
async def test_user_b_404_on_every_agentic_route(
    owner_client, project_id, make_client, db, work, method, path, body
):
    base = f"/api/v1/projects/{project_id}"
    run = (
        await owner_client.post(f"{base}/stages/requirements/runs", json={"mode": "generate"})
    ).json()
    await work()
    await make_user(db, email="bob@x.com")
    bob = make_client()
    await login(bob, "bob@x.com")
    url = base + path.format(rid=run["id"], cid=uuid.uuid4())
    kwargs = {"json": body} if body is not None else {}
    r = await getattr(bob, method)(url, **kwargs)
    assert r.status_code == 404, r.text
    assert "Portal" not in r.text
