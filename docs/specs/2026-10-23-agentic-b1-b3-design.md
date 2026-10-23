# Sub-projects B1–B3 — Agent Runtime, Requirements Analyst, Architecture Studio: Design Spec

- **Date:** 2026-10-23
- **Status:** Draft — awaiting review
- **Builds on:** `2026-10-23-foundation-design.md` (incl. the MongoDB addendum)
- **Repos:** `poc-backend` (FastAPI + MongoDB), `poc-frontend` (Next.js 16)

## 1. Goal

Turn the platform from intake forms into a governed agentic workflow: after a project specification is captured, an **orchestrator** runs **specialized agents** in durable background jobs, every output is a **versioned, schema-validated artifact**, humans **approve** artifacts before downstream work starts, upstream changes mark downstream work **stale**, and the UI shows real agent activity — never fabricated.

B1–B3 deliver the first two agents end-to-end plus the **Architecture Studio** (highest priority). Planning/Budget (C) and Code generation + sandbox (D) get their own specs; their stages appear in the UI as not-yet-available.

### Success criteria (all verified by automated tests unless noted)

1. A user runs the Requirements Analyst on a project; a worker process executes it; the UI shows queued → running → result with real events, provider and model.
2. When information is missing, the analyst asks focused questions; the user answers; re-running produces a new revision that keeps existing requirement IDs.
3. The user edits and approves a requirements revision. Approved revisions cannot be modified (API refuses; edits create a new revision).
4. Architecture generation is refused until requirements are approved; once approved, the Solution Architect produces a diagram linked to requirement IDs.
5. In the Architecture Studio the user adds/moves/connects/deletes components, edits properties, undoes/redoes, auto-lays out, validates, saves, reloads without loss, compares two revisions, restores an old one, and approves a revision.
6. A natural-language request ("Add Redis between the API and the worker") produces a **proposed change set**, validated server-side, previewed on the canvas with an impact summary, and only applied (as a new revision) on user confirmation; rejecting it changes nothing.
7. Approving a new requirements revision (or changing the project specification) marks the architecture **stale** with the reason shown.
8. A worker crash mid-run does not lose the job; retries are bounded; cancel works; duplicate start requests don't create duplicate runs.
9. Users cannot see or act on other users' projects, runs, artifacts or events (404); quotas are enforced.
10. Tests use a deterministic mock provider; no test makes a paid model call. A real Groq call is verified manually once the key is present.

## 2. Decisions

| Topic | Decision | Why |
|---|---|---|
| Database | MongoDB (owner decision 2026-10-23). `CLAUDE.md`/docs updated accordingly. | Already in production use on Atlas. |
| Roles/tenancy | Unchanged: `USER` / `SUPER_ADMIN`, private projects. | Owner decision; supersedes the 7-role text in `CLAUDE.md`. |
| Job execution | MongoDB-backed job queue + separate worker process (`python -m app.worker`). No Redis/Celery. | Durable, transactional with artifact writes, one fewer service. |
| Orchestration | Own explicit state machine (no LangGraph). | Strict, testable transitions and gates; agents are single-shot structured calls in B. |
| LLM provider | `LLMProvider` interface; **Groq** adapter (OpenAI-compatible Chat Completions, `response_format: json_schema`, best-effort); **mock** adapter for tests/offline demo only. | Provider independence; Groq structured outputs don't support streaming or tools. |
| Default model | `GROQ_MODEL=openai/gpt-oss-120b` (configurable; must be a model listed by Groq as supporting `json_schema`). | Documented structured-output support. |
| Diagram editor | `@xyflow/react` 12.x (React Flow) + `elkjs` 0.12 for auto-layout; editor state in a reducer (no new state library). | Proven node editor; ELK handles layered architecture layouts. |
| Live progress | Server-Sent Events, polling fallback. | One-way progress stream; works through the Next.js rewrite. |

## 3. Out of scope for B1–B3

Project Manager / Budget agents and screens (C), Developer / Validation / Repair agents and sandbox (D), document upload, sequence/ER/deployment diagram editors (the schema reserves a `diagram_type` field), PNG/SVG/Mermaid export (JSON export only in B; image export later), workspaces, email.

## 4. Agent runtime (B1)

### 4.1 Concepts

- **Stage** — a step of the delivery workflow for one project: `requirements`, `architecture` (B); `plan`, `budget`, `code` (reserved, shown as unavailable).
- **Artifact** — the single per-project, per-kind container (`requirements`, `architecture`).
- **Revision** — an immutable version of an artifact's content, with provenance.
- **Agent run** — one execution of an agent for a stage (may have several attempts).
- **Job** — the durable queue entry that drives an agent run.
- **Change set** — an AI-proposed, not-yet-applied edit to an architecture revision.

### 4.2 Collections

All documents carry `project_id` and `owner_id` (copied from the project) so every query is ownership-scoped. UUID `_id`s.

**`artifacts`** — unique `(project_id, kind)`.
`kind`, `latest_revision`, `approved_revision` (nullable), `stale` (bool), `stale_reason` (nullable), `updated_at`.

**`artifact_revisions`** — unique `(artifact_id, revision)`; unique sparse `run_id` (idempotent agent writes).
`kind`, `revision` (1..n), `schema_version`, `content` (validated per kind), `sources: [{kind, artifact_id|null, revision}]` (e.g. `{kind: "specification", revision: 3}`), `origin: "agent" | "user" | "change_set" | "restore"`, `run_id` (nullable), `change_set_id` (nullable), `restored_from` (nullable), `summary` (one-line description of the change), `validation: {ok, issues: [{severity: error|warning, code, message, path}]}`, `created_by`, `created_at`.
**Never updated after insert.** Approval is recorded in `approvals` and `artifacts.approved_revision`.

**`approvals`** — `artifact_id`, `kind`, `revision`, `decision: APPROVED | CHANGES_REQUESTED`, `comment`, `decided_by`, `decided_at`.

**`agent_runs`** — `stage`, `agent` (`requirements_analyst` | `solution_architect`), `agent_version`, `mode` (`generate` | `revise` | `change_request`), `status` (see 4.3), `inputs: [{kind, artifact_id|null, revision}]`, `instruction` (change requests only), `output: {artifact_id, revision} | {change_set_id} | null`, `provider`, `model`, `attempts: [{n, started_at, finished_at, outcome, error_code, usage}]`, `usage: {input_tokens, output_tokens}`, `summary` (agent's decision summary), `error: {code, message, retryable} | null`, `created_by`, `created_at`, `started_at`, `finished_at`, `cancel_requested` (bool).

**`run_events`** — unique `(project_id, seq)`; `run_id`, `seq` (per-project monotonic, allocated with `$inc` on a counter document), `ts`, `type` (`status` | `step` | `validation` | `error`), `message` (human-readable, no secrets, no chain-of-thought), `data` (small structured payload). TTL: none (audit trail).

**`jobs`** — unique sparse `idempotency_key`; index `(status, run_after)`.
`run_id`, `type`, `status: QUEUED | RUNNING | DONE`, `attempts`, `max_attempts` (3), `run_after`, `lease_owner`, `lease_expires_at`, `timeout_s` (180), `last_error`.

**`change_sets`** — `artifact_id`, `base_revision`, `run_id`, `status: PROPOSED | APPLIED | REJECTED | INVALID | SUPERSEDED`, `ops` (4.6), `summary`, `impact: {added, removed, modified, requirement_ids}`, `validation`, `applied_revision` (nullable).

**`counters`** — `{_id: "events:<project_id>", seq}`.

### 4.3 Run and stage state machines

**Run status:** `QUEUED → RUNNING → {SUCCEEDED | FAILED | CANCELLED}`; `RUNNING → QUEUED` only via retry/lease expiry. Transitions are enforced by conditional updates (`find_one_and_update` with the expected current status); an illegal transition is a server error and is logged.

**Stage status** is *derived* (not stored) from the latest run, the artifact and dependencies, so it can never drift:

| Stage status | Condition |
|---|---|
| `BLOCKED` | a dependency stage is not `APPROVED` (reason lists which) |
| `NOT_STARTED` | no runs, no artifact |
| `QUEUED` / `RUNNING` | latest run in that status |
| `FAILED` / `CANCELLED` | latest run ended so and no newer revision exists |
| `WAITING_FOR_INPUT` | latest revision has unanswered **blocking** open questions (requirements) |
| `WAITING_FOR_APPROVAL` | latest revision exists, not approved, validation has no errors |
| `NEEDS_REVISION` | latest revision has validation errors, or the latest decision is `CHANGES_REQUESTED` |
| `APPROVED` | `approved_revision == latest_revision` and not stale |
| `STALE` | artifact `stale == true` (overrides `APPROVED`/`WAITING_*`) |

Dependencies: `requirements ← specification`; `architecture ← requirements (approved)`.

### 4.4 Orchestrator

`app/modules/orchestration/` — the only code that decides *what runs next*; agents never call each other.

- `start_stage(project, stage, mode, user, instruction=None) -> AgentRun`:
  1. Authorize (owner or Super Admin).
  2. Check dependencies → `409 STAGE_BLOCKED` with reasons.
  3. Enforce quota: runs created by the user in the last 24 h `< (user.daily_run_limit ?? PLATFORM_DAILY_RUN_LIMIT=50)` → `429`.
  4. Resolve inputs to **approved** upstream revisions (spec: latest spec revision).
  5. Idempotency: if an active run (`QUEUED`/`RUNNING`) exists for this stage → return it (`200`, not a duplicate). Job key `run:<run_id>`.
  6. Insert run + job in one transaction; emit `status` event.
- `approve(artifact, revision, user, comment)`: revision must be the latest, have no validation errors, not be stale, and (requirements) have no unanswered blocking questions → otherwise `409` with reason. In one transaction: insert approval, set `approved_revision`, clear `stale`, then **propagate staleness** to dependents whose approved/latest revision's `sources` reference an older revision of this artifact (`stale_reason`: "Requirements changed: approved revision 4 replaces 3 used by this architecture").
- `request_changes(...)`: records `CHANGES_REQUESTED` with comment.
- Specification save (existing endpoint) additionally marks `requirements` stale if its latest revision was built from an older spec revision.
- `cancel(run)`: `QUEUED` → `CANCELLED` immediately; `RUNNING` → `cancel_requested=true` (worker stops at the next checkpoint, ≤ heartbeat interval).
- `retry(run)`: only `FAILED`/`CANCELLED` → creates a **new** run with the same inputs (history preserved).

### 4.5 Worker

`python -m app.worker` (one or more processes).

- **Claim:** `find_one_and_update({status: QUEUED, run_after ≤ now} OR {status: RUNNING, lease_expires_at < now}, set RUNNING + lease_owner + lease_expires_at = now+60s, inc attempts)`, sorted by `run_after`.
- **Heartbeat** every 20 s extends the lease and checks `cancel_requested`.
- **Timeout:** `asyncio.wait_for(job, timeout_s)`.
- **Retry policy:** transient errors (provider timeout, 429, 5xx, network) → `QUEUED` with `run_after = now + 2^attempt·5s` (±20% jitter) until `max_attempts`; non-retryable (schema still invalid after repairs, provider 4xx other than 429, validation of inputs) → run `FAILED` immediately.
- **Crash recovery:** an expired lease is reclaimed by any worker; the agent write is idempotent (unique `run_id` on revisions/change sets), so a re-run after a crash never creates two revisions.
- Poll interval 1 s when idle; graceful shutdown on SIGTERM (finishes or releases the current lease).

### 4.6 Agents and provider gateway

```
class LLMProvider(Protocol):
    name: str
    model: str
    async def generate(self, *, system: str, user: str, schema: type[BaseModel],
                       max_output_tokens: int, timeout_s: float) -> LLMResult  # parsed + usage
```

- **Groq adapter:** `POST https://api.groq.com/openai/v1/chat/completions` via `httpx`, `response_format={"type":"json_schema","json_schema":{"name":…, "schema": <pydantic JSON schema>}}` (best-effort mode), temperature 0.2. Key from `GROQ_API_KEY` (server env only, never logged, never sent to the browser). Errors mapped to `ProviderError(retryable=…)`.
- **Mock adapter:** returns deterministic fixtures per agent/mode, can be scripted in tests to fail, time out, or return invalid JSON. Selected by `LLM_PROVIDER=mock`; **refused at startup when `ENVIRONMENT=production`**. Every run made with it is labelled "Mock provider" in the UI.
- **Agent contract:** `Agent.run(ctx) -> AgentOutput` where `ctx` holds typed, approved inputs. Output is validated with Pydantic **and** domain validation (IDs, references). On failure the agent sends one **repair** prompt containing the validation errors; max **2 repairs**, then `FAILED (OUTPUT_INVALID)`.
- **Prompt safety:** user/project text is passed inside a delimited data block; the system prompt states it is data, not instructions. Model output never triggers actions directly — it is only ever stored as a draft revision or a proposed change set. Outputs are size-capped.
- **Transparency:** each run emits `step` events (e.g. "Loaded approved requirements r4 (12 requirements)", "Called groq/openai/gpt-oss-120b", "Output failed validation: edge e7 references unknown node — requesting repair (1/2)", "Saved architecture revision 3"). The agent's `summary` field is a short decision summary written by the model as part of its structured output; no hidden reasoning is requested or shown.

## 5. Requirements Analyst (B2)

**Input:** latest project specification revision; optional previous requirements revision + answers (mode `revise`).

**Content schema `requirements/1.0`:**
```
summary: str
goals: [str]
personas: [{name, description}]
requirements: [{
  id: "FR-001" | "NFR-001",       # ^(FR|NFR)-\d{3}$, unique
  type: functional | non_functional,
  title, description,
  priority: MUST | SHOULD | COULD | WONT,
  acceptance_criteria: [str] (≥1 for MUST),
  source: stated | inferred | assumption,
}]
assumptions: [str]
open_questions: [{id: "Q-001", question, why_it_matters, blocking: bool, answer: str | null}]
risks: [{description, likelihood: low|medium|high, mitigation}]
dependencies: [str]
```
Limits: ≤ 150 requirements, ≤ 30 open questions, text fields capped.

**Rules:** in `revise` mode, IDs of retained requirements must be preserved; removed IDs are never reused (server checks against all prior revisions). Questions are focused (≤ 7 per run, blocking only when the answer materially changes scope or architecture).

**Answers:** `PUT …/requirements/answers {question_id: answer}` stores answers on a **new revision** (origin `user`, content unchanged except answers) — revisions stay immutable. "Re-run with answers" starts a `revise` run.

**UI (`/projects/[id]/requirements`):** summary and goals; requirements table (filter by type/priority, inline edit → save as new revision); open-questions panel with answer fields and "Re-run with answers"; assumptions and risks; revision selector; validation issues; Approve / Request changes; run status and activity.

## 6. Architecture Studio (B3 — highest priority)

### 6.1 Canonical diagram schema `architecture/1.0`

```
schema_version: "1.0"
diagram_type: "system"            # reserved: "deployment" | "sequence" | "erd" (not in B)
nodes: [{
  id: "n_<slug>",                 # ^[a-z][a-z0-9_]{1,40}$, unique
  type: client | frontend | backend | service | worker | database | cache | queue |
        storage | external_api | auth | gateway | monitoring | cdn | deployment
  label (≤80), description (≤500), technology (≤80),
  responsibilities: [str] (≤10),
  config: {str: str} (≤20 entries),
  requirement_ids: [str],
  position: {x, y}, group: str | null,
}]
edges: [{
  id: "e_<slug>", source: node id, target: node id,
  label (≤80), protocol: str | null (e.g. HTTPS, gRPC, AMQP, SQL), data: str | null,
  requirement_ids: [str],
}]
groups: [{id, label}]             # trust boundaries / environments
decisions: [{id: "ADR-001", title, decision, alternatives: [str], rationale, requirement_ids}]
notes: [{id, text, position}]     # annotations
viewport: {x, y, zoom}
```
Limits: ≤ 200 nodes, ≤ 400 edges, ≤ 30 groups, ≤ 50 decisions.

**Server validation** (shared by save, change-set apply, agent output):
- *Errors (block save):* schema violations, duplicate IDs, edge endpoints referencing unknown nodes, self-loops, nodes in unknown groups, exceeding limits.
- *Errors (block approval only):* requirement IDs not present in the source requirements revision; `MUST` requirements not referenced by any node, edge or decision.
- *Warnings:* isolated nodes, no `auth` node when any requirement mentions login/users, no `monitoring` node, duplicate edges between the same pair with the same label.

### 6.2 Revisions, compare, restore

- **Save:** `POST …/architecture/revisions {base_revision, content, summary}` → new revision (origin `user`). If `base_revision != latest_revision` → `409 CONFLICT` with the latest revision number (no silent overwrite).
- **Compare:** `GET …/architecture/compare?from=2&to=5` → structural diff: nodes/edges/groups/decisions added, removed, and modified (field-level), ignoring position-only changes unless `include_layout=true`.
- **Restore:** `POST …/revisions/{n}/restore` → new revision copying `n` (origin `restore`, `restored_from: n`).
- **Approve:** as in 4.4; approved revisions are immutable (editing an approved revision starts from it and saves a new one).

### 6.3 AI change requests

1. `POST …/architecture/change-requests {instruction, base_revision}` → starts a `solution_architect` run in `change_request` mode (202).
2. The agent returns **ops** against the base: `add_node`, `update_node {id, fields}`, `remove_node {id}` (removes attached edges), `add_edge`, `update_edge`, `remove_edge`, `add_group`, `add_decision`, plus `summary`.
3. The server applies the ops **in memory** to the base, validates the result, computes `impact`, and stores a `change_set` (`PROPOSED`, or `INVALID` with issues — never applied).
4. UI previews it: canvas overlay (added = green outline, modified = amber, removed = red dashed), an ops list, and the impact summary.
5. `POST …/change-sets/{id}/apply` → only if `base_revision` is still latest (else `409`, change set → `SUPERSEDED`; user can re-request) → new revision (origin `change_set`). `…/reject` → `REJECTED`, no revision.
6. Manual edits since the base are therefore never overwritten.

### 6.4 Studio UI (`/projects/[id]/architecture`)

Three panels; below 1024 px the side panels become drawers (canvas keeps full width).

- **Left — Component library:** searchable list grouped by category (Clients, Application, Data, Integration, Security, Operations), each with icon and description; drag onto the canvas or press Enter to add at centre.
- **Centre — Canvas (React Flow):** pan, zoom, fit view, minimap, select / multi-select (shift-drag), connect and reconnect, move, duplicate (Ctrl+D), delete (Del), group, annotations, **undo/redo** (Ctrl+Z / Ctrl+Shift+Z, 100 steps), **auto-layout** (ELK layered, left→right), visible selection and connection feedback, validation markers on nodes/edges; empty state with "Generate architecture" when no revision exists.
- **Right — Inspector:** selected node/edge fields (name, type, technology, description, responsibilities, config, requirement links with titles), connections in/out, validation issues; edits are staged with Save/Cancel per field group. With nothing selected: diagram summary, ADRs, requirement coverage.
- **Toolbar:** revision indicator (`r5 · draft`, `approved`, `stale`), unsaved-changes dot, Save (summary prompt), Validate, Auto-layout, Undo/Redo, History (drawer: list, compare two, restore), AI request box ("Describe a change…"), Approve / Request changes, Export JSON.
- **Keyboard:** all actions reachable by keyboard; nodes are focusable with arrow-key nudge; announcements via a polite live region.

## 7. Activity and pipeline UI (B1)

- **Project overview pipeline** becomes live: each stage card shows status (4.3), latest revision, last run, stale reason, and contextual actions (Run, Open, Approve, Retry, Cancel). Plan/Budget/Code remain "Available in a coming release".
- **Activity drawer** (per project): runs (agent, mode, status, started, duration, provider/model or "Mock provider", tokens), expandable event timeline, error with actionable message and Retry/Cancel. Fed by SSE `GET /projects/{id}/events?after=<seq>`; on SSE failure falls back to polling every 3 s.
- **Approvals:** list per project with decision, revision, approver, time, comment.

## 8. API (all under `/api/v1/projects/{project_id}`; every route uses `get_project_for`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/workflow` | stages with derived status, reasons, artifacts, latest runs |
| POST | `/stages/{stage}/runs` `{mode}` | start a run (202) |
| GET | `/runs` · `/runs/{run_id}` | list (paginated) · detail with events |
| POST | `/runs/{run_id}/cancel` · `/runs/{run_id}/retry` | |
| GET | `/events?after=` | SSE stream (`text/event-stream`), heartbeat comment every 15 s |
| GET | `/artifacts/{kind}` | artifact + latest revision + approved revision number |
| GET | `/artifacts/{kind}/revisions` · `/revisions/{n}` | history · one revision |
| POST | `/artifacts/{kind}/revisions` | user edit → new revision (`base_revision` required) |
| POST | `/artifacts/{kind}/validate` | validate unsaved content (no write) |
| POST | `/artifacts/{kind}/revisions/{n}/restore` | |
| POST | `/artifacts/{kind}/revisions/{n}/approve` · `/request-changes` | gates |
| GET | `/artifacts/architecture/compare?from=&to=` | structural diff |
| PUT | `/artifacts/requirements/answers` | answers → new revision |
| POST | `/artifacts/architecture/change-requests` | AI change request → run (202) |
| GET | `/change-sets/{id}` · POST `/change-sets/{id}/apply` · `/reject` | |
| GET | `/approvals` | |

Errors use the existing envelope; new codes: `STAGE_BLOCKED` (409), `REVISION_CONFLICT` (409), `NOT_APPROVABLE` (409), `QUOTA_EXCEEDED` (429), `PROVIDER_UNAVAILABLE` (503, only on synchronous checks).

## 9. Security

- Every new route goes through `get_project_for` (owner or Super Admin; others 404). Run, event, artifact and change-set lookups also filter by `project_id`.
- Mutations still require the Origin check; SSE is GET.
- `GROQ_API_KEY` lives only in server env; startup logs the provider and model, never the key. Provider errors are mapped to generic messages before reaching the client.
- Prompt-injection posture per 4.6; model output size-capped and schema-validated before persistence.
- Per-user daily run quota (default 50, `daily_run_limit` overrides; Super Admin can change it).
- No code execution in B.

## 10. Testing

Backend (pytest, mock provider, local Mongo):
- Run state machine: legal/illegal transitions; stage status derivation table (every row).
- Worker: claim/lease, heartbeat, lease-expiry recovery (simulated crash), timeout, bounded retries with backoff schedule, non-retryable failure, cancel queued and running, idempotent agent write after crash.
- Orchestrator: dependency gating, duplicate start returns the active run, quota 429, approval gate conditions, immutability (no endpoint mutates a revision), stale propagation (requirements → architecture; spec → requirements), request-changes.
- Agents: valid output stored; invalid → repair → success; invalid ×3 → `FAILED OUTPUT_INVALID`; requirement-ID stability in revise mode; questions/answers flow.
- Diagram: every validation rule; change-set ops application; invalid change set never applied; apply with stale base → 409 + `SUPERSEDED`; compare; restore.
- Isolation: user B gets 404 on every new route; SSE stream only carries own project's events.
- Groq adapter: request shape and error mapping via `httpx.MockTransport` (no network).

Frontend:
- Vitest: studio reducer (add/move/connect/delete/duplicate, undo/redo limits, apply/preview change set), validation marker rendering, inspector editing, requirements table editing, pipeline status rendering for every stage status, activity drawer from SSE/polling.
- Playwright (backend with `LLM_PROVIDER=mock`, clearly labelled in the UI): create project → run requirements → answer question → re-run → approve → generate architecture → edit in studio → save → reload → AI change request → preview → apply → compare → approve → edit spec → see architecture stale. Axe on requirements and studio pages.

## 11. Configuration

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `groq` | `mock` refused in production |
| `GROQ_API_KEY` | — | required when provider is `groq` |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | must support `json_schema` |
| `PLATFORM_DAILY_RUN_LIMIT` | `50` | per user |
| `AGENT_TIMEOUT_S` | `180` | per attempt |

Run locally: API (`uvicorn`) + worker (`python -m app.worker`) + frontend.

## 12. Risks

- **Groq structured output is best-effort** → Pydantic + domain validation + bounded repair; failures are visible, never silently "fixed".
- **Free-tier rate limits** → retryable 429 with backoff; quota per user.
- **Large diagrams** → limits in 6.1; ELK runs in the browser on demand only.
- **Two processes to run** (API + worker) → documented; the pipeline shows "Waiting for a worker" when a run stays queued > 30 s.
- **Model-quality variance** → output is always a draft requiring human approval.
