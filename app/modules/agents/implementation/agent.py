import json
import logging
from dataclasses import dataclass

from app.modules.agents.base import AgentContext, AgentOutcome, OutputInvalid
from app.modules.agents.code import agent as code_agent
from app.modules.agents.code.schema import CodeAgentOutput, CodeFile
from app.modules.agents.code.validate import manifest_issues
from app.modules.agents.implementation import prompts
from app.modules.agents.implementation.schema import Health, ImplementationContent
from app.modules.agents.structured import call_structured
from app.modules.agents.subtask import subtask
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import SourceRef, ValidationIssue
from app.modules.sandbox import service as sandbox
from app.modules.sandbox.spec import SandboxSpec, missing_dirs

logger = logging.getLogger(__name__)

NAME = "implementer"
REPAIR_ROUNDS = 2
SANDBOX_FILE = "sandbox.json"
MANIFESTS = ("package.json", "pyproject.toml", "requirements.txt")


def components(files: list[CodeFile]) -> list[str]:
    """Top-level folders that contain a dependency manifest."""
    return sorted(
        {
            f.path.split("/")[0]
            for f in files
            if f.path.count("/") == 1 and f.path.rsplit("/", 1)[1] in MANIFESTS
        }
    )


@dataclass
class Inputs:
    refs: list[SourceRef]
    code: dict
    design: dict
    arch: dict
    reqs: dict
    base: dict | None  # the latest implementation revision (chat mode)

    @property
    def sources(self) -> list[SourceRef]:
        return [r for r in self.refs if r.kind != "implementation"]

    def digest(self) -> str:
        return prompts.digest(self.reqs, self.arch, self.design)


async def load_inputs(ctx: AgentContext) -> Inputs:
    refs = {s.kind: s for s in ctx.run.inputs}

    async def get(kind: str) -> dict:
        return (
            await artifacts.get_revision(ctx.db, ctx.project, kind, refs[kind].revision)
        ).content

    base = await get("implementation") if "implementation" in refs else None
    return Inputs(
        ctx.run.inputs,
        await get("code"),
        await get("design"),
        await get("architecture"),
        await get("requirements"),
        base,
    )


def _under(files: list[CodeFile], folder: str) -> list[CodeFile]:
    return [f for f in files if f.path.startswith(folder + "/")]


def _probe(files: list[CodeFile]) -> CodeAgentOutput:
    # model_construct: the count cap is manifest_issues' job (an issue, not a ValidationError)
    return CodeAgentOutput.model_construct(summary="", project_slug="probe", files=files)


def _with_spec(files: list[CodeFile], spec: SandboxSpec) -> list[CodeFile]:
    text = json.dumps(spec.model_dump(), indent=2) + "\n"
    return [f for f in files if f.path != SANDBOX_FILE] + [
        CodeFile(path=SANDBOX_FILE, content=text)
    ]


def _manifest_of(files: list[CodeFile], folder: str) -> str:
    return next((m for m in MANIFESTS if any(f.path == f"{folder}/{m}" for f in files)), "")


async def _start(ctx: AgentContext, files: list[CodeFile], spec: SandboxSpec) -> list[Health]:
    for s in spec.services:
        await subtask(ctx, f"service:{s.name}", f"{s.name} health", "running", f"Starting {s.name}")
    try:
        state = await sandbox.start(ctx.db, ctx.project, files, spec)
        services = [(s.name, s.healthy, s.error) for s in state.services]
    except Exception as exc:  # a missing Docker must block approval, not crash the run
        logger.warning("Sandbox start failed: %s", exc)
        services = [(s.name, False, f"Sandbox unavailable: {exc}") for s in spec.services]
    health = []
    for name, healthy, error in services:
        excerpt = ""
        if not healthy:
            try:
                excerpt = await sandbox.logs(ctx.project.id, name)
            except Exception:  # no container logs: fall back to the start error
                excerpt = ""
            excerpt = (excerpt or error or "")[-prompts.MAX_LOGS :]
        health.append(Health(service=name, healthy=healthy, log_excerpt=excerpt))
        status = "passed" if healthy else "failed"
        await subtask(ctx, f"service:{name}", f"{name} health", status, f"{name}: {status}")
    return health


async def implement(
    ctx: AgentContext, ins: Inputs, files: list[CodeFile], direction: str | None, usage: dict
) -> tuple[list[CodeFile], list[str]]:
    """One request_changes call per component, scoped to its folder."""
    out, done = _probe(files), []
    for folder in components(files):
        sid = f"component:{folder}"
        await subtask(ctx, sid, folder, "running", f"Implementing {folder}/")
        prompt = prompts.component_prompt(
            ins.digest(), folder, _under(out.files, folder), direction
        )
        try:
            out, _, used = await code_agent.request_changes(ctx, out, prompt, {folder + "/"})
        except Exception:
            await subtask(ctx, sid, folder, "failed", f"Could not implement {folder}/")
            raise
        await subtask(ctx, sid, folder, "passed", f"Implemented {folder}/")
        code_agent._add_usage(usage, used)
        done.append(folder)
    return out.files, done


async def make_spec(ctx: AgentContext, files: list[CodeFile], usage: dict) -> SandboxSpec:
    folders = [(c, _manifest_of(files, c)) for c in components(files)]
    spec, used = await call_structured(
        ctx,
        schema_model=SandboxSpec,
        schema_name="sandbox_spec",
        system=prompts.SANDBOX_SYSTEM,
        user=prompts.sandbox_prompt(folders),
        validate=lambda s: [
            ValidationIssue(severity="error", code="SANDBOX_DIR", message=f"No such folder: {d}")
            for d in missing_dirs(s, files)
        ],
    )
    code_agent._add_usage(usage, used)
    return spec


async def repair(
    ctx: AgentContext, files: list[CodeFile], spec: SandboxSpec, health: list[Health], usage: dict
) -> list[CodeFile]:
    """One request_changes call per failing service: its logs and its folder only."""
    out = _probe(files)
    by_name = {s.name: s for s in spec.services}
    for h in (h for h in health if not h.healthy):
        svc = by_name[h.service]
        prompt = prompts.repair_prompt(svc.name, svc.dir, _under(out.files, svc.dir), h.log_excerpt)
        try:
            out, _, used = await code_agent.request_changes(ctx, out, prompt, {svc.dir + "/"})
        except OutputInvalid:
            continue
        code_agent._add_usage(usage, used)
    return out.files


async def save(ctx, ins, files, spec, health, done, summary):
    content = ImplementationContent(
        summary=summary, files=files, sandbox=spec, health=health, components_done=done
    )
    rev = await artifacts.add_revision(
        ctx.db,
        ctx.project,
        "implementation",
        content.model_dump(mode="json"),
        sources=ins.sources,  # all transitive upstream kinds, for the staleness cascade
        origin="agent",
        summary=summary,
        created_by=None,
        run_id=ctx.run.id,
    )
    await ctx.emit(
        "step",
        f"Saved implementation revision {rev.revision} ({len(files)} files)",
        {"artifact_id": str(rev.artifact_id), "revision": rev.revision},
    )
    return {"artifact_id": str(rev.artifact_id), "revision": rev.revision}


async def generate(ctx: AgentContext, ins: Inputs, base: list[CodeFile] | None = None):
    """Implement every component (from the scaffold, or `base`), start the sandbox, repair, save."""
    usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    start = base or [CodeFile.model_validate(f) for f in ins.code["files"]]
    files, done = await implement(ctx, ins, start, ctx.run.instruction, usage)
    spec = await make_spec(ctx, files, usage)
    files = _with_spec(files, spec)
    health = await _start(ctx, files, spec)
    for round_ in range(1, REPAIR_ROUNDS + 1):
        if all(h.healthy for h in health):
            break
        bad = ", ".join(h.service for h in health if not h.healthy)
        await ctx.emit("validation", f"Unhealthy: {bad} - repairing (round {round_})", None)
        files = await repair(ctx, files, spec, health, usage)
        health = await _start(ctx, files, spec)
    ok = all(h.healthy for h in health)
    summary = f"Implemented {len(done)} component(s); " + (
        "all services healthy." if ok else "some services are unhealthy."
    )
    output = await save(ctx, ins, files, spec, health, done, summary)
    return AgentOutcome(output=output, summary=summary, usage=usage)


async def run(ctx: AgentContext) -> AgentOutcome:
    ins = await load_inputs(ctx)
    await ctx.emit("step", f"Loaded the approved scaffold ({len(ins.code['files'])} files)", None)
    if ctx.run.mode == "chat":
        from app.modules.agents.implementation import chat

        return await chat.run(ctx, ins)
    return await generate(ctx, ins)


def save_issues(content: ImplementationContent) -> list[ValidationIssue]:
    return manifest_issues(_probe(content.files), allow_generated=True)
