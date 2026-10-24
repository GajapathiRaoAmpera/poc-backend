# Increment C1 — Agentic Core: LangGraph Orchestration, Conversational Intake, Stage Agents, 3D Agent Graph

- **Date:** 2026-10-24
- **Status:** Draft — awaiting review
- **Builds on:** `2026-10-23-agentic-b1-b3-design.md` (agent runtime, Requirements Analyst, Architecture Studio)
- **Repos:** `poc-backend` (FastAPI + MongoDB), `poc-frontend` (Next.js 16)

## 1. Why

A review on 2026-10-24 found that B1–B3 execute real agents (durable Mongo job queue, Groq
structured calls with a repair loop, immutable revisions, approvals, staleness, SSE events,
Architecture Studio with AI change sets) but that the *workflow* is not agentic:

- Intake is a 4-step form wizard; the Requirements Analyst reads the form.
- Stages are a hardcoded list (`orchestration/stages.py: STAGES, DEPENDS_ON`); every project gets
  the same sequence. The "orchestrator" gates and enqueues but never decides what runs next
  ("Agents never chain"). The user clicks "Run" per stage.
- Follow-up questions are answered in form fields, not conversation.
- Plan / Budget / Code agents are placeholders.

C1 replaces the click-through pipeline with an **orchestrated multi-agent graph** while reusing
every B1–B3 component underneath it.

### Owner decisions (2026-10-24)

| Topic | Decision |
|---|---|
| Orchestration engine | **LangGraph** (supersedes "no LangGraph initially"). Sequential stage agents, one graph per project. |
| Agents | One specialized agent per stage; **not** a single chatbot. |
| Progression | **Automatic**: the graph advances on its own and pauses only at approval gates or to ask the user. |
| Stage interaction | **Per-stage chat**: each stage page has a chat with that stage's agent beside its artifact. |
| Agentic feel | **three.js** live 3D agent graph on the project overview, driven by real events. |
| Hosting | LangGraph runs **inside the existing durable worker**, checkpointed to MongoDB. No LangGraph Platform. |

### Success criteria (automated tests unless noted)

1. A user types a project idea into the intake chat; a project is created and the backend runs the
   Orchestrator Agent, which produces a validated, versioned plan specific to that idea.
2. With no further clicks, the graph runs the Requirements Agent; if blocking questions exist it
   pauses and the questions appear in the Requirements chat.
3. Answering in chat resumes the graph (revise run); approving requirements resumes it and the
   Architecture Agent runs automatically; the graph then pauses for architecture approval.
4. A chat message in the Studio ("Add Redis between the API and the worker") produces a proposed
   change set via the Architecture Agent; apply/reject behave as in B3.
5. A worker crash mid-graph resumes from the last checkpoint without re-running completed nodes or
   duplicating revisions.
6. A plan that violates policy is repaired once, then replaced by the safe default plan, labelled
   as such in the UI; the graph never acts on an unvalidated plan.
7. The 3D graph shows only planned stages, reflects real run/pause state within ~1 s of the event,
   and degrades to the accessible list view without WebGL or with reduced motion.
8. All new routes enforce ownership (others get 404); quota still applies; no test calls Groq.

## 2. Scope

**In:** LangGraph delivery graph; Orchestrator Agent + policy validator; conversational intake;
per-stage chat for Requirements and Architecture; wizard demoted to optional preferences; 3D agent
graph; Planning/Budget nodes present in the graph topology but marked unavailable.

**Out (own specs):** C2 Planning + Budget agents (parallel after architecture approval, scoped
recalculation); C3 Development / Validation / Repair agents and sandbox; document upload.

**Pre-existing work to land first:** the uncommitted SSE `no-transform` fix (backend) and the
`e2e/agentic.spec.ts` failure (frontend) — the plan's first task investigates and fixes these
before new behaviour is added.

## 3. Delivery graph (LangGraph)

### 3.1 Dependencies

- `langgraph` 1.2.x, `langgraph-checkpoint-mongodb` 0.5.x (`AsyncMongoDBSaver`; requires
  `pymongo>=4.18.2` — bump the pin). **Fallback** if its transitive deps (`langchain-mongodb`)
  conflict: a ~150-line `BaseCheckpointSaver` implementation on our own collections; the plan's
  dependency task decides and records the choice.
- No LangChain chat-model wrappers: nodes call the existing `LLMProvider` (Groq / mock).

### 3.2 State

```
class DeliveryState(TypedDict):
    project_id: str
    plan_version: int | None            # pointer into `plans`
    artifacts: dict[str, {artifact_id, revision, approved_revision}]   # refs only, never content
    pending: {kind, stage, payload} | None   # what the graph is paused for
    loops: dict[str, int]               # automatic re-runs per stage (policy budget)
    last_event: {stage, outcome, run_id} | None
```

Content stays in the artifact store; the checkpoint stays small and revisions keep their audit trail.

### 3.3 Topology

```
START ─▶ orchestrator ─┬─▶ requirements ─▶ orchestrator
                       ├─▶ architecture ─▶ orchestrator
                       ├─▶ gate (⏸ interrupt) ─▶ orchestrator
                       └─▶ END
```

`gate` is one interrupt node whose `pending.kind` is one of `intake_questions` (orchestrator's
blocking `missing_info`), `awaiting_answers` (requirements blocking questions), `approval`
(a stage draft awaits approval), `needs_input` (loop budget
exhausted), `stage_busy` (a chat run is active on the next stage), `quota` (daily quota reached).
`failed` and `cancelled` are set by the worker, not by an interrupt. Planning/Budget/Code nodes
are added in C2/C3.

- **`orchestrator`** is planner + router:
  - **Planner (LLM call, §4)** only when the plan may need to change: no plan yet, a new intake
    message or preferences change (`trigger = user_message`), requirements newly approved
    (re-plan with real requirements), or a stage failed non-retryably.
  - **Router (deterministic, pure function)** otherwise — walk the plan's included stages in
    dependency order and pick the first that is not done: no draft / stale / changes requested →
    run the stage (mode `generate` or `revise`); blocking questions → `gate awaiting_answers`;
    draft not approved → `gate approval`; all approved → `END`. Every run through a stage
    increments `loops[stage]`; a user action resets it; `loops[stage] > 3` → `gate needs_input`.
- **Stage nodes** (`requirements`, `architecture`) call the existing agent functions
  (`agents/requirements/agent.py`, `agents/architecture/agent.py`) unchanged in contract.
- **`⏸` nodes** call `interrupt(payload)`; `payload` says what is needed and is stored on the
  thread for the UI.
- The graph never advances past a stage that has an active (QUEUED/RUNNING) chat run (§5).

### 3.4 Execution on the existing queue

- New job type `graph_step` (existing `jobs` gain `type: "agent_run" | "graph_step"`; missing
  `type` means `agent_run` for backward compatibility). A `graph_step` job invokes the compiled
  graph with `thread_id = project_id` until it pauses or ends.
- `jobs.run_id` becomes optional: the unique `run_id` index is replaced by a partial unique index
  (only where `run_id` is a UUID), and `ensure_indexes` drops the old `run_id_1` index first.
  A partial unique index on `idempotency_key` (`"graph:<project_id>"`, unset when the job is
  DONE) guarantees one queued/running graph job per project; a duplicate resume is a no-op.
- Each stage node records an `agent_run` (stage, agent, mode, inputs, attempts, provider/model,
  usage, events) created idempotently with `graph_key = "<project_id>:<langgraph_step>:<node>"`
  (unique sparse) so a re-executed node after a crash reuses the same run: a `SUCCEEDED` run
  returns its stored output without calling the model again. Orchestrator planning calls are
  recorded the same way as runs of agent `project_orchestrator` (stage `orchestrator`) and count
  toward quota. The activity drawer, run detail and SSE are unchanged.
- Errors: a retryable `ProviderError` / timeout fails the `graph_step` job attempt; the job retries
  with existing backoff and LangGraph resumes from the last checkpoint (completed nodes are not
  re-run). Non-retryable failures end the agent run `FAILED` and route to `orchestrator`, which
  decides (ask the user, retry within the loop budget, or pause at `orchestrator_failed`).
- Cancel: `POST …/delivery/cancel` sets `cancel_requested` on the active run; the worker stops at
  the next checkpoint; the thread status becomes `PAUSED` (kind `cancelled`).

### 3.5 Thread record

`delivery_threads` (unique `project_id`): `status: IDLE | RUNNING | PAUSED | DONE | FAILED`,
`pending {kind, stage, payload}`, `plan_version`, `last_error`, `updated_at`. Written by the worker
at each pause/end so the API and UI never need to read raw checkpoints.

### 3.6 Resuming

All user actions call one function, `delivery.notify(db, project, trigger, message=None)`:

| Thread status | Effect of `notify` |
|---|---|
| `PAUSED` at a gate (any kind) | enqueue `graph_step` with `Command(resume={"trigger": trigger, "message": message})` |
| `DONE` | enqueue `graph_step` with fresh input `{"trigger": trigger}` (same thread, state kept) |
| `RUNNING` / queued | no-op (the router reads current facts on its next pass) |
| `FAILED` / `PAUSED cancelled` | no-op; only `POST …/delivery/resume` restarts |

Callers: approve, request-changes, answers (endpoint or chat), preferences save, intake message
(`trigger = user_message`), chat run finished (`trigger = chat_finished`). Because the router
derives the next step from stored facts, resuming at any gate is safe: it re-pauses if nothing
changed. Staleness re-enters naturally: a stale approved stage is "not done" for the router.

### 3.7 What changes in B1–B3 code

- `stages.py`: `STAGES`/`DEPENDS_ON` no longer decide progression; they remain the policy's
  dependency table and the derived-status fallback. Derived stage status additionally reports
  `NOT_PLANNED` (excluded by the plan) and uses `pending` for `WAITING_FOR_*`.
- `orchestration/service.start_stage` is kept for chat runs only; forward progress is the graph's.
- Stage-card "Run" buttons are replaced by "Open" + chat; "Retry" maps to `delivery/resume`.

## 4. Orchestrator Agent and policy

### 4.1 Intake

- New project creation is one box: *"What do you want to build?"* `POST /projects/intake
  {message, preferences?}` creates the project (name = orchestrator's `project_name`, initially
  the first 60 chars of the message), the intake conversation, the thread, and enqueues the first
  `graph_step` (202).
- The wizard becomes an optional **Preferences** panel (budget range, target date, preferred
  technologies, deployment, compliance, integrations, team assumptions) on the project; never
  required. Existing `/projects/new` wizard route redirects to intake; `/projects/[id]/edit`
  becomes Preferences.

### 4.2 Output schema `plan/1.0`

```
project_name: str (≤160)
objective_summary: str (≤1000)
project_type: web_app | mobile_app | data_pipeline | integration | internal_tool | other
brief: {business_problem, target_users[], goals[], expected_outcomes[],
        functional[], non_functional[], expected_scale, constraints{...}}   # = ProjectSpecification
stages: [{stage, include: bool, rationale (≤300), inputs: [stage], approval_required: bool}]
missing_info: [{id: "I-001", question, why_it_matters, blocking: bool}]   # ≤7
next_action: run_stage | ask_user | wait_approval | done
next_stage: stage | null
decision_summary: str (≤600)            # shown in UI; no hidden reasoning requested or shown
```

- `brief` is saved as a new **project specification revision** (origin `orchestrator`), so the
  Requirements Agent keeps reading a specification exactly as today and spec→requirements
  staleness keeps working. User preferences are merged in and win on conflict.
- Called only on the planner triggers in §3.3, with a compact state digest (stage statuses,
  artifact summaries, intake messages, answers) — never full artifact content. A new
  specification revision is written only when the merged brief differs from the latest one
  (otherwise every re-plan would mark requirements stale).
- Preferences are stored on the project (`projects.preferences`, the `Constraints` model) via
  `PUT /projects/{id}/preferences` and merged into `brief.constraints` (user values win).

### 4.3 Policy validator (deterministic; errors trigger the repair loop)

1. Only implemented agents may be `include: true` (C1: `requirements`, `architecture`).
2. Dependency order is fixed: requirements → architecture → (plan, budget) → code; `inputs`
   must be a subset of earlier included stages.
3. `approval_required` is forced `true` for requirements and architecture; the model may add
   approval to other stages, never remove it.
4. `next_stage` must be included, its inputs approved and not stale; `next_action` must be
   consistent with facts (e.g. `wait_approval` only if a draft awaits approval).
5. Loop budget: ≤3 automatic re-runs per stage, then the graph pauses (`needs_input`).
6. ≤7 questions per turn; quota checked before any agent node runs.

After one failed repair the **safe default plan** is used (all implemented stages, all approvals,
next action derived from facts) with `fallback: true`; the UI states "Default plan used — the
Orchestrator's plan failed validation" with the issues.

### 4.4 Persistence

`plans` (unique `(project_id, version)`): `content`, `validation`, `fallback`, `run_id`,
`created_at`. Every orchestrator call that changes the plan writes a version; the overview shows
the current plan with per-stage rationale.

## 5. Stage chats

### 5.1 Model

`conversations` (unique `(project_id, channel)`; channel `intake | requirements | architecture`)
and `messages`: `conversation_id`, `project_id`, `owner_id`, `seq` (per conversation),
`role: user | agent | system`, `agent`, `text` (≤4000), `refs: {run_id?, artifact_revision?,
change_set_id?, question_ids?}`, `created_at`. Agent messages are written only by agent code from
real outputs; system messages record graph events ("Requirements approved — Architecture Agent
started").

### 5.2 Turn

`POST /projects/{id}/conversations/{channel}/messages {text}` → stores the user message and starts
a chat run (existing `start_stage` path, new `RunMode` value `chat`, quota + one-active-run rules) → 202.
The stage agent receives the current revision, the last 20 messages, and the instruction; it
returns structured output:

- **Requirements** `{reply, action: none | answer_questions | revise, answers?: {Q-id: text},
  instruction?}` — `answer_questions` records answers (new revision, origin `user`, as today);
  `revise` runs the existing revise mode producing a new draft revision.
- **Architecture** `{reply, action: none | propose_changes | generate, ops?, instruction?}` —
  `propose_changes` creates a change set exactly as B3 §6.3; `generate` produces a new draft
  revision (allowed once requirements are approved), honouring the instruction (this is
  "describe the architecture in natural language").
- **Intake** messages go to the orchestrator via graph resume (§3.6), not a chat run.

The agent's `reply` becomes the agent message, with refs to whatever it produced. Model output
never approves anything; approval stays an explicit button.

### 5.3 UI

Requirements page and Architecture Studio gain a right-side **Agent chat** panel (Studio: tab
alongside Inspector; drawer below 1024 px). Messages stream via SSE (`message` events); agent
messages show produced artifacts as links ("Draft r6", "Change set: 3 changes — Preview"). The B3
"Describe a change…" box becomes this chat. Pending gates appear as a pinned system card with the
approve / request-changes action.

## 6. 3D agent graph (three.js)

- Libraries: `three` 0.186, `@react-three/fiber` 9.8, `@react-three/drei` 10.7 (exact pins in the
  plan). Built following the `cloudai-x/threejs-skills` skills (MIT; r186 / R3F 9.8 / drei 10.7),
  installed project-scoped in `poc-frontend/.claude/skills/`: `frameloop="demand"`, refs not
  `setState` in `useFrame`, delta-based motion, drei `OrbitControls makeDefault`. Lazy-loaded (`next/dynamic`, `ssr: false`) on the project overview only.
- **Scene:** one node per planned stage plus the Orchestrator at the hub; edges follow the plan's
  `inputs`. Unplanned stages are not drawn; unavailable ones are ghosted.
- **State mapping (pure function, unit-tested)** from workflow + thread + recent events:
  idle = gray `#969696`; running = magenta `#9C369A` with a slow pulse; waiting for user =
  outlined magenta; approved = navy `#000F5C`; failed = muted red; stale = dashed outline.
  Packets travel along an edge only when a real event links them (e.g. requirements approved →
  architecture run started) within the last 5 s. No ambient fake activity.
- **Interaction:** orbit (limited), hover tooltip (stage, status, latest revision, last run),
  click → stage page; keyboard users use the list view.
- **Accessibility / fallback:** the canvas is `aria-hidden`; the existing pipeline list stays as
  the accessible equivalent and a "3D / List" toggle (remembered per viewer). No WebGL or
  `prefers-reduced-motion` → static rendering or list view. Restrained styling per brand rules
  (no neon, bloom or gradients).
- **Verification:** parallax-threejs `checkpoint`/`diff` for state changes and `memcheck` for
  geometry/material disposal when the view unmounts.

## 7. API (under `/api/v1`; project routes use `get_project_for`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/intake` | create project from idea, start graph (202) |
| GET | `/projects/{id}/delivery` | thread status, pending gate, current plan |
| POST | `/projects/{id}/delivery/resume` · `/delivery/cancel` | retry after failure · cancel |
| GET | `/projects/{id}/plans` · `/plans/{version}` | plan history |
| GET | `/projects/{id}/conversations/{channel}/messages?after=` | history |
| POST | `/projects/{id}/conversations/{channel}/messages` | send (202) |
| PUT | `/projects/{id}/preferences` | optional preferences (spec revision) |

Existing routes stay; approve/request-changes/answers additionally resume the graph (§3.6).
SSE gains event types `message` and `graph` (thread status/pending changes). New error codes:
`GRAPH_BUSY` (409, resume while running), `NOT_PAUSED` (409).

## 8. Security

Unchanged posture from B1–B3: ownership on every route and every lookup (`project_id` filters on
threads, plans, conversations, messages); Origin check on mutations; chat text and preferences are
passed to models inside delimited data blocks; model output is schema- and policy-validated and
only ever produces drafts, change sets, plans or messages; checkpoints contain refs, not secrets;
`GROQ_API_KEY` never leaves the server.

## 9. Testing

Backend (pytest, mock provider, local Mongo replica set):
- Policy validator: every rule, repair then fallback.
- Graph routing with scripted mock outputs: happy path; blocking questions → pause → answer →
  revise; changes requested → revise (loop budget exhausted → `needs_input`); plan excludes a stage;
  stage failure → orchestrator decision; staleness re-entry.
- Interrupt/resume via each user action; duplicate resume is a no-op; `GRAPH_BUSY`/`NOT_PAUSED`.
- Crash mid-graph (kill after a node, expire lease) → resume from checkpoint, no duplicate run or
  revision; retryable provider error retries the job.
- Chat turns per action for both stages; one active run per stage; quota.
- Isolation: user B gets 404 on every new route; SSE carries only own messages.

Frontend (Vitest): intake chat; stage chat panels (send, stream, refs, pending card); graph state
mapping function; 3D/List toggle and WebGL/reduced-motion fallback.
Playwright (mock provider): idea → plan visible → requirements questions in chat → answer →
approve → architecture runs automatically → Studio chat change → preview → apply → approve;
axe on intake, requirements, studio, overview (list mode).

## 10. Risks

- **LangGraph + our queue:** two layers of retry. Mitigation: nodes do not retry internally;
  only the job retries, and LangGraph resumes from the checkpoint.
- **`langgraph-checkpoint-mongodb` deps** (pulls `langchain-mongodb`, needs pymongo ≥4.18.2) →
  fallback saver (§3.1).
- **Automatic progression spends quota** → loop budgets, quota check per node, and a hard stop at
  every approval gate.
- **Orchestrator quality variance** → policy validator + safe default plan; every plan visible.
- **3D view cost on low-end devices** → lazy load, list default when WebGL missing, dispose on
  unmount.
