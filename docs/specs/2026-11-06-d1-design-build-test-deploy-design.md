# Increment D1 — UI/UX Design, Implementation (sandbox), Testing, Deployment Agents

- **Date:** 2026-11-06
- **Status:** Draft, awaiting owner review
- **Builds on:** `2026-10-28-c3-scaffold-design.md` (C3: Development Agent, scaffold script)
- **Repos:** `poc-backend` (FastAPI + MongoDB + LangGraph), `poc-frontend` (Next.js 16, SCSS modules)

## 1. Why

C3 ends with an approved scaffold: stub files that the user downloads and runs. Ampera is meant to
automate the SDLC end to end, so D1 adds four stages after the scaffold:

`requirements → architecture → plan → budget → code (scaffold) → design → implementation → testing → deploy`

| Stage | Agent | Output |
|---|---|---|
| `design` | `ui_designer` | Clickable HTML mockups for each screen, plus screenshots rendered through Playwright MCP |
| `implementation` | `implementer` | A working frontend and backend built from the scaffold and the design, running in a Docker sandbox with a live preview |
| `testing` | `tester` | A test report: the agent drives the running sandbox app through Playwright MCP and checks every MUST acceptance criterion |
| `deploy` | `deployer` | The frontend and backend code pushed to the user's git repository |

### Owner decisions (2026-11-06)

| Topic | Decision |
|---|---|
| Design tool | **HTML mockups + Playwright MCP.** Pencil.dev's MCP needs the Pencil app open on a desktop, and Penpot adds a heavy self-hosted service. HTML mockups are free, run on the server, and the implementation agent can reuse the markup. Penpot can be added later. |
| Running generated code | **Reverses C3's "the platform never runs generated code".** Generated code now runs, but only inside a locked-down Docker container (§5.3). Tests may still never call the real LLM. |
| Who asks questions | **Only the Requirements agent asks the user anything.** Later agents never ask; they work from approved artifacts. The repository URL is a Requirements question; the token is entered on the Requirements page (§3). |
| Deployment | **Push code to the user's git repo, and nothing more.** No hosting and no CI setup. The push goes to a new branch and never force-pushes. |
| MCP | Playwright MCP (`npx @playwright/mcp`, free, headless) is the browser tool for both the design and testing agents, called from the backend through the `mcp` Python client. |

### Success criteria (automated tests unless noted)

1. Once the scaffold is approved, design → implementation → testing run in order with no clicks. Each
   one pauses for approval like the existing stages. Deployment runs only after the user clicks **Push to repo**.
2. Design: each screen is stored as self-contained HTML (no external scripts, inline CSS only), and
   its PNG screenshot comes from Playwright MCP. The validator rejects `<script>`, `<iframe>`,
   `<object>`, `<embed>`, `on*=` attributes, and any `src`/`href` pointing at an external URL.
3. Implementation: the sandbox starts every service, and `/health` responds within the timeout. If a
   service fails, its build or run logs go back to the agent for at most 2 repair rounds, and only the
   failing service's files may change. The preview shows the running frontend in an iframe.
4. The sandbox container has no Docker socket and no host mounts other than its own work dir. It runs
   with `--cap-drop ALL`, no-new-privileges, and limits on memory, CPU and process count, and it is
   bound to 127.0.0.1 only. A test checks these flags on the `docker run` command line that is built.
5. Testing: every MUST requirement with acceptance criteria gets at least one test case. Each case
   stores its steps, pass/fail, and a screenshot. A failing MUST case blocks testing approval, and
   **Send failures to implementation** re-runs implementation with the failures as direction.
6. Deploy: a mock git remote (a local bare repo) receives a commit on branch `ampera/<slug>-r<rev>`
   containing exactly the implementation files. The token never appears in logs, run events,
   artifacts, API responses or LLM prompts (a test greps all of them).
7. The repo token is stored encrypted (Fernet). The API only returns `{url, token_set, token_last4}`.
8. Staleness: a new approved revision upstream marks every downstream D1 stage stale, using the
   existing cascade.
9. Ownership holds on every new route (other users get 404). No test calls Groq; design and testing
   use a fake MCP client in unit tests, plus one opt-in integration test with real Playwright MCP
   (`-m browser`).

## 2. Scope

**In:** the four agents; the repo credential form and storage; the sandbox runner; the Playwright
MCP client; pages for the four stages; graph, policy and staleness wiring.

**Out:**
- hosting or deploying to a cloud (Vercel, Render…); CI pipelines in the pushed repo
- Pencil.dev or Penpot integration
- editing files or mockups by hand (changes go through each stage's chat, as in C3)
- running more than one sandbox per project; keeping a sandbox alive for more than 30 idle minutes
- databases other than what the scaffold's services start themselves (no managed DBs)
- pushing to a branch other than `ampera/...`, force pushes, pull requests

## 3. Requirements agent changes (`app/modules/agents/requirements/`)

- The prompt always adds one open question when the project does not have one yet:
  `Q-xxx "Which git repository should the finished code be pushed to?"` with `blocking: false`.
  It only blocks the `deploy` stage, not requirements approval.
- New project fields (in `projects/models.py`): `repo_url: str | None` and
  `repo_token_enc: str | None`.
- `PUT /api/projects/{id}/repo` takes `{url, token}`. The URL must be `https://` on any host and
  match `^https://[\w.-]+/[\w./-]+?(\.git)?$`. The token is encrypted with Fernet using
  `settings.secret_box_key` (new required setting). `GET` returns
  `{url, token_set, token_last4}`. The answer to the repo question pre-fills the URL.
- Requirements page: a **Repository** card with URL + token inputs (token type `password`) under the
  open questions. It is the only place the token is ever entered.
- `deploy` is blocked with reason "Add the repository URL and token on the Requirements page" until
  both are set.

## 3b. Architecture agent: database schema (`diagrams/schema.py`, `agents/architecture/`)

The architecture stage also produces the project's **data model**, shown as an ER diagram.

- `ArchitectureContent` gets `data_model: DataModel = DataModel()`. The default is empty, so old
  revisions still validate and `schema_version` stays `"1.0"`.
- `DataModel`: `entities` (at most 40) × `{id: "ent_<snake>", name, database_node_id, description,
  requirement_ids[], fields[]}`; `fields` (at most 40) × `{name, type, pk: bool, nullable: bool,
  unique: bool, ref: "ent_x.field" | None}`; `relations` × `{from_entity, to_entity, kind:
  "1-1"|"1-n"|"n-n", label}`. `type` is one of a fixed list (`uuid, string, text, int, float,
  bool, datetime, date, json, enum, decimal`), so the implementation agent can map it to SQL or Mongo.
- Validator: every `database_node_id` points at a node whose type is `database`; each entity has
  exactly one `pk`; every `ref` and `relations` endpoint exists; names are unique. If the
  architecture has a `database` node, there must be at least one entity.
- The generate prompt asks for the data model. Chat ops gain `upsert_entity`, `remove_entity`,
  `upsert_relation` and `remove_relation`, which go through the same op pipeline as node edits.
- Architecture page: tabs **System** | **Database**. The Database tab renders entities as table
  cards (PK/FK badges, type column, nullable marker) on an `@xyflow/react` canvas, with crow's-foot
  edge labels, auto layout, and a side panel for the selected entity that lists its requirement ids.
  There is also a **Copy as SQL DDL** action (generated client-side from the model).
- Downstream: the implementation agent gets the data model in its digest and must create these
  models and tables; the testing agent may use entity names in API cases.
- Success criterion: generate → the Database tab shows the mock fixture's entities and relations;
  a chat "add an audit_log table" adds that entity through the ops; an invalid `ref` is rejected.

## 4. Design agent (`app/modules/agents/design/`, agent `ui_designer`, stage `design`)

**Inputs:** approved requirements (personas, MUST/SHOULD functional requirements) and architecture
(frontend components).

**Output schema** (`DesignContent`):
- `theme`: `{primary, surface, text, font_family, radius}`: one shared token set.
- `screens`: at most 12 × `{id: "S-001", name, purpose, requirement_ids[], html}`; each `html` is
  at most 30 KB.
- `flows`: `{from_screen, action, to_screen}[]`: navigation between screens, used for links.
- `screenshots`: filled by the platform after rendering (not by the LLM): `{screen_id, png_ref}`.

**Run:** `call_structured` generates the screens. The validator checks the HTML rules (§1 criterion 2),
the `requirement_ids` that exist, and that every MUST functional requirement is on some screen. The
platform renders each screen with Playwright MCP (`browser_navigate` to a `data:text/html;base64,…` URL, then `browser_take_screenshot` at 1280×800). PNGs are stored in a `media` collection
(`{_id, project_id, content_type, data: bytes}`, each ≤ 2 MB) and referenced by id from the revision;
`GET /api/projects/{id}/media/{media_id}` serves them (owner only).

**Page:** a grid of screenshots; clicking one opens the live HTML in a sandboxed iframe
(`sandbox=""`, no scripts). The chat edits screens using the C3 `CodeChanges`-style change mechanism
(write/delete per screen id).

## 5. Implementation agent (`app/modules/agents/implementation/`, agent `implementer`, stage `implementation`)

### 5.1 Generation
**Inputs:** the approved scaffold manifest (`code` revision), approved design, requirements, and plan
tasks.

The agent works in batches over the scaffold's files: it reuses `request_changes`/`apply_changes`
from the code agent to turn stubs into working code, one component per call so each call stays
under `MAX_OUTPUT_TOKENS`. The frontend reuses the design HTML and theme tokens.

It also writes `sandbox.json` (validated, never free shell):
```json
{"services": [{"name": "backend", "dir": "backend", "runtime": "python3.12",
               "install": ["pip", "install", "-r", "requirements.txt"],
               "start": ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
               "port": 8000, "health": "/health"},
              {"name": "frontend", "dir": "frontend", "runtime": "node22", ...}]}
```
`runtime` is from an allowlist mapped to pinned images (`python:3.12-slim`, `node:22-slim`).
Commands are argv lists, run without a shell. At most 4 services.

### 5.2 Sandbox runner (`app/modules/sandbox/`)
- One work dir per project under `settings.sandbox_root/<project_id>/`, rewritten from the manifest
  on every run.
- One Docker network per project (`ampera-<id>`); services reach each other by name.
- For each service: `docker run -d --name ampera-<id>-<svc> --network ampera-<id>
  --cap-drop ALL --security-opt no-new-privileges --memory 1g --cpus 1 --pids-limit 256
  --user 1000:1000 -v <workdir>/<dir>:/app -w /app -p 127.0.0.1::<port> <image> sh -c 'install && start'`.
  The command is built from argv with `shlex.join`, never from model text.
- The runner waits for `/health` (120 s for install + start), then collects `docker logs --tail 200`.
- Egress is allowed so that installs work. `shortcut: open egress for npm/pip installs, upgrade to an
  allowlisted proxy before multi-tenant hosting.`
- `stop(project)` removes the containers and network. A background task stops sandboxes that have
  been idle for 30 minutes. Starting a new run stops the old sandbox first.

### 5.3 Validation and repair
Validation = manifest rules from C3 + `sandbox.json` schema + the sandbox health check. On failure,
the failing service's logs (truncated to 4 KB) go to `request_changes` with `allowed` = that
service's files. At most 2 rounds (same loop shape as `validate_and_repair` in C3). If it still
fails, approval is blocked and the logs are shown.

### 5.4 Preview
`GET /api/projects/{id}/sandbox` → `{status, services: [{name, url, healthy}]}`, with URLs like
`http://127.0.0.1:<host_port>`. The page shows the frontend in an iframe with an address bar, a
**Restart** and **Stop** button, and a logs panel per service.
`shortcut: direct localhost ports work for local POC only, upgrade to an authenticated reverse proxy for hosted use.`

## 6. Testing agent (`app/modules/agents/testing/`, agent `tester`, stage `testing`)

**Inputs:** approved requirements (MUST + SHOULD with acceptance criteria), design flows, and the
running sandbox (started on demand if it is stopped).

**Run:**
1. `call_structured` → test plan: `cases[]` of `{id: "TC-001", requirement_id, title, kind: "ui"|"api", steps[]}`.
   The validator checks that every MUST criterion is covered.
2. For each case, a step loop (at most 15 steps per case). The provider has no native tool calling,
   so each step is one `call_structured` returning `{"tool": ..., "args": {...}}`. The platform
   runs it and feeds the truncated result (≤ 3000 chars) into the next step. Allowed tools: the Playwright MCP tools
   (`browser_navigate`, `browser_click`, `browser_type`, `browser_snapshot`,
   `browser_take_screenshot`) plus `http_request` for API cases, limited to the sandbox's own URLs.
   It ends by calling `report_result{passed, evidence}`.
3. The platform saves the final screenshot per case.

**Output** (`TestReport`): `cases[]` with `{..., passed, evidence, screenshot_ref, tool_calls}` plus
totals. Approval is blocked while any MUST case fails. **Send failures to implementation** starts an
implementation run with the failed cases as `direction`. The graph then re-runs testing.

## 7. Deployment agent (`app/modules/agents/deploy/`, agent `deployer`, stage `deploy`)

Mostly code, not LLM. The LLM writes only the commit message (`call_structured`, one sentence plus a
bullet list of requirement ids).

1. Copy the approved implementation manifest into a temp dir and run `git init` with a commit
   (author `Ampera <noreply@amperatech.ai>`).
2. `git push <repo_url> HEAD:refs/heads/ampera/<slug>-r<rev>`. The token is passed through
   `GIT_ASKPASS` (a temp script that reads it from an env var), never in the URL or argv.
   Output is scrubbed of the token before logging.
3. If the branch already exists remotely, the push is rejected (no force). The error says so.
4. The artifact stores `{repo_url, branch, commit_sha, files, pushed_at}`; the page links to the branch.

Deploy needs an explicit click on **Push to repo**, because it is outward-facing. It never runs
automatically from the graph.

## 8. Wiring

- `stages.py`: `STAGES` += `design, implementation, testing, deploy`. `DEPENDS_ON`:
  `design ← [requirements, architecture]`, `implementation ← [code, design]`,
  `testing ← [implementation]`, `deploy ← [testing]`. Labels: "UI/UX design", "Implementation",
  "Testing", "Deploy".
- The orchestrator's `default_plan` includes all four. The plan schema accepts them.
- Graph nodes are added the same way as the existing stage nodes in `orchestration/nodes.py`. `deploy`
  is entered only from the approve action, not by auto-advance.
- Frontend: four stage pages under the existing project layout, reusing the stage header, approval
  bar, chat panel and run-event stream.

### 8b. UI/UX bar for every new screen
- Reuse the house components and SCSS tokens. No new UI library.
- Each page picks one pattern:
  - Design: gallery + detail
  - Implementation: split pane (preview | logs/files)
  - Testing: list + detail
  - Deploy: summary card
  - Database tab: canvas + side panel
- Loading states:
  - Skeletons for loads over 1 s.
  - Live progress from run events: "Starting backend… installing deps… healthy".
- Empty states always have one action, for example "Approve the scaffold to start the design".
- Errors say what happened and what to do next. Sandbox and test failures show the relevant log
  lines, not a raw dump.
- Tables and actions:
  - Pass/fail uses an icon plus text, never colour alone.
  - Buttons start with a verb: "Push to repo", "Restart sandbox", "Send failures to implementation".
- Accessibility:
  - Full keyboard access and visible focus.
  - Respects `prefers-reduced-motion`.
  - Usable from 375 px to 1280 px wide; the preview iframe gets a device toggle (mobile/desktop).
- Before step 6 is done, run an audit against the installed `vectorlab-ux-skills`.

## 8c. Live agent universe (Three.js + Motion)

The existing 3D agent graph (`poc-frontend/src/components/agent-graph/`: the orchestrator is the
sun and the stage agents are planets, built with `three`, `@react-three/fiber`, `@react-three/drei`
and `motion`, all already installed) grows to show the whole SDLC while it is being built:

- **Planets:** the four new agents join on outer orbits ("UI/UX Designer", "Implementation Agent",
  "Testing Agent", "Deployment Agent"). The orbit maths stay as they are, extended over 9 stages.
- **Moons = live subtasks.** While an agent runs, each subtask it reports becomes a moon orbiting
  its planet:
  - design screens
  - implementation components and sandbox services
  - test cases
  - deploy steps

  Moon colour follows status (running pulses, passed, failed). Moons fade out 10 s after their run ends.
- **Data, not decoration:** agents emit `ctx.emit("step", message, {"subtask": {"id", "label",
  "status": "running"|"passed"|"failed"}})`. `view-model.ts` derives moons only from these events,
  in the same pure way it derives active edges today.
- **Mission HUD (Motion):**
  - an overlay beside the canvas: the active agent's name, a progress ring (done/total subtasks),
    and a ticker of the last 6 step messages (enter/exit with Motion `AnimatePresence`, layout
    animations, 150–250 ms)
  - the parent → child handoff (orchestrator → agent) animates a beam along the edge
- **On every new stage page**, while a run is live, a compact version (the HUD plus a mini canvas
  focused on that planet) sits in the page header.
- **Accessibility:** `prefers-reduced-motion` gives static positions and opacity-only HUD changes.
  The existing WebGL fallback (`webgl.ts`, `scene-boundary.tsx`) shows the HUD as a plain list.
  Every moon is also listed in an `aria-live="polite"` region.
- **Performance:** at most 40 moons are drawn (more collapse into "+N"); the frame budget stays
  under 4 ms at 1280×800 on a mid laptop (checked with the performance trace once).

## 9. New dependencies

- `cryptography` (Fernet for the repo token)
- `mcp` (Python MCP client, stdio transport to `npx -y @playwright/mcp --headless`)
- Runtime: Docker (already used locally), Node for `npx`, git.

## 10. Testing approach

- Unit: the validators for each schema; the `docker run` argv builder (flags in criterion 4);
  `GIT_ASKPASS` token handling and log scrubbing; coverage checks.
- Service tests with the mock LLM provider and a fake MCP client (as in C3's `mock_fixture.py`).
- Integration (opt-in markers): `-m docker` starts a two-service fixture app in the real sandbox;
  `-m browser` renders a mockup with real Playwright MCP. The deploy test pushes to a local bare repo.
- e2e (Playwright, existing `poc-frontend/e2e`): scaffold approved → design approved → preview
  iframe loads → test report → push to a local bare remote.

## 11. Build order

1. Architecture data model + Database tab.
2. Repo credentials (Requirements changes + encryption).
3. Playwright MCP client + design agent.
4. Sandbox runner + implementation agent + preview.
5. Testing agent.
6. Deployment agent.
7. Live agent universe (planets, moons, Mission HUD) + UX audit of all new screens.

Each step is shippable on its own; later stages show "Coming soon" until their step lands.
