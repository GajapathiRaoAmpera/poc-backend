import logging
from dataclasses import dataclass

from app.modules import mcp
from app.modules.agents.base import AgentContext, AgentOutcome, OutputInvalid
from app.modules.agents.code.schema import CodeFile
from app.modules.agents.implementation.schema import ImplementationContent
from app.modules.agents.structured import call_structured
from app.modules.agents.subtask import subtask
from app.modules.agents.testing import prompts, tools
from app.modules.agents.testing.schema import (
    MAX_STEPS,
    CaseResult,
    Step,
    TestCase,
    TestPlan,
    TestReport,
)
from app.modules.agents.testing.validate import coverage_issues, must_ids
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import SourceRef
from app.modules.media import service as media
from app.modules.sandbox import service as sandbox

logger = logging.getLogger(__name__)

NAME = "tester"


@dataclass
class Inputs:
    refs: list[SourceRef]
    impl: ImplementationContent
    impl_sources: list[SourceRef]
    reqs: dict
    design: dict

    @property
    def sources(self) -> list[SourceRef]:
        # all transitive upstream kinds, for the staleness cascade
        by_kind = {s.kind: s for s in [*self.impl_sources, *self.refs]}
        return list(by_kind.values())


async def load_inputs(ctx: AgentContext) -> Inputs:
    refs = {s.kind: s for s in ctx.run.inputs}

    async def get(kind: str):
        return await artifacts.get_revision(ctx.db, ctx.project, kind, refs[kind].revision)

    impl = await get("implementation")
    keep = [refs[k] for k in ("implementation", "requirements", "design")]
    return Inputs(
        keep,
        ImplementationContent.model_validate(impl.content),
        impl.sources,
        (await get("requirements")).content,
        (await get("design")).content,
    )


async def ensure_sandbox(ctx: AgentContext, impl: ImplementationContent) -> tuple[set[str], str]:
    """(sandbox origins, error). Starts the sandbox from the approved implementation if stopped."""
    try:
        state = await sandbox.status(ctx.project.id)
        if state.status != "healthy":
            files = [CodeFile.model_validate(f) for f in impl.files]
            state = await sandbox.start(ctx.db, ctx.project, files, impl.sandbox)
    except Exception as exc:
        return set(), f"The sandbox could not be started: {str(exc)[:300]}"
    if state.status != "healthy":
        bad = ", ".join(s.name for s in state.services if not s.healthy) or state.status
        return set(), f"The sandbox is not healthy ({bad})"
    return {o for s in state.services if s.url and (o := tools.origin(s.url))}, ""


def _fail(case: TestCase, evidence: str, steps: int = 0, shot: str | None = None) -> CaseResult:
    return CaseResult(
        **case.model_dump(),
        passed=False,
        evidence=evidence[:3000],
        screenshot_media_id=shot,
        steps_used=steps,
    )


async def _shot(ctx: AgentContext, browser) -> str | None:
    try:
        return await media.put(ctx.db, ctx.project, await browser.screenshot(), "image/png")
    except Exception as exc:  # the verdict stands without a screenshot
        logger.warning("Final screenshot failed: %s", exc)
        return None


async def run_case(
    ctx: AgentContext, browser, case: TestCase, origins: set[str], usage: dict
) -> CaseResult:
    history: list[tuple[str, str]] = []
    result: CaseResult | None = None
    for n in range(1, MAX_STEPS + 1):
        try:
            step, used = await call_structured(
                ctx,
                schema_model=Step,
                schema_name="test_step",
                system=prompts.STEP_SYSTEM,
                user=prompts.step_prompt(case, origins, history),
                validate=lambda s: [],
                max_output_tokens=1000,
            )
        except OutputInvalid:
            result = _fail(case, "The tester produced an invalid step", n)
            break
        for k, v in used.items():
            usage[k] = usage.get(k, 0) + v
        if step.tool == "report_result":
            passed = step.args.get("passed", "").lower() == "true"
            evidence = step.args.get("evidence", "")[:3000]
            result = CaseResult(**case.model_dump(), passed=passed, evidence=evidence, steps_used=n)
            break
        out = await tools.run_tool(step, browser, origins)
        history.append((f"{step.tool} {step.args}"[:300], out))
    if result is None:
        result = _fail(case, f"No verdict within {MAX_STEPS} steps", MAX_STEPS)
    result.screenshot_media_id = await _shot(ctx, browser)
    return result


async def _report(ctx: AgentContext, r: CaseResult) -> CaseResult:
    status = "passed" if r.passed else "failed"
    await subtask(ctx, r.id, r.title, status, f"{r.id} {status}")
    return r


async def execute(
    ctx: AgentContext, plan: TestPlan, origins: set[str], error: str, usage: dict
) -> list[CaseResult]:
    """Run every case in one browser session; any setup failure fails the cases with evidence."""
    if error:
        return [await _report(ctx, _fail(c, error)) for c in plan.cases]
    results: list[CaseResult] = []
    cm = mcp.get_browser()
    try:
        browser = await cm.__aenter__()
    except Exception as exc:  # only a failed start fails the cases; other errors fail the run
        logger.warning("Browser start failed: %s", exc)
        msg = f"The test browser could not be started: {str(exc)[:300]}"
        return [await _report(ctx, _fail(c, msg)) for c in plan.cases]
    try:
        for case in plan.cases:
            await subtask(ctx, case.id, case.title, "running", f"Testing {case.id}: {case.title}")
            results.append(await _report(ctx, await run_case(ctx, browser, case, origins, usage)))
    finally:
        try:
            await cm.__aexit__(None, None, None)
        except Exception as exc:  # a close error must not discard the results
            logger.warning("Browser close failed: %s", exc)
    return results


def build_report(plan: TestPlan, results: list[CaseResult], reqs: dict) -> TestReport:
    must = must_ids(reqs)
    failed = [r for r in results if not r.passed]
    return TestReport(
        summary=f"{len(results) - len(failed)} of {len(results)} test cases passed.",
        results=results,
        passed=len(results) - len(failed),
        failed=len(failed),
        must_failed=sorted({r.requirement_id for r in failed if r.requirement_id in must}),
    )


async def save(ctx: AgentContext, ins: Inputs, report: TestReport):
    rev = await artifacts.add_revision(
        ctx.db,
        ctx.project,
        "testing",
        report.model_dump(mode="json"),
        sources=ins.sources,
        origin="agent",
        summary=report.summary,
        created_by=None,
        run_id=ctx.run.id,
    )
    await ctx.emit(
        "step",
        f"Saved test report {rev.revision} ({report.passed} passed, {report.failed} failed)",
        {"artifact_id": str(rev.artifact_id), "revision": rev.revision},
    )
    return {"artifact_id": str(rev.artifact_id), "revision": rev.revision}


async def generate(ctx: AgentContext, ins: Inputs) -> AgentOutcome:
    origins, error = await ensure_sandbox(ctx, ins.impl)
    await ctx.emit("step", error or f"Sandbox healthy ({len(origins)} origin(s))", None)
    plan, usage = await call_structured(
        ctx,
        schema_model=TestPlan,
        schema_name="test_plan",
        system=prompts.PLAN_SYSTEM,
        user=prompts.plan_prompt(ins.reqs, ins.design, origins),
        validate=lambda p: coverage_issues(p, ins.reqs),
    )
    results = await execute(ctx, plan, origins, error, usage)
    report = build_report(plan, results, ins.reqs)
    output = await save(ctx, ins, report)
    return AgentOutcome(output=output, summary=report.summary, usage=usage)


async def run(ctx: AgentContext) -> AgentOutcome:
    ins = await load_inputs(ctx)
    await ctx.emit("step", f"Loaded the implementation ({len(ins.impl.files)} files)", None)
    if ctx.run.mode == "chat":
        from app.modules.agents.testing import chat

        return await chat.run(ctx, ins)
    return await generate(ctx, ins)
