"""Graph nodes. Each agent call is recorded as an ``agent_run`` keyed by ``graph_key`` so that a
node re-executed after a crash or a retried job reuses its run instead of duplicating work."""

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pymongo.errors import DuplicateKeyError

from app.core.documents import now
from app.core.errors import QuotaExceeded
from app.llm.base import ProviderError
from app.modules.agents.base import AgentContext, AgentOutcome, OutputInvalid, get_agent
from app.modules.agents.orchestrator import agent as orchestrator_agent
from app.modules.agents.orchestrator.schema import PlanContent
from app.modules.auth.models import User
from app.modules.conversations.service import add_message
from app.modules.delivery.facts import collect_facts
from app.modules.delivery.plans import current_plan
from app.modules.delivery.routing import Decision, needs_replan, route
from app.modules.delivery.state import DeliveryState, GraphDeps
from app.modules.orchestration import events, runs
from app.modules.orchestration.models import AgentRun, RunError, RunStatus
from app.modules.orchestration.service import _quota_check, _resolve_inputs
from app.modules.projects.models import Project

logger = logging.getLogger(__name__)

AGENT_FOR = {
    "requirements": "requirements_analyst",
    "architecture": "solution_architect",
    "plan": "project_planner",
    "budget": "budget_estimator",
    "code": "developer",
}
LABEL = {
    "orchestrator": "Project Orchestrator",
    "requirements": "Requirements Agent",
    "architecture": "Architecture Agent",
    "plan": "Plan Agent",
    "budget": "Budget Agent",
    "code": "Development Agent",
}


@dataclass
class NodeResult:
    outcome: Literal["succeeded", "failed", "gate"]
    run_id: uuid.UUID | None = None
    output: dict[str, Any] | None = None
    gate: Decision | None = None


async def _project(deps: GraphDeps, state: DeliveryState, config: RunnableConfig) -> Project:
    pid = state.get("project_id") or config["configurable"]["thread_id"]
    return Project.from_doc(await deps.db.projects.find_one({"_id": uuid.UUID(pid)}))


def _graph_key(project: Project, config: RunnableConfig, node: str) -> str:
    return f"{project.id}:{config['metadata']['langgraph_step']}:{node}"


async def _start_or_reuse_run(
    deps: GraphDeps, project: Project, *, stage, agent, mode, instruction, graph_key
) -> AgentRun | NodeResult:
    db = deps.db
    existing = await db.agent_runs.find_one({"graph_key": graph_key})
    if existing:
        run = AgentRun.from_doc(existing)
        if run.status == RunStatus.SUCCEEDED:
            return NodeResult("succeeded", run.id, run.output)
        if run.status != RunStatus.RUNNING:
            return NodeResult("failed", run.id)
        return run
    owner = User.from_doc(await db.users.find_one({"_id": project.owner_id}))
    try:
        await _quota_check(db, owner)
    except QuotaExceeded as exc:
        return NodeResult(
            "gate",
            gate=Decision(
                "gate", gate_kind="quota", payload={"stage": stage, "reason": exc.message}
            ),
        )
    inputs = (
        [] if stage == "orchestrator" else await _resolve_inputs(db, project, stage, mode, None)
    )
    provider = deps.provider_factory()
    run = AgentRun(
        project_id=project.id,
        owner_id=project.owner_id,
        stage=stage,
        agent=agent,
        mode=mode,
        status=RunStatus.RUNNING,
        inputs=inputs,
        instruction=instruction,
        provider=provider.name,
        model=provider.model,
        is_mock=provider.is_mock,
        graph_key=graph_key,
        created_by=project.owner_id,
        started_at=now(),
    )
    try:
        await db.agent_runs.insert_one(run.to_doc())
        if stage in AGENT_FOR:
            await add_message(
                db,
                project,
                stage,
                "system",
                f"{LABEL[stage]} started ({mode})",
                refs={"run_id": str(run.id)},
            )
    except DuplicateKeyError as exc:
        if "graph_key" in (exc.details or {}).get("keyPattern", {}):
            return await _start_or_reuse_run(
                deps,
                project,
                stage=stage,
                agent=agent,
                mode=mode,
                instruction=instruction,
                graph_key=graph_key,
            )
        return NodeResult(
            "gate", gate=Decision("gate", gate_kind="stage_busy", payload={"stage": stage})
        )
    return run


async def _push_attempt(deps: GraphDeps, run: AgentRun) -> int:
    n = len(run.attempts) + 1
    await deps.db.agent_runs.update_one(
        {"_id": run.id},
        {
            "$push": {
                "attempts": {
                    "n": n,
                    "started_at": now(),
                    "finished_at": None,
                    "outcome": None,
                    "error_code": None,
                    "input_tokens": None,
                    "output_tokens": None,
                }
            }
        },
    )
    return n


async def _finish_attempt(
    deps: GraphDeps, run: AgentRun, n: int, outcome: str, code=None, usage=None
) -> None:
    usage = usage or {}
    await deps.db.agent_runs.update_one(
        {"_id": run.id, "attempts.n": n},
        {
            "$set": {
                "attempts.$.finished_at": now(),
                "attempts.$.outcome": outcome,
                "attempts.$.error_code": code,
                "attempts.$.input_tokens": usage.get("input_tokens"),
                "attempts.$.output_tokens": usage.get("output_tokens"),
            }
        },
    )


async def run_agent_node(
    deps: GraphDeps,
    project: Project,
    *,
    stage: str,
    agent: str,
    mode: str,
    instruction: str | None,
    graph_key: str,
    fn=None,
) -> NodeResult:
    """Run one agent as a recorded run. Retryable provider errors propagate (the job retries)."""
    started = await _start_or_reuse_run(
        deps,
        project,
        stage=stage,
        agent=agent,
        mode=mode,
        instruction=instruction,
        graph_key=graph_key,
    )
    if isinstance(started, NodeResult):
        return started
    run, db = started, deps.db
    attempt = await _push_attempt(deps, run)
    provider = deps.provider_factory()

    async def emit(type_, message, data=None):
        await events.emit(db, project.id, run.id, type_, message, data)

    await emit(
        "status",
        f"{LABEL.get(stage, stage)} started ({mode}) — attempt {attempt}",
    )
    ctx = AgentContext(db, project, run, provider, emit, deps.cancelled)
    try:
        outcome: AgentOutcome = await (fn or get_agent(agent))(ctx)
    except asyncio.CancelledError:
        await _finish_attempt(deps, run, attempt, "CANCELLED")
        await runs.transition(db, run.id, {RunStatus.RUNNING}, RunStatus.CANCELLED)
        await emit("status", "Cancelled")
        raise
    except (ProviderError, TimeoutError) as exc:
        retryable = isinstance(exc, TimeoutError) or exc.retryable
        code = "TIMEOUT" if isinstance(exc, TimeoutError) else exc.code
        message = (
            "The agent took too long to respond" if isinstance(exc, TimeoutError) else exc.message
        )
        if retryable:
            await _finish_attempt(deps, run, attempt, "RETRYING", code)
            await emit("status", f"Attempt {attempt} failed ({message}); will retry")
            raise
        return await _fail(deps, project, run, attempt, code, message)
    except OutputInvalid as exc:
        return await _fail(
            deps,
            project,
            run,
            attempt,
            "OUTPUT_INVALID",
            f"The model's output was still invalid after repairs: {exc}",
            {"issues": exc.issues[:10]},
        )
    except Exception:
        logger.exception("Agent %s failed on run %s", agent, run.id)
        return await _fail(deps, project, run, attempt, "INTERNAL", "The agent failed unexpectedly")
    await _finish_attempt(deps, run, attempt, "SUCCEEDED", usage=outcome.usage)
    await runs.transition(
        db,
        run.id,
        {RunStatus.RUNNING},
        RunStatus.SUCCEEDED,
        output=outcome.output,
        summary=outcome.summary[:1000],
        usage={
            "input_tokens": int(outcome.usage.get("input_tokens") or 0),
            "output_tokens": int(outcome.usage.get("output_tokens") or 0),
        },
    )
    await emit("status", "Succeeded")
    return NodeResult("succeeded", run.id, outcome.output)


async def _fail(deps, project, run, attempt, code, message, data=None) -> NodeResult:
    await _finish_attempt(deps, run, attempt, "FAILED", code)
    await runs.transition(
        deps.db,
        run.id,
        {RunStatus.RUNNING},
        RunStatus.FAILED,
        error=RunError(code=code, message=message).model_dump(),
    )
    await events.emit(deps.db, project.id, run.id, "error", f"Failed: {message}", data)
    return NodeResult("failed", run.id)


def _decision(d: Decision) -> dict[str, Any]:
    return asdict(d)


async def orchestrator_node(
    state: DeliveryState, runtime: Runtime[GraphDeps], config: RunnableConfig
) -> dict[str, Any]:
    deps = runtime.context
    project = await _project(deps, state, config)
    update: dict[str, Any] = {
        "trigger": None,
        "last_event": None,
        "forced_gate": None,
        "project_id": str(project.id),
    }
    if state.get("forced_gate"):
        return {**update, "decision": state["forced_gate"]}
    facts = await collect_facts(deps.db, project)
    plan = await current_plan(deps.db, project)
    if needs_replan(state.get("trigger"), plan is not None, state.get("last_event")):

        async def planner(ctx: AgentContext) -> AgentOutcome:
            _, outcome = await orchestrator_agent.plan_turn(ctx, facts)
            return outcome

        result = await run_agent_node(
            deps,
            project,
            stage="orchestrator",
            agent=orchestrator_agent.NAME,
            mode="generate",
            instruction=None,
            graph_key=_graph_key(project, config, "orchestrator"),
            fn=planner,
        )
        if result.outcome == "gate":
            return {**update, "decision": _decision(result.gate)}
        if result.outcome == "failed" and plan is None:
            return {
                **update,
                "decision": _decision(
                    Decision(
                        "gate",
                        gate_kind="failed",
                        payload={
                            "stage": "orchestrator",
                            "reason": "The Project Orchestrator failed",
                        },
                    )
                ),
            }
        plan = await current_plan(deps.db, project)
        facts = await collect_facts(deps.db, project)  # the planner may have written a spec
    decision = route(PlanContent.model_validate(plan.content), facts, state.get("loops") or {})
    return {**update, "decision": _decision(decision), "plan_version": plan.version}


def stage_node(stage: str):
    async def node(
        state: DeliveryState, runtime: Runtime[GraphDeps], config: RunnableConfig
    ) -> dict[str, Any]:
        deps = runtime.context
        project = await _project(deps, state, config)
        d = state["decision"]
        result = await run_agent_node(
            deps,
            project,
            stage=stage,
            agent=AGENT_FOR[stage],
            mode=d["mode"],
            instruction=d.get("instruction"),
            graph_key=_graph_key(project, config, stage),
        )
        if result.outcome == "gate":
            return {"forced_gate": _decision(result.gate)}
        loops = dict(state.get("loops") or {})
        loops[stage] = loops.get(stage, 0) + 1
        return {
            "loops": loops,
            "last_event": {"stage": stage, "outcome": result.outcome, "run_id": str(result.run_id)},
        }

    node.__name__ = f"{stage}_node"
    return node


async def gate_node(state: DeliveryState) -> dict[str, Any]:
    d = state["decision"]
    value = interrupt(
        {"kind": d["gate_kind"], "stage": d["payload"].get("stage"), "payload": d["payload"]}
    )
    trigger = value.get("trigger") if isinstance(value, dict) else None
    # A person acted: the automatic re-run budget starts again.
    return {"trigger": trigger, "loops": {}}
