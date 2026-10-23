def _parse(body: str):
    events = []
    for block in body.split("\n\n"):
        lines = dict(
            line.split(": ", 1)
            for line in block.splitlines()
            if ": " in line and not line.startswith(":")
        )
        if "id" in lines:
            events.append((int(lines["id"]), lines.get("event")))
    return events


async def test_sse_streams_events_after_cursor(owner_client, project_id, work):
    base = f"/api/v1/projects/{project_id}"
    await owner_client.post(f"{base}/stages/requirements/runs", json={"mode": "generate"})
    await work()
    r = await owner_client.get(f"{base}/events", params={"after": 0, "max_wait": 0.3})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _parse(r.text)
    seqs = [s for s, _ in events]
    assert seqs == sorted(seqs) and len(seqs) >= 4 and all(e == "run_event" for _, e in events)


async def test_sse_resumes_from_last_event_id_without_duplicates(owner_client, project_id, work):
    base = f"/api/v1/projects/{project_id}"
    await owner_client.post(f"{base}/stages/requirements/runs", json={"mode": "generate"})
    first = _parse((await owner_client.get(f"{base}/events", params={"max_wait": 0.2})).text)
    last = first[-1][0]
    await work()
    second = _parse(
        (
            await owner_client.get(
                f"{base}/events", params={"max_wait": 0.3}, headers={"Last-Event-ID": str(last)}
            )
        ).text
    )
    assert second and min(s for s, _ in second) == last + 1
    assert not {s for s, _ in first} & {s for s, _ in second}
