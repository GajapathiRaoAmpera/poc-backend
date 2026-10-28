from app.modules.agents.code.render import render, sha256
from app.modules.agents.code.schema import CodeContent
from app.modules.artifacts import service as artifacts
from app.modules.ratecard.service import seed_default
from tests.agents.helpers import approved_requirements, good_architecture, run_stage, setup_project
from tests.agents.test_plan_agent import plan as plan_out
from tests.delivery.conftest import scripted

BAD_DEV = '#!/usr/bin/env bash\ncd $1\necho "ok"\n'
GOOD_DEV = '#!/usr/bin/env bash\nset -euo pipefail\ncd "$1" || exit 1\necho "ok"\n'


def code(dev=GOOD_DEV, **over):
    out = {
        "summary": "API and web stubs.",
        "project_slug": "client-portal",
        "files": [
            {"path": "README.md", "content": "# Client portal\n"},
            {"path": "api/main.py", "content": "print('health')\n"},
            {"path": "scripts/dev.sh", "content": dev, "executable": True},
        ],
        "next_steps": ["cd api && uv sync"],
    }
    out.update(over)
    return out


def fix(path="scripts/dev.sh", content=GOOD_DEV):
    return {"summary": "Quoted.", "changes": [{"op": "write", "path": path, "content": content}]}


async def ready(db):
    await seed_default(db)
    owner, project = await setup_project(db)
    await approved_requirements(db, project, owner)
    await run_stage(db, project, owner, "architecture", lambda *a: good_architecture())
    await artifacts.approve(db, project, "architecture", 1, owner, None)
    await run_stage(db, project, owner, "plan", lambda *a: plan_out())
    await artifacts.approve(db, project, "plan", 1, owner, None)
    return owner, project


async def test_generate_passes(db):
    owner, project = await ready(db)
    _, provider, doc = await run_stage(db, project, owner, "code", scripted(code_output=code()))
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    assert [c["schema_name"] for c in provider.calls] == ["code_output"]
    rev = await artifacts.get_revision(db, project, "code", 1)
    assert {s.kind for s in rev.sources} == {"architecture", "plan"}
    content = CodeContent.model_validate(rev.content)
    assert content.report.ok and rev.validation.ok, content.report.findings
    plan_file = next(f for f in content.files if f.path == "PLAN.md")
    assert plan_file.generated and "- [ ] T-001" in plan_file.content
    script = render(content, {s.kind: s.revision for s in rev.sources})
    assert content.script_sha256 == sha256(script)


async def test_repair_fixes_only_failing_file(db):
    owner, project = await ready(db)
    script = scripted(code_output=code(BAD_DEV), code_changes=fix())
    _, provider, doc = await run_stage(db, project, owner, "code", script)
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    assert [c["schema_name"] for c in provider.calls] == ["code_output", "code_changes"]
    repair = provider.calls[1]["user"]
    assert "scripts/dev.sh" in repair and "cd $1" in repair
    assert "print('health')" not in repair and "# Client portal" not in repair
    rev = await artifacts.get_revision(db, project, "code", 1)
    content = CodeContent.model_validate(rev.content)
    assert content.report.ok, content.report.findings
    assert next(f for f in content.files if f.path == "scripts/dev.sh").content == GOOD_DEV


async def test_repair_out_of_scope_is_rejected(db):
    owner, project = await ready(db)
    script = scripted(code_output=code(BAD_DEV), code_changes=fix(path="README.md"))
    _, provider, doc = await run_stage(db, project, owner, "code", script)
    assert doc["status"] == "SUCCEEDED"
    rev = await artifacts.get_revision(db, project, "code", 1)
    assert not rev.validation.ok


async def test_repair_gives_up_after_two_rounds(db):
    owner, project = await ready(db)
    script = scripted(code_output=code(BAD_DEV), code_changes=fix(content=BAD_DEV))
    _, provider, doc = await run_stage(db, project, owner, "code", script)
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    assert [c["schema_name"] for c in provider.calls] == ["code_output"] + ["code_changes"] * 2
    rev = await artifacts.get_revision(db, project, "code", 1)
    assert not rev.validation.ok
    assert any(i.path == "scripts/dev.sh" for i in rev.validation.issues)


async def test_invalid_manifest_is_repaired_by_schema_loop(db):
    owner, project = await ready(db)
    outputs = iter([code(files=[{"path": "../evil", "content": "x"}]), code()])
    _, provider, doc = await run_stage(
        db, project, owner, "code", scripted(code_output=lambda s, u: next(outputs))
    )
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    assert "../evil" in provider.calls[1]["user"]


async def test_chat_revise_applies_changes(db):
    owner, project = await ready(db)
    await run_stage(db, project, owner, "code", scripted(code_output=code()))
    _, provider, doc = await run_stage(
        db, project, owner, "code", scripted(code_output=code()), mode="chat",
        instruction="Add NOTES.md describing the API",
    )  # fmt: skip
    assert doc["status"] == "SUCCEEDED", doc.get("error")
    assert [c["schema_name"] for c in provider.calls] == ["code_chat_output", "code_changes"]
    rev = await artifacts.get_revision(db, project, "code", 2)
    paths = {f["path"] for f in rev.content["files"]}
    assert {"NOTES.md", "README.md", "PLAN.md", "scripts/dev.sh"} <= paths
    assert rev.validation.ok
