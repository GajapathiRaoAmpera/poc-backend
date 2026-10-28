import copy

from app.modules.agents.plan import prompts
from app.modules.artifacts import service as artifacts
from app.modules.conversations.service import recent_messages
from app.modules.ratecard.service import seed_default
from tests.agents.helpers import approved_requirements, good_architecture, run_stage, setup_project


def plan(**over):
    out = {
        "summary": "Two tasks.",
        "milestones": [{"id": "M-01", "title": "Build", "goal": "Ship it"}],
        "tasks": [
            {
                "id": "T-001",
                "title": "Upload",
                "milestone_id": "M-01",
                "role": "backend_engineer",
                "effort_days": 3,
                "depends_on": [],
                "requirement_ids": ["FR-001"],
                "component_ids": ["n_api"],
            },
            {
                "id": "T-002",
                "title": "A11y",
                "milestone_id": "M-01",
                "role": "frontend_engineer",
                "effort_days": 2.5,
                "depends_on": ["T-001"],
                "requirement_ids": ["NFR-001"],
                "component_ids": [],
            },
        ],
        "risks": [],
    }
    out.update(over)
    return out


async def ready(db):
    await seed_default(db)
    owner, project = await setup_project(db)
    await approved_requirements(db, project, owner)
    await run_stage(db, project, owner, "architecture", lambda *a: good_architecture())
    await artifacts.approve(db, project, "architecture", 1, owner, None)
    return owner, project


async def test_generate_saves_scheduled_revision(db):
    owner, project = await ready(db)
    run, _, doc = await run_stage(db, project, owner, "plan", lambda *a: plan())
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    rev = await artifacts.get_revision(db, project, "plan", 1)
    assert {s.kind for s in rev.sources} == {"requirements", "architecture"}
    assert all(t["start"] and t["end"] for t in rev.content["tasks"])
    assert rev.origin == "agent" and rev.validation.ok, rev.validation.issues
    covered = {r for t in rev.content["tasks"] for r in t["requirement_ids"]}
    assert {"FR-001", "NFR-001"} <= covered


async def test_invalid_then_repaired(db):
    owner, project = await ready(db)
    bad = plan()
    bad["tasks"][0]["depends_on"] = ["T-002"]
    outputs = [bad, plan()]
    _, provider, doc = await run_stage(db, project, owner, "plan", lambda *a: outputs.pop(0))
    assert doc["status"] == "SUCCEEDED" and len(provider.calls) == 2
    events = [e async for e in db.run_events.find({"run_id": doc["_id"]})]
    assert any("self-correcting (1/2)" in e["message"] for e in events)


async def test_reschedule_makes_no_llm_call(db):
    owner, project = await ready(db)
    await run_stage(db, project, owner, "plan", lambda *a: plan())
    first = await artifacts.get_revision(db, project, "plan", 1)
    project.preferences = {"start_date": "2030-01-07", "team_size": {"backend_engineer": 2}}
    await db.projects.update_one(
        {"_id": project.id}, {"$set": {"preferences": project.preferences}}
    )
    _, provider, doc = await run_stage(db, project, owner, "plan", None, mode="reschedule")
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    assert provider.calls == []
    rev = await artifacts.get_revision(db, project, "plan", 2)
    assert rev.origin == "system" and rev.summary == "Rescheduled with updated team or start date"

    def strip(c):
        return [{k: v for k, v in t.items() if k not in ("start", "end")} for t in c["tasks"]]

    assert strip(rev.content) == strip(first.content)
    assert rev.content["schedule"]["start"] == "2030-01-07"
    assert rev.content["schedule"]["start"] != first.content["schedule"]["start"]


async def test_chat_revise(db):
    owner, project = await ready(db)
    await run_stage(db, project, owner, "plan", lambda *a: plan())
    revised = plan(summary="Revised.")
    replies = [
        {"reply": "Revising.", "action": "revise", "instruction": "shorter"},
        revised,
        {"reply": "It is fine.", "action": "none", "instruction": None},
    ]
    _, provider, doc = await run_stage(
        db, project, owner, "plan", lambda *a: copy.deepcopy(replies.pop(0)),
        mode="chat", instruction="make it shorter",
    )  # fmt: skip
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    assert (await artifacts.get_revision(db, project, "plan", 2)).summary == "Revised."
    gen_prompt = provider.calls[1]["user"]
    assert "make it shorter" in gen_prompt or "shorter" in gen_prompt
    assert "<current_plan>" in gen_prompt and "T-001 | M-01 | backend_engineer" in gen_prompt
    msgs = await recent_messages(db, project, "plan")
    assert [(m.role, m.text) for m in msgs] == [("agent", "Revising.")]
    _, _, doc = await run_stage(
        db, project, owner, "plan", lambda *a: replies.pop(0), mode="chat", instruction="why?"
    )
    assert (await artifacts.get_artifact(db, project, "plan")).latest_revision == 2
    assert [m.text for m in await recent_messages(db, project, "plan")][-1] == "It is fine."


def test_prompt_is_compact():
    reqs = {
        "requirements": [
            {
                "id": f"FR-{i:03d}",
                "priority": "MUST",
                "title": f"Req {i} " + "x" * 120,
                "description": "SECRETDESC" * 20,
            }
            for i in range(40)
        ]
    }
    arch = {
        "nodes": [
            {"id": f"n_{i}", "type": "service", "label": f"Svc {i} " + "y" * 120} for i in range(25)
        ],
        "edges": [{"source": f"n_{i}", "target": f"n_{i + 1}"} for i in range(24)],
    }
    user = prompts.generate_prompt(prompts.digest(reqs, arch, ["backend_engineer"], {}))
    assert "SECRETDESC" not in user and len(user) < 12000


async def test_reschedule_with_invalid_stored_plan_regenerates(db):
    owner, project = await ready(db)
    bad = plan()
    bad["tasks"][0]["depends_on"] = ["T-002"]
    d = {"start": "2030-01-07", "end": "2030-01-08"}
    bad["tasks"] = [t | d for t in bad["tasks"]]
    bad["milestones"] = [m | d for m in bad["milestones"]]
    bad["schedule"] = d | {"working_days": 2, "target_date": None}
    bad["warnings"] = []
    req = await artifacts.get_artifact(db, project, "requirements")
    arch = await artifacts.get_artifact(db, project, "architecture")
    from app.modules.artifacts.models import SourceRef

    srcs = [
        SourceRef(kind="requirements", artifact_id=req.id, revision=req.approved_revision),
        SourceRef(kind="architecture", artifact_id=arch.id, revision=arch.approved_revision),
    ]
    await artifacts.add_revision(
        db, project, "plan", bad, sources=srcs, origin="agent", summary="x", created_by=None
    )
    _, provider, doc = await run_stage(
        db, project, owner, "plan", lambda *a: plan(), mode="reschedule"
    )
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    assert len(provider.calls) == 1 and "Dependency cycle" in provider.calls[0]["user"]
    rev = await artifacts.get_revision(db, project, "plan", 2)
    assert rev.origin == "agent" and rev.validation.ok


async def test_chat_revise_keeps_timeline_pins(db):
    from app.modules.agents.plan.edits import TaskEdit, apply_edits
    from app.modules.agents.plan.schema import PlanContent

    owner, project = await ready(db)
    await run_stage(db, project, owner, "plan", lambda *a: plan())
    first = await artifacts.get_revision(db, project, "plan", 1)
    pinned = apply_edits(
        PlanContent.model_validate(first.content),
        [TaskEdit(task_id="T-001", not_before="2030-01-07")],
        {},
    )
    await artifacts.add_revision(
        db, project, "plan", pinned.model_dump(mode="json"), sources=first.sources, origin="user",
        summary="pin", created_by=owner.id, base_revision=1,
    )  # fmt: skip
    replies = [{"reply": "Revising.", "action": "revise", "instruction": "x"}, plan(summary="R")]
    _, _, doc = await run_stage(
        db, project, owner, "plan", lambda *a: copy.deepcopy(replies.pop(0)),
        mode="chat", instruction="tweak",
    )  # fmt: skip
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    rev = await artifacts.get_revision(db, project, "plan", 3)
    task = next(t for t in rev.content["tasks"] if t["id"] == "T-001")
    assert task["not_before"] == "2030-01-07" and task["start"] >= "2030-01-07"
