import copy

from app.modules.agents.budget import prompts
from app.modules.agents.budget.calc import compute_budget
from app.modules.agents.budget.schema import BudgetAgentOutput, BudgetContent
from app.modules.agents.plan.schema import PlanContent
from app.modules.artifacts import service as artifacts
from app.modules.conversations.service import recent_messages
from app.modules.projects.spec_schema import Budget
from app.modules.ratecard.models import RateCard
from app.modules.ratecard.service import effective, seed_default, snapshot
from tests.agents.helpers import approved_requirements, good_architecture, run_stage, setup_project
from tests.agents.test_plan_agent import plan as plan_out


def budget(**over):
    out = {
        "summary": "Hosting and a licence.",
        "items": [
            {
                "component_id": "n_api",
                "category": "infra",
                "description": "Hosting",
                "recurrence": "monthly",
                "amount": 100,
            }
        ],
        "contingency_pct": 10,
        "contingency_reason": "Covers R-01",
    }
    out.update(over)
    return out


async def ready(db):
    await seed_default(db)
    owner, project = await setup_project(db)
    await approved_requirements(db, project, owner)
    await run_stage(db, project, owner, "architecture", lambda *a: good_architecture())
    await artifacts.approve(db, project, "architecture", 1, owner, None)
    risk = {"id": "R-01", "description": "Slip", "impact": "high", "mitigation": "Buffer"}
    await run_stage(db, project, owner, "plan", lambda *a: plan_out(risks=[risk]))
    await artifacts.approve(db, project, "plan", 1, owner, None)
    return owner, project


async def set_card(db, project, **over):
    card = (await effective(db, project)).model_dump(mode="json") | over
    await db.projects.update_one({"_id": project.id}, {"$set": {"rate_card": card}})


async def test_generate(db):
    owner, project = await ready(db)
    run, _, doc = await run_stage(db, project, owner, "budget", lambda *a: budget())
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    rev = await artifacts.get_revision(db, project, "budget", 1)
    assert {s.kind for s in rev.sources} == {"plan", "architecture"}
    assert rev.origin == "agent" and rev.validation.ok, rev.validation.issues
    card = await effective(db, project)
    assert rev.content["rate_card"] == snapshot(card)
    plan = PlanContent.model_validate(
        (await artifacts.get_revision(db, project, "plan", 1)).content
    )
    expect = compute_budget(plan, card, BudgetAgentOutput.model_validate(budget()), None)
    assert BudgetContent.model_validate(rev.content).total == expect.total


async def test_reprice_no_llm(db):
    owner, project = await ready(db)
    await run_stage(db, project, owner, "budget", lambda *a: budget())
    first = await artifacts.get_revision(db, project, "budget", 1)
    await set_card(
        db,
        project,
        roles=[
            {"role": "backend_engineer", "hourly_rate": "100"},
            {"role": "frontend_engineer", "hourly_rate": "100"},
        ],
    )
    _, provider, doc = await run_stage(db, project, owner, "budget", None, mode="reprice")
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    assert provider.calls == []
    rev = await artifacts.get_revision(db, project, "budget", 2)
    assert rev.origin == "system" and rev.summary == "Repriced with updated rates"
    assert rev.content["items"] == first.content["items"]
    assert rev.content["contingency_pct"] == first.content["contingency_pct"]
    assert rev.content["contingency_reason"] == first.content["contingency_reason"]
    assert rev.content["labour"]["total"] != first.content["labour"]["total"]
    assert rev.content["rate_card"] != first.content["rate_card"]


async def test_reprice_on_hours_per_day_only(db):
    owner, project = await ready(db)
    await run_stage(db, project, owner, "budget", lambda *a: budget())
    first = await artifacts.get_revision(db, project, "budget", 1)
    await set_card(db, project, hours_per_day="6")
    _, provider, doc = await run_stage(db, project, owner, "budget", None, mode="reprice")
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    assert provider.calls == []
    rev = await artifacts.get_revision(db, project, "budget", 2)
    assert rev.content["total"] != first.content["total"]


async def test_reprice_with_invalid_items_regenerates(db):
    owner, project = await ready(db)
    await run_stage(
        db, project, owner, "budget", lambda *a: budget(),
    )  # fmt: skip
    rev1 = await artifacts.get_revision(db, project, "budget", 1)
    bad = copy.deepcopy(rev1.content)
    bad["items"][0]["component_id"] = "n_gone"
    await db.artifact_revisions.update_one(
        {"project_id": project.id, "kind": "budget", "revision": 1}, {"$set": {"content": bad}}
    )
    _, provider, doc = await run_stage(
        db, project, owner, "budget", lambda *a: budget(), mode="reprice"
    )
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    assert len(provider.calls) == 1 and "n_gone" in provider.calls[0]["user"]
    assert (await artifacts.get_revision(db, project, "budget", 2)).origin == "agent"


async def test_reprice_after_plan_change_regenerates(db):
    owner, project = await ready(db)
    await run_stage(db, project, owner, "budget", lambda *a: budget())
    plan1 = await artifacts.get_revision(db, project, "plan", 1)
    await artifacts.add_revision(
        db, project, "plan", plan1.content, sources=plan1.sources, origin="user",
        summary="edit", created_by=owner.id, base_revision=1,
    )  # fmt: skip
    await artifacts.approve(db, project, "plan", 2, owner, None)
    _, provider, doc = await run_stage(
        db, project, owner, "budget", lambda *a: budget(), mode="reprice"
    )
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    assert len(provider.calls) == 1  # regenerated, not repriced
    rev = await artifacts.get_revision(db, project, "budget", 2)
    assert rev.origin == "agent" and {s.kind: s.revision for s in rev.sources}["plan"] == 2


async def test_chat_revise_and_none(db):
    owner, project = await ready(db)
    await run_stage(db, project, owner, "budget", lambda *a: budget())
    replies = [
        {"reply": "Revising.", "action": "revise", "instruction": "cut hosting"},
        budget(summary="Revised.", items=[]),
        {"reply": "It is fine.", "action": "none", "instruction": None},
    ]
    _, provider, doc = await run_stage(
        db, project, owner, "budget", lambda *a: copy.deepcopy(replies.pop(0)),
        mode="chat", instruction="cut hosting",
    )  # fmt: skip
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    assert (await artifacts.get_revision(db, project, "budget", 2)).summary == "Revised."
    assert "<current_budget>" in provider.calls[1]["user"]
    assert [m.text for m in await recent_messages(db, project, "budget")] == ["Revising."]
    await run_stage(
        db, project, owner, "budget", lambda *a: replies.pop(0), mode="chat", instruction="why?"
    )
    assert (await artifacts.get_artifact(db, project, "budget")).latest_revision == 2
    assert [m.text for m in await recent_messages(db, project, "budget")][-1] == "It is fine."


def test_prompt_compact():
    d = "2026-11-02"
    tasks = [
        {
            "id": f"T-{i:03d}", "title": "SECRETTITLE" * 8, "milestone_id": "M-01",
            "role": "backend_engineer", "effort_days": 2, "component_ids": [f"n_{i % 25}"],
            "start": d, "end": d,
        }
        for i in range(60)
    ]  # fmt: skip
    plan = {
        "milestones": [{"id": "M-01", "title": "x"}],
        "tasks": tasks,
        "risks": [{"id": "R-01", "description": "r" * 300, "impact": "high"}],
        "schedule": {"working_days": 50},
    }
    arch = {
        "nodes": [
            {"id": f"n_{i}", "type": "service", "label": f"Svc {i}", "technology": "Go"}
            for i in range(25)
        ]
    }
    card = RateCard.model_validate(
        {"currency": "USD", "hours_per_day": 8,
         "roles": [{"role": "backend_engineer", "hourly_rate": 65}]}
    )  # fmt: skip
    user = prompts.generate_prompt(prompts.digest(plan, arch, card, Budget(max=1000)))
    assert "SECRETTITLE" not in user and len(user) < 8000
    assert "n_0 | service | Svc 0 | Go | 6\n" in user  # 3 tasks x 2 days
