# Agentic Core (C1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the click-through stage pipeline with a LangGraph delivery graph driven by an Orchestrator Agent, started from a conversational intake, with per-stage agent chats and a live 3D agent graph.

**Architecture:** New backend module `app/modules/delivery/` (facts, routing, graph, threads, service, router) and agent `app/modules/agents/orchestrator/`, plus `app/modules/conversations/`. The compiled LangGraph graph runs inside the existing Mongo-leased worker as a new `graph_step` job type, checkpointed to MongoDB; stage nodes wrap the existing Requirements/Architecture agents. Frontend adds intake chat, agent chat panels and an R3F agent graph.

**Tech Stack:** FastAPI, PyMongo async, Pydantic v2, `langgraph` 1.2.x, `langgraph-checkpoint-mongodb` 0.5.x, pytest · Next.js 16, TanStack Query, `three` 0.186.1, `@react-three/fiber` 9.8.1, `@react-three/drei` 10.7.9, Vitest, Playwright + axe.

**Spec:** `poc-backend/docs/specs/2026-10-24-agentic-core-design.md`

## Global Constraints

- **No git commits or staging** (owner instruction 2026-10-24). Each task ends with a test checkpoint; the owner commits.
- Work on the existing `feat/agentic` branch in both repos.
- Every new project route resolves the project via `get_project_for` (others → 404); every query on threads/plans/conversations/messages filters by `project_id`.
- Revisions, plans, messages and specification revisions are never updated after insert.
- Orchestrator never `include`s an unimplemented agent; C1 implemented stages: `requirements`, `architecture`.
- Approval is always required for `requirements` and `architecture`.
- Loop budget: `loops[stage] > 3` → `gate needs_input`. ≤7 questions per orchestrator turn.
- Gate kinds exactly: `intake_questions, awaiting_answers, approval, needs_input, stage_busy, quota`; worker-set kinds: `failed, cancelled`. Thread statuses exactly: `IDLE, RUNNING, PAUSED, DONE, FAILED`.
- Graph job: `type="graph_step"`, `idempotency_key="graph:<project_id>"` (unset on DONE), `timeout_s = 3 × AGENT_TIMEOUT_S`, `max_attempts` 3, same backoff as agent jobs.
- Agent-run `graph_key = "<project_id>:<langgraph_step>:<node>"`; orchestrator runs: `agent="project_orchestrator"`, `stage="orchestrator"`.
- New error codes: `GRAPH_BUSY` 409, `NOT_PAUSED` 409.
- New SSE event types: `message`, `graph` (run_event `type` values; `run_id` may be null).
- No test calls Groq; mock provider only. Brand tokens only; WCAG 2.2 AA.
- 3D view follows the installed threejs skills: `frameloop="demand"`, no `setState` inside `useFrame`, delta-based motion, `<OrbitControls makeDefault />`, dispose on unmount; no bloom/neon/gradients.

## Review Focus

1. **Worker dies after a stage node saved its revision but before the checkpoint** → on reclaim the node re-executes, finds the `SUCCEEDED` run by `graph_key`, returns its output; exactly one revision and one run exist. (Task 6 `test_crash_after_node_no_duplicate`)
2. **User approves twice quickly / double-clicks approve** → second `notify` is a no-op (one graph job); no duplicate stage runs. (Task 6 `test_duplicate_notify_single_job`)
3. **Chat run active on architecture when requirements approval resumes the graph** → graph pauses `stage_busy`, and resumes automatically when the chat run finishes. (Task 8 `test_stage_busy_then_chat_finished_resumes`)
4. **Orchestrator returns a plan including `budget` (not implemented) and dropping requirements approval** → repaired once, then default plan with `fallback: true`; graph proceeds on the default. (Task 4 `test_invalid_plan_falls_back`)
5. **Re-planning with an unchanged brief** → no new specification revision, requirements not marked stale. (Task 4 `test_unchanged_brief_no_spec_revision`)

---

## Task 0: Baseline

- [ ] Copy skills from the reviewed clone `…/scratchpad/dl/threejs-skills/skills/` into `poc-frontend/.claude/skills/`: `threejs-fundamentals, threejs-geometry, threejs-materials, threejs-lighting, threejs-interaction, threejs-animation, threejs-performance, threejs-react-three-fiber` (+ `LICENSE` as `poc-frontend/.claude/skills/THREEJS-SKILLS-LICENSE`).
- [ ] Backend: `uv run pytest -q` → 257 passed (with the staged SSE `no-transform` change).
- [ ] Frontend: `npx vitest run`, `npx tsc --noEmit`, `npm run lint` → record counts.
- [ ] Run `npx playwright test e2e/agentic.spec.ts`; if it still fails waiting for the question label, root-cause it (superpowers:systematic-debugging) and fix before Task 1. Record cause.

## Backend (`poc-backend`)

### Task 1: Dependencies, checkpointer, model and index changes

**Files:** Modify `pyproject.toml` (add `langgraph>=1.2,<1.3`, `langgraph-checkpoint-mongodb>=0.5,<0.6`; `pymongo>=4.18.2`), `app/core/db.py`, `app/modules/orchestration/models.py`, `app/modules/orchestration/events.py`. Create `app/modules/delivery/__init__.py`, `app/modules/delivery/checkpoint.py`. Test `tests/delivery/test_checkpoint.py`, `tests/delivery/test_indexes.py`.

**Interfaces — Produces:**
- `make_checkpointer(db: Db) -> BaseCheckpointSaver` — `AsyncMongoDBSaver` on the app's client/db name with collections `graph_checkpoints`, `graph_checkpoint_writes`. If the package cannot work with our `AsyncMongoClient`, implement `MongoCheckpointSaver(BaseCheckpointSaver)` in the same file (`aget_tuple`, `alist`, `aput`, `aput_writes`, `adelete_thread`) and record the decision in the file docstring.
- `Job.run_id: UUID | None`, `Job.idempotency_key: str | None`, `Job.input: dict | None`; `RunEvent.run_id: UUID | None`; `EventType` adds `"message" | "graph"`; `RunMode` adds `"chat"`; `AgentRun.graph_key: str | None`.
- `events.emit(db, project_id, run_id: UUID | None, type, message, data=None, session=None)`.
- Indexes: `jobs`: partial unique `run_id` (`{"run_id": {"$type": "binData"}}`, name `one_job_per_run`), partial unique `idempotency_key` (`{"idempotency_key": {"$type": "string"}}`); `agent_runs`: unique sparse `graph_key`; new `delivery_threads` unique `project_id`; `plans` unique `(project_id, version)`; `conversations` unique `(project_id, channel)`; `messages` unique `(conversation_id, seq)`. `ensure_indexes` drops a legacy `jobs.run_id_1` index if present.

- [ ] **Step 1: failing tests** — `test_checkpoint_roundtrip` (compile a 2-node toy `StateGraph` with the saver, invoke with `thread_id`, new saver instance → `aget_state` returns same values); `test_interrupt_resume_persists` (toy graph with `interrupt`, resume via `Command(resume=…)` on a fresh saver); `test_legacy_run_id_index_migrated` (create `run_id_1` unique index, run `ensure_indexes` → gone, `one_job_per_run` present); `test_two_graph_jobs_same_key_conflict`; `test_graph_jobs_without_run_id_coexist`.
- [ ] **Step 2:** `uv sync && uv run pytest tests/delivery -q` → FAIL. **Step 3:** implement. **Step 4:** `uv run pytest -q && uv run ruff check .` → all pass.

### Task 2: Plan schema, facts and policy

**Files:** Create `app/modules/agents/orchestrator/{__init__.py,schema.py,policy.py}`, `app/modules/delivery/facts.py`. Test `tests/agents/test_orchestrator_policy.py`, `tests/delivery/test_facts.py`.

**Interfaces — Produces:**
- `schema.py`: `StagePlan(stage: Literal["requirements","architecture","plan","budget","code"], include: bool, rationale: str≤300, inputs: list[stage], approval_required: bool)`; `MissingInfo(id: ^I-\d{3}$, question, why_it_matters, blocking: bool)`; `Brief` = fields of `ProjectSpecification` sections `problem`, `requirements`, `constraints` + `name`/`description`; `PlanContent(project_name≤160, objective_summary≤1000, project_type: Literal[web_app, mobile_app, data_pipeline, integration, internal_tool, other], brief: Brief, stages: list[StagePlan], missing_info: list[MissingInfo]≤7, next_action: Literal[run_stage, ask_user, wait_approval, done], next_stage: stage | None, decision_summary≤600)`; `OrchestratorOutput(content: PlanContent, summary: str)`.
- `facts.py`: `@dataclass StageFacts(stage, has_draft: bool, latest_revision: int|None, approved_revision: int|None, approved_is_latest: bool, stale: bool, stale_reason: str|None, save_errors: int, blocking_unanswered: int, changes_requested: str|None (comment), active_run: bool)`; `@dataclass Facts(project_id, spec_revision: int|None, stages: dict[str, StageFacts], intake_messages: list[str], preferences: dict)`; `async collect_facts(db, project) -> Facts`; `digest(facts) -> str` (compact, ≤4000 chars, no artifact content).
- `policy.py`: `IMPLEMENTED = {"requirements","architecture"}`; `ORDER = ["requirements","architecture","plan","budget","code"]`; `REQUIRED_APPROVAL = {"requirements","architecture"}`; `check_plan(plan: PlanContent, facts: Facts) -> list[ValidationIssue]` (rules 1–4, 6 of spec §4.3; codes `UNAVAILABLE_AGENT, ORDER, APPROVAL_REQUIRED, NEXT_STAGE, TOO_MANY_QUESTIONS`); `default_plan(facts: Facts, brief: Brief | None) -> PlanContent`.

- [ ] **Step 1: failing tests** — one test per rule code; `test_default_plan_passes_policy`; `test_default_plan_includes_all_implemented_with_approval`; facts: no artifacts → `has_draft False`; blocking questions counted; changes-requested comment surfaced only for the latest revision; stale flag; `test_digest_excludes_content` (requirement descriptions not in digest).
- [ ] **Steps 2–4** as Task 1.

### Task 3: Deterministic router

**Files:** Create `app/modules/delivery/routing.py`. Test `tests/delivery/test_routing.py`.

**Interfaces — Consumes:** `PlanContent`, `Facts`. **Produces:** `@dataclass Decision(node: Literal["requirements","architecture","gate","end"], mode: Literal["generate","revise"] | None = None, instruction: str | None = None, gate_kind: str | None = None, payload: dict = {})`; `route(plan: PlanContent, facts: Facts, loops: dict[str,int]) -> Decision`; `needs_replan(trigger: str | None, facts: Facts, last_event: dict | None, has_plan: bool) -> bool` (true for: no plan, `trigger == "user_message"`, `last_event == {outcome: "approved", stage: "requirements"}`-equivalent detected via trigger `approved:requirements`, `last_event.outcome == "failed"`).

Routing order per included stage (spec §3.3): blocking `missing_info` → `gate intake_questions`; `active_run` → `gate stage_busy`; no draft / stale / changes requested → run (`generate` if no draft, else `revise`; `instruction` = changes-requested comment); `blocking_unanswered > 0` → `gate awaiting_answers`; `save_errors > 0` → run `revise`; not `approved_is_latest` → `gate approval` (payload `{stage, revision}`); else next stage; all done → `end`. Before any run: `loops[stage] >= 3` → `gate needs_input`.

- [ ] **Step 1: failing tests** — a parametrized table covering every branch above, including: architecture excluded by plan → `end` after requirements approved; requirements stale while architecture approved → run requirements `revise`; architecture changes requested → run architecture `generate` with the comment as instruction; loop budget.
- [ ] **Steps 2–4.**

### Task 4: Orchestrator Agent, plans store, brief → specification

**Files:** Create `app/modules/agents/orchestrator/{prompts.py,agent.py,mock_fixture.py}`, `app/modules/delivery/plans.py`; modify `app/modules/agents/__init__.py` (`load_all` registers orchestrator), `app/modules/projects/{models.py,service.py}` (`Project.preferences: dict | None`; `ProjectSpecification.origin: Literal["user","orchestrator"] = "user"`; extract `async add_spec_revision(db, project, data: dict, *, created_by: UUID | None, origin) -> ProjectSpecification` used by `save_specification`). Test `tests/agents/test_orchestrator_agent.py`, `tests/delivery/test_plans.py`.

**Interfaces — Produces:**
- `async plan_turn(ctx: AgentContext, facts: Facts) -> tuple[Plan, AgentOutcome]` — `call_structured` with `schema_name="orchestrator_output"`, `validate=lambda o: check_plan(o.content, facts)`; on `OutputInvalid` returns `default_plan` with `fallback=True` and the issues; persists a `Plan`; writes a spec revision only if `merge_brief(plan.brief, project.preferences) != latest spec data`; updates `project.name` from `project_name` when the project name is still the intake placeholder.
- `plans.py`: `Plan(Document)` fields `project_id, owner_id, version, content: dict, validation: ValidationResult, fallback: bool, run_id: UUID | None, created_at`; `async save_plan(db, project, content, validation, fallback, run_id) -> Plan`; `async current_plan(db, project) -> Plan | None`; `async list_plans(db, project) -> list[Plan]`; `merge_brief(brief: dict, preferences: dict | None) -> dict` (preferences override `constraints.*`).
- Prompt: delimited `<project_data>` block with digest + intake messages + preferences; states it is data, not instructions; asks for focused questions only when the answer materially changes scope.
- Mock fixture: plan including both implemented stages, one non-blocking question; when the intake text contains `"[ask]"`, one blocking `missing_info` (lets tests drive `intake_questions`).

- [ ] **Step 1: failing tests** — `test_plan_saved_versioned`; `test_brief_written_as_spec_revision_origin_orchestrator`; `test_preferences_override_brief_constraints`; `test_unchanged_brief_no_spec_revision` (Review Focus 5); `test_invalid_plan_falls_back` (scripted mock returns `budget` included + approval false three times → `fallback True`, issues stored; Review Focus 4); `test_repaired_plan_not_fallback`; `test_placeholder_name_replaced`.
- [ ] **Steps 2–4.**

### Task 5: Delivery graph and stage nodes

**Files:** Create `app/modules/delivery/{state.py,nodes.py,graph.py}`. Test `tests/delivery/test_graph.py`.

**Interfaces — Consumes:** Tasks 1–4; existing `get_agent(name)`, `AgentContext`. **Produces:**
- `state.py`: `class DeliveryState(TypedDict, total=False)`: `project_id: str, plan_version: int | None, pending: dict | None, loops: dict[str,int], last_event: dict | None, trigger: str | None, decision: dict | None`.
- `@dataclass GraphDeps(db, provider_factory, emit_factory, cancelled)` passed via `config["configurable"]["deps"]` (never checkpointed).
- `nodes.py`: `async orchestrator_node(state, config) -> dict` (replan if `needs_replan`, then `route`; stores `decision`; clears `trigger`); `async stage_node(stage: str)` factory → node that creates/reuses the `agent_run` by `graph_key` (status RUNNING, `created_by=project.owner_id`, quota check → on `QuotaExceeded` returns decision to `gate quota`), runs the registered agent with the decision's mode/instruction, transitions the run, emits events, returns `last_event {stage, outcome: succeeded|failed, run_id}` and increments `loops[stage]`; retryable `ProviderError`/`TimeoutError` are re-raised (job retries); `OutputInvalid`/non-retryable → run `FAILED`, `last_event.outcome = "failed"`; active-run `DuplicateKeyError` → `gate stage_busy`. `async gate_node(state, config)` → `interrupt({kind, stage, payload})`; on resume returns `{trigger, pending: None}` and resets `loops` for user triggers.
- `graph.py`: `build_graph(checkpointer) -> CompiledStateGraph` with edges `START→orchestrator`, conditional `orchestrator → {requirements, architecture, gate, END}` on `state["decision"]["node"]`, `requirements→orchestrator`, `architecture→orchestrator`, `gate→orchestrator`.
- Architecture agent: `generate` honours `run.instruction` (append to prompt as "User direction"); requirements `revise` honours `run.instruction` likewise.

- [ ] **Step 1: failing tests** (InMemorySaver, mock provider, real Mongo) — `test_intake_to_requirements_approval_gate` (one invoke: orchestrator run + requirements run, ends at `gate approval` with `stage=requirements`); `test_blocking_questions_pause_awaiting_answers`; `test_resume_after_approval_runs_architecture` (approve via artifacts service, resume → architecture run → `gate approval architecture`); `test_changes_requested_reruns_with_comment`; `test_intake_questions_gate_then_user_message_replans`; `test_quota_gate`; `test_failed_stage_routes_to_orchestrator_replan`; `test_node_reuses_succeeded_run_by_graph_key`.
- [ ] **Steps 2–4.**

### Task 6: Worker integration, threads, notify, resume/cancel

**Files:** Create `app/modules/delivery/{threads.py,service.py}`; modify `app/worker/runner.py` (dispatch on `job.type`), `app/worker/__main__.py` (build checkpointer + graph once). Test `tests/delivery/test_worker_graph.py`, `tests/delivery/test_notify.py`.

**Interfaces — Produces:**
- `threads.py`: `DeliveryThread(Document)` (`project_id, owner_id, status, pending, plan_version, last_error, cancel_requested, started_by, updated_at`); `async get_thread(db, project) -> DeliveryThread | None`; `async set_thread(db, project_id, **fields)` (emits `graph` event with `{status, pending}`).
- `service.py`: `async start_delivery(db, project, user) -> DeliveryThread` (creates thread + `graph_step` job input `{"trigger": "intake"}`); `async notify(db, project, trigger: str, message: str | None = None) -> bool` (table in spec §3.6; returns whether a job was enqueued); `async resume(db, project, user)` (`FAILED`/`PAUSED cancelled` → enqueue; else `NOT_PAUSED`); `async cancel(db, project, user)` (sets `cancel_requested`, cancels active agent runs via existing `cancel_run`; nothing running → `NOT_PAUSED`). `_enqueue` inserts the job with `idempotency_key`; `DuplicateKeyError` → no-op.
- Worker `graph_step`: invoke `graph.ainvoke(Command(resume=job.input) if thread.status == "PAUSED" else job.input, config={"configurable": {"thread_id": str(project_id), "deps": deps}})`; afterwards `aget_state` → interrupt value → thread `PAUSED` with `pending`; no `next` → `DONE`; retryable errors → existing backoff (thread stays `RUNNING`); exhausted/non-retryable → thread `FAILED` with `pending {kind: "failed"}`; heartbeat checks `delivery_threads.cancel_requested` → task cancelled → thread `PAUSED {kind: "cancelled"}`; job DONE unsets `idempotency_key`.

- [ ] **Step 1: failing tests** — `test_graph_job_runs_to_first_gate`; `test_crash_after_node_no_duplicate` (Review Focus 1: run node, simulate crash by raising after `add_revision` once, expire lease, `run_once` again → one revision, one run); `test_retryable_provider_error_retries_job`; `test_exhausted_retries_thread_failed_and_resume_restarts`; `test_cancel_running_graph`; `test_duplicate_notify_single_job` (Review Focus 2); `test_notify_when_done_restarts`; `test_notify_while_running_noop`; existing `tests/worker` still pass unchanged.
- [ ] **Steps 2–4.**

### Task 7: HTTP API — intake, delivery, plans, preferences, hooks, workflow

**Files:** Create `app/modules/delivery/{router.py,schemas.py}`; modify `app/main.py`, `app/modules/artifacts/router.py` (approve, request-changes, answers call `notify` with `approved:<kind>`, `changes_requested:<kind>`, `answers`), `app/modules/projects/router.py` (`PUT /projects/{id}/preferences` → `notify("user_message")`), `app/modules/orchestration/stages.py` (`NOT_PLANNED` when the current plan excludes a stage; `pending` gate overrides `WAITING_FOR_*` reasons), `app/modules/orchestration/schemas.py` (`RunEventOut.run_id` optional), `app/core/errors.py` (`GraphBusy`, `NotPaused`). Test `tests/api/test_delivery_api.py`, extend `tests/api/test_isolation_agentic.py`.

**Interfaces — Produces:** routes per spec §7; `IntakeIn(message: str 1..4000, preferences: Constraints | None)`; `DeliveryOut(status, pending, plan: PlanOut | None, last_error)`; `PlanOut(version, content, validation, fallback, created_at, run_id)`; `POST /projects/intake` → `201 {project: ProjectOut, delivery: DeliveryOut}` (creates project with placeholder name = first 60 chars, intake conversation + user message (Task 8 service — implement the conversations service in this task if Task 8 not done: `add_message` only), thread, job).

- [ ] **Step 1: failing tests** — intake creates project, conversation message, thread `IDLE`→job queued; after `worker.run_once()` thread `PAUSED approval requirements` and `GET /delivery` shows plan; approving requirements via HTTP enqueues a graph job; `GET /plans` lists versions; preferences save → spec unchanged until re-plan, then merged; `GET /workflow` reports `NOT_PLANNED` for an excluded stage; `resume` on a paused thread → `409 NOT_PAUSED`; isolation parametrized over all new routes → 404.
- [ ] **Steps 2–4.**

### Task 8: Conversations and stage chat agents

**Files:** Create `app/modules/conversations/{__init__.py,models.py,service.py,router.py,schemas.py}`, `app/modules/agents/requirements/chat.py`, `app/modules/agents/architecture/chat.py` (+ schemas and mock fixtures in each agent's `schema.py`/`mock_fixture.py`); modify both agents' `run` to dispatch `mode == "chat"`, `app/worker/runner.py` (after a run reaches a terminal status call `on_run_finished` hooks), `app/modules/agents/base.py` (`register_run_finished(fn)` / `run_finished_hooks()`). Test `tests/conversations/test_chat.py`.

**Interfaces — Produces:**
- `Channel = Literal["intake","requirements","architecture"]`; `Message(Document)` per spec §5.1; `async add_message(db, project, channel, role, text, agent=None, refs=None) -> Message` (allocates `seq` with `$inc` on `counters._id = "msg:<conversation_id>"`, emits `message` event `{channel, message_id, seq}`); `async list_messages(db, project, channel, after=0, limit=100)`; `async recent_messages(db, project, channel, n=20) -> list[Message]`.
- `POST /conversations/{channel}/messages {text}`: `intake` → `add_message` + `notify("user_message", text)` (202 `{message}`); stage channels → `add_message` + `start_stage(..., mode="chat", instruction=text)` (202 `{message, run}`); stage blocked → 409 `STAGE_BLOCKED` with the reason also added as a `system` message.
- `RequirementsChatOutput(reply≤2000, action: Literal["none","answer_questions","revise"], answers: dict[str,str] = {}, instruction: str | None)`; `ArchitectureChatOutput(reply≤2000, action: Literal["none","propose_changes","generate"], ops: list[Op] = [], instruction: str | None)` — `propose_changes` validated in the repair loop with `apply_ops` + `validate_structure`, then `create_change_set`; `generate` runs the generate path with the instruction (requires approved requirements, else reply explains). Agent message refs: `{run_id, artifact_revision?, change_set_id?}`.
- Run-finished hook (conversations): chat run `FAILED` → system message "The agent couldn't finish: <error.message>"; any chat run terminal → `notify(project, "chat_finished")`.
- Graph system messages: `stage_node` and `gate_node` add `system` messages to the stage channel (e.g. "Requirements Agent started (generate)", "Waiting for your approval of revision 3").

- [ ] **Step 1: failing tests** — intake message resumes paused graph; requirements chat `answer_questions` → new revision with answers + agent message refs revision; `revise` → new draft; architecture `propose_changes` → change set PROPOSED + message refs it; invalid ops → repaired or run FAILED + system message; one active chat run per stage (409 path returns existing run); `test_stage_busy_then_chat_finished_resumes` (Review Focus 3); message `seq` monotonic per conversation; isolation.
- [ ] **Steps 2–4.** **Step 5:** README: delivery graph, worker runs graph jobs, new collections, intake flow.

## Frontend (`poc-frontend`)

### Task 9: Types, API client, event handling

**Files:** Create `src/lib/schemas/delivery.ts`, `src/lib/schemas/conversation.ts`, `src/lib/api/delivery.ts`, `src/lib/api/conversations.ts`; modify `src/lib/schemas/workflow.ts` (`NOT_PLANNED` status, nullable `run_id`, event types), `src/hooks/use-project-events.ts`. Test `src/lib/api/__tests__/delivery.test.ts`, extend `src/hooks/__tests__/use-project-events.test.tsx`.

**Produces:** types mirroring Task 7/8 schemas; hooks `useDelivery(pid)`, `usePlans(pid)`, `useIntake()` (POST `/projects/intake`), `useResumeDelivery()`, `useCancelDelivery()`, `useSavePreferences()`, `useMessages(pid, channel)`, `useSendMessage(pid, channel)`; keys `dlKeys.delivery/plans`, `convKeys.messages(pid, channel)`. Events hook: `graph` → invalidate delivery + workflow; `message` → invalidate `convKeys.messages(pid, data.channel)`; exposes `lastEvents` for the 3D view.

- [ ] Failing tests → implement → `npx vitest run && npx tsc --noEmit && npm run lint` pass.

### Task 10: Conversational intake and Preferences

**Files:** Create `src/components/intake/{intake.tsx,chat-thread.tsx,chat-composer.tsx}` (thread + composer reusable by Task 11), `src/components/preferences/preferences-form.tsx`; modify `src/app/(app)/projects/new/page.tsx` (render `<Intake />`), `src/app/(app)/projects/[id]/edit/page.tsx` (render Preferences form built from the existing `StepConstraints` fields), dashboard/projects-list "New project" copy. Test `src/components/intake/__tests__/intake.test.tsx`, `src/components/preferences/__tests__/preferences-form.test.tsx`.

- [ ] Failing tests: single labelled textarea "What do you want to build?" + "Start project" button (verb-first); submit → POST intake → navigate to `/projects/{id}`; empty submit disabled; API error shown with recovery; optional collapsed "Preferences" disclosure sends `preferences`; `ChatThread` renders roles distinctly with agent name and refs as links, `aria-live="polite"`; Preferences form saves via `PUT /preferences` and shows "Saved — the Orchestrator will take this into account". → implement → pass.

### Task 11: Agent chat panels, gates, plan and pipeline

**Files:** Create `src/components/agents/{agent-chat-panel.tsx,gate-card.tsx,plan-panel.tsx}`; modify `src/components/requirements/requirements-view.tsx` (chat panel beside table), `src/components/studio/studio.tsx` (right panel tabs "Inspector | Agent chat"; remove `change-request.tsx` box, keep `change-set-preview` behaviour driven by `refs.change_set_id`), `src/components/projects/project-overview.tsx` (intake chat while `intake_questions`, plan panel, delivery status), `src/components/workflow/stage-card.tsx` (actions: Open; Retry → resume; Cancel → delivery cancel; no Run), delete `src/components/studio/change-request.tsx` if unused. Test `src/components/agents/__tests__/*.test.tsx`, update affected existing tests.

- [ ] Failing tests: panel lists messages, sends, disables while the stage's chat run is active with "Requirements Agent is working…"; agent message with `change_set_id` opens the preview; gate card renders per kind (`approval` → Approve/Request changes for the revision; `awaiting_answers` → link to questions; `needs_input`, `quota`, `failed` → resume button with message; `stage_busy` → info); plan panel shows stages with included/skipped + rationale and "Default plan used" banner when `fallback`; stage card no longer renders "Run Requirements Analyst". → implement → pass.

### Task 12: 3D agent graph (three.js)

**Files:** `npm i three@0.186.1 @react-three/fiber@9.8.1 @react-three/drei@10.7.9 && npm i -D @types/three@0.186.0`. Create `src/components/agent-graph/{view-model.ts,agent-graph.tsx,scene.tsx,agent-node.tsx,flow-edge.tsx,webgl.ts}`; modify `project-overview.tsx` (render `<AgentGraph />` above the pipeline list). Test `src/components/agent-graph/__tests__/{view-model.test.ts,agent-graph.test.tsx}`. Follow `.claude/skills/threejs-react-three-fiber` and `threejs-performance`.

**Produces:** `buildGraphView(plan: PlanContent | null, workflow: StageState[], delivery: Delivery, events: RunEvent[], nowMs: number) -> {nodes: [{id, label, status: "idle"|"running"|"waiting"|"approved"|"failed"|"stale"|"unavailable", position: [x,y,z]}], edges: [{from, to, active: boolean}]}` — orchestrator hub at origin, planned stages on an arc in plan order, unplanned stages omitted; `active` only if a linking event is < 5000 ms old. `<AgentGraph projectId>`: `next/dynamic` scene with `ssr:false`; "3D | List" toggle (localStorage in try/catch); WebGL unavailable (`webgl.ts: hasWebGL()`) → list only with note; `prefers-reduced-motion` → no pulses/packets. Scene: `<Canvas frameloop="demand" flat>`; nodes = rounded boxes/spheres with brand colours from CSS tokens (read once via `getComputedStyle`); running pulse via `useFrame` + ref + `invalidate()`; packets along active edges; drei `Html` labels, `OrbitControls makeDefault enablePan={false}` with limited polar angle; click → stage route; canvas `aria-hidden`.

- [ ] Failing tests: view-model status mapping for every stage status and gate kind; unplanned omitted; edge active window (4999 ms true, 5001 ms false); toggle persists; no WebGL → list rendered and canvas not mounted; reduced motion flag passed. → implement → pass.
- [ ] Manual check in the running app: parallax-threejs `checkpoint` before/after a stage status change and `diff`; `memcheck` after navigating away (no leaked geometries/materials).

### Task 13: End-to-end, accessibility, docs, real-provider smoke

**Files:** Rewrite `e2e/agentic.spec.ts`; extend `e2e/a11y.spec.ts` (intake, overview list mode); update `e2e/helpers.ts` (`createProject` → intake); READMEs (both), `poc-frontend/CLAUDE.md` priorities, `poc-frontend/docs/agent-contracts.md` (orchestrator + chat contracts).

- [ ] e2e journey (mock provider): intake idea → overview shows plan → requirements gate appears automatically → requirements chat: answer question → approve → architecture runs automatically (no click) → Studio chat "Add Redis between the API and the worker" → preview → apply → approve → thread `DONE`; preferences change → re-plan visible. Also `e2e/auth-and-wizard.spec.ts` updated for intake.
- [ ] Axe: zero serious/critical on intake, requirements, studio, overview (list mode).
- [ ] Real Groq smoke (key present in `.env`): run API + worker with `LLM_PROVIDER=groq`, submit one intake via HTTP, record orchestrator + requirements run provider/model/tokens/outcome. Report honestly if it fails.
- [ ] Full verification: backend `uv run pytest -q && uv run ruff check .`; frontend `npx tsc --noEmit && npm run lint && npx vitest run && npx playwright test`.
