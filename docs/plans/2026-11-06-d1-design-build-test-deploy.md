# D1 Design · Build · Test · Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live Three.js/Motion agent universe (orchestrator sun, agent planets, subtask moons), the database schema to the Architecture stage, plus four new stages after the scaffold: `design` (HTML mockups + Playwright MCP screenshots), `implementation` (working code running in a Docker sandbox with a live preview), `testing` (Playwright MCP drives the sandbox against the MUST criteria), and `deploy` (push to the user's git repo).

**Architecture:**
- Each agent is a package under `app/modules/agents/<name>/` mirroring `agents/code/`: `schema`, `validate`, `prompts`, `agent`, `chat`, `mock_fixture`, `__init__.register()`.
- Two shared infrastructure modules:
  - `app/modules/mcp/`: the Playwright MCP client over stdio, plus a fake for tests
  - `app/modules/sandbox/`: the Docker runner
- The four stages are wired into stages, policy, registry, graph and conversations once (Task 6). Each later agent task adds its stage to `AVAILABLE`/`IMPLEMENTED`.
- The frontend adds one page per stage plus a Database tab inside the Architecture Studio.

**Tech Stack:** FastAPI, pymongo async, LangGraph, `mcp` (Python client), `cryptography` (Fernet), Docker CLI, git CLI, `npx @playwright/mcp`; Next.js 16, React Query, zod, SCSS modules, `@xyflow/react`, Vitest, Playwright.

**Spec:** `poc-backend/docs/specs/2026-11-06-d1-design-build-test-deploy-design.md`

## Global Constraints

- **Commits and styling**
  - No git commits; the user commits. No commit steps.
  - Frontend styling uses SCSS modules and the existing `src/components/ui/*` kit only. No Tailwind, no new UI library.
- **Stages and names**
  - Stage = artifact kind for every new stage: `design`, `implementation`, `testing`, `deploy`.
  - Agents: `ui_designer`, `implementer`, `tester`, `deployer`.
  - Labels: "UI/UX design", "Implementation", "Testing", "Deploy".
  - `DEPENDS_ON`: `design ← [requirements, architecture]`, `implementation ← [code, design]`, `testing ← [implementation]`, `deploy ← [testing]`.
  - Policy `ORDER` and `STAGES` gain `design, implementation, testing, deploy` after `code`.
- **Questions and LLM calls**
  - Only the Requirements agent asks the user questions. Other agents never create open questions.
  - The LLM provider has no tool calling. Every LLM step goes through `call_structured`.
  - No test calls Groq. Unit and service tests use `MockProvider` and `FakeMcp`.
  - Real Docker, browser and git tests sit behind the pytest markers `docker`, `browser`, `git`. Register them in `pyproject.toml`; they are excluded by default with `-m "not docker and not browser"`, so `git` tests (local bare repo) run by default.
- **Subprocesses**
  - Always `asyncio.create_subprocess_exec`: argv only, never `shell=True`.
  - Build commands with lists; never interpolate model text into a shell string.
- **Sandbox limits**
  - Flags: `--cap-drop ALL --security-opt no-new-privileges --memory 1g --cpus 1 --pids-limit 256 --user 1000:1000 -p 127.0.0.1::<port>`.
  - No Docker socket, no host mounts other than `<sandbox_root>/<project_id>/<dir>:/app`.
  - Health timeout 120 s; idle stop after 30 min; at most 4 services.
  - Runtimes: `python3.12 → python:3.12-slim`, `node22 → node:22-slim`.
- **Design limits:** at most 12 screens, HTML ≤ 30 000 characters per screen, screenshots at 1280×800, media ≤ 2 MB each.
- **Repair and test-step limits:** at most 2 repair rounds; failure logs ≤ 4 000 characters; at most 15 test steps per case; tool results fed back ≤ 3 000 characters.
- **Repository and token**
  - Repo URL regex: `^https://[\w.-]+/[\w./-]+?(\.git)?$`.
  - The token is Fernet-encrypted with `settings.secret_box_key` and never sent to the LLM, logs, events, artifacts or API responses.
  - Push target is `refs/heads/ampera/<slug>-r<rev>`, with no force push.
- **Data model:** at most 40 entities and 40 fields each. Field types: `uuid, string, text, int, float, bool, datetime, date, json, enum, decimal`.

## Review Focus

1. **A token that leaks through a git error message.**
   - Scenario: the remote rejects the push and git echoes the URL or credential.
   - Expected: the user sees "push rejected" without the token.
   - Test: `test_push_error_is_scrubbed` (Task 13).
2. **Generated HTML that hides an external resource.**
   - Scenario: `<img src="https://…">`, `style="background:url(http…)"` or `<link href>`.
   - Expected: rejected, so mockups stay offline and safe.
   - Test: `test_html_rejects_external_css_url` (Task 5).
3. **A sandbox port collision or a container left behind by a crashed run.**
   - Expected: starting again cleans up `ampera-<id>-*` first.
   - Test: `test_start_removes_stale_containers` (Task 8).
4. **The testing agent navigating away from the sandbox.**
   - Scenario: the model calls `browser_navigate` to `https://evil.example`.
   - Expected: the step is refused and counted, not executed.
   - Test: `test_navigate_outside_sandbox_refused` (Task 11).
5. **Old architecture revisions without `data_model`.**
   - Expected: they still load and render; the Database tab shows its empty state.
   - Tests: `test_old_revision_without_data_model_loads` (Task 1), plus the Vitest empty-state case (Task 2).

---

### Task 1: Data model schema, validation, ops, prompt, mock

**Files:**
- Modify: `app/modules/diagrams/schema.py` (add `Field_`, `Entity`, `Relation`, `DataModel`; `ArchitectureContent.data_model: DataModel = DataModel()`)
- Modify: `app/modules/diagrams/ops.py` (add the `UpsertEntity`, `RemoveEntity`, `UpsertRelation`, `RemoveRelation` ops to the `Op` union and the apply function)
- Modify: the diagram validator in `app/modules/diagrams/` (the module that registers the `architecture` KindSpec)
- Modify: `app/modules/agents/architecture/{prompts,mock_fixture}.py`
- Test: `tests/diagrams/test_data_model.py`

**Interfaces (Produces):**
- `DataField(name, type: FieldType, pk=False, nullable=True, unique=False, ref: str|None=None)`. The class is named `DataField` so it doesn't collide with pydantic's `Field`.
- `Entity(id: "^ent_[a-z0-9_]{1,40}$", name, database_node_id, description="", requirement_ids=[], fields: list[DataField])`
- `Relation(id: "^rel_[a-z0-9_]{1,40}$", from_entity, to_entity, kind: Literal["1-1","1-n","n-n"], label="")`
- `DataModel(entities=[], relations=[])`
- `data_model_issues(content: ArchitectureContent) -> list[ValidationIssue]`, with codes `DM_DB_NODE`, `DM_PK`, `DM_REF`, `DM_DUPLICATE`, `DM_MISSING`
- Op JSON: `{"op":"upsert_entity","entity":{...}}`, `{"op":"remove_entity","id":...}` (also removes its relations and the `ref`s pointing at it), `{"op":"upsert_relation","relation":{...}}`, `{"op":"remove_relation","id":...}`

- [ ] Tests:
  - `test_old_revision_without_data_model_loads`: a dict without `data_model` validates; `data_model.entities == []`
  - `test_entity_must_point_at_database_node`: `database_node_id` of a `backend` node → `DM_DB_NODE`
  - `test_exactly_one_pk`: zero pk → `DM_PK`; two → `DM_PK`
  - `test_ref_must_exist`: `ref="ent_user.nope"` → `DM_REF`; a relation to a missing entity → `DM_REF`
  - `test_database_node_requires_entities`: a `database` node with an empty model → `DM_MISSING`
  - `test_remove_entity_cascades`: removing `ent_user` drops `rel_user_orders` and clears `ent_order.user_id.ref`
  - `test_mock_fixture_has_valid_data_model`: the default `architecture_output` fixture passes `data_model_issues` with ≥ 3 entities
- [ ] Run `uv run pytest tests/diagrams/test_data_model.py -q` → fails.
- [ ] Implement. Add a rule to the prompt: "Also return `data_model`: the tables/collections each database node stores, with a pk, typed fields, refs and relations; cite requirement ids". Extend the change prompt to list the four new ops.
- [ ] Run the file plus `uv run pytest tests/diagrams tests/agents/test_architecture_agent.py -q` → pass.

### Task 2: Database tab in the Architecture Studio

**Files:**
- Modify: `src/lib/schemas/diagram.ts` (zod `dataModel`, defaulting to `{entities:[],relations:[]}`)
- Create: `src/components/studio/database/{database-view,entity-node,entity-panel,ddl}.tsx`, `database.module.scss`
- Modify: `src/components/studio/studio.tsx` (tabs **System** | **Database**; the URL search param `?view=database` keeps the tab when you reload)
- Test: `src/components/studio/__tests__/database-view.test.tsx`

**Interfaces:**
- Consumes: the architecture revision content from the existing studio state.
- Produces: `toDDL(model: DataModel): string`, Postgres flavour. Type map: `uuid→uuid, string→varchar(255), text→text, int→integer, float→double precision, bool→boolean, datetime→timestamptz, date→date, json→jsonb, enum→text, decimal→numeric(12,2)`. Each `ref` becomes `REFERENCES table(col)`.

- [ ] Vitest:
  - renders one card per entity, with `PK`/`FK` badges as text plus an icon
  - clicking a card opens the side panel with fields and requirement ids
  - `toDDL` on a 2-entity fixture equals an exact expected string
  - the **Copy as SQL DDL** button writes to the clipboard (mock) and shows a toast
  - empty model → an empty state with the copy "No tables yet — ask the architect in chat to add the data model"
  - the tab can be reached with the keyboard (arrow keys between tabs, `aria-selected`)
- [ ] Layout: dagre-free. Use simple layered columns, with entities grouped by `database_node_id` and ordered by relation count. Reuse `layered_positions`' idea client-side in ≤ 30 lines.
- [ ] Run `npm run typecheck && npm run lint && npx vitest run src/components/studio` → green.

### Task 3: Repository credentials (Requirements stage only)

**Files:**
- Modify: `pyproject.toml` (+ `cryptography>=44`); run `uv sync`
- Modify: `app/core/config.py` (`secret_box_key: SecretStr | None = None`; the `deploy` stage reports it is unavailable when this is None)
- Create: `app/core/secret_box.py`
- Modify: `app/modules/projects/{models,schemas,router,service}.py`
- Modify: `app/modules/agents/requirements/{prompts,validate,mock_fixture}.py`
- Frontend:
  - Create: `src/components/requirements/repository-card.tsx`
  - Modify: `requirements-view.tsx` and `src/lib/schemas/project.ts`
- Test: `tests/projects/test_repo_credentials.py`, `tests/agents/test_requirements_repo_question.py`, `src/components/requirements/__tests__/repository-card.test.tsx`

**Interfaces (Produces):**
- `secret_box.seal(plain: str) -> str`, `secret_box.open_(token: str) -> str`. Both raise `AppError("SECRET_BOX_UNCONFIGURED")` when the key is unset.
- `Project.repo_url: str | None`, `Project.repo_token_enc: str | None`
- `PUT /api/projects/{id}/repo` body `{url: str, token: str | None}`. A `None` token keeps the existing one. Returns `RepoOut{url, token_set: bool, token_last4: str | None}`.
- `GET /api/projects/{id}/repo` → `RepoOut`
- `service.repo_token(db, project) -> str | None`: used only by the deploy agent.
- `REPO_QUESTION = "Which git repository should the finished code be pushed to?"`

- [ ] Tests:
  - `test_put_get_repo_never_returns_token`: the response JSON has no key containing the full token; `token_last4` equals the last 4 characters
  - `test_token_encrypted_at_rest`: the raw Mongo doc's `repo_token_enc` != token and `open_` round-trips
  - `test_repo_url_validation`: `http://x/y`, `git@github.com:a/b.git` and `https://x/../y` → 422; `https://github.com/acme/app.git` → 200
  - `test_other_user_404`
  - `test_requirements_adds_repo_question_once`: a mock draft without it → saved content has exactly one open question with `REPO_QUESTION`, `blocking=False`; a re-draft doesn't duplicate it
  - `test_repo_answer_prefills_url`: answering that question with a valid URL sets `project.repo_url` when it was None
- [ ] Vitest:
  - the URL and token fields have visible labels
  - the token input is `type=password`
  - after saving, it shows "Token saved ••••abcd" and a **Replace token** action
  - URL validation runs on blur and the error clears on input
- [ ] Implement; run backend + frontend tests → green.

### Task 4: Media store and Playwright MCP client

**Files:**
- Modify: `pyproject.toml` (+ `mcp>=1.10`); `uv sync`
- Create: `app/modules/media/{__init__,service,router}.py` (collection `media`)
- Create: `app/modules/mcp/{__init__,playwright,fake}.py`
- Modify: `app/main.py` (include the media router)
- Test: `tests/media/test_media.py`, `tests/mcp/test_playwright_client.py` (one `@pytest.mark.browser` case)

**Interfaces (Produces):**
- `media.service.put(db, project, data: bytes, content_type: str) -> str` (id; raises `AppError("MEDIA_TOO_LARGE")` above 2 MB)
- `media.service.get(db, project, media_id) -> tuple[bytes, str]`
- `GET /api/projects/{id}/media/{media_id}`: owner only; `Cache-Control: private, max-age=86400`
- `class McpBrowser(Protocol)`:
  - `async call(tool: str, args: dict) -> str` (text result, truncated by the caller)
  - `async screenshot() -> bytes`
- `playwright.session() -> AsyncContextManager[McpBrowser]`:
  - starts `npx -y @playwright/mcp@latest --headless --isolated --viewport-size 1280,800` over stdio with `mcp.ClientSession`
  - `screenshot` calls `browser_take_screenshot` and decodes the image content block
- `fake.FakeMcp(script: dict[str, str | Callable])`: records `calls` and returns a 1×1 PNG for screenshots
- `mcp.get_browser` is a module-level factory that tests monkeypatch to return `FakeMcp`.

- [ ] Tests:
  - media put/get round trip
  - 2 MB + 1 byte → error
  - other user → 404
  - `test_fake_records_calls`
  - `@browser test_real_screenshot_of_data_url`: navigating to `data:text/html;base64,…` of `<h1>Hi</h1>` gives PNG bytes starting with `\x89PNG`
- [ ] Implement; run `uv run pytest tests/media tests/mcp -q` → pass.

### Task 5: Design agent (backend)

**Files:**
- Create: `app/modules/agents/design/{__init__,schema,validate,prompts,agent,chat,mock_fixture}.py`
- Modify: `app/modules/agents/__init__.py` (call `design.register()`)
- Test: `tests/agents/test_design_validate.py`, `tests/agents/test_design_agent.py`

**Interfaces (Produces):**
- `Theme(primary, surface, text, font_family, radius)`
- `Screen(id "^S-\d{3}$", name, purpose, requirement_ids, html)`
- `Flow(from_screen, action, to_screen)`
- `Shot(screen_id, media_id)`
- `DesignAgentOutput(summary, theme, screens, flows)`
- `DesignContent(DesignAgentOutput + screenshots: list[Shot])`
- `DesignChanges(summary, changes: list[{op: "write"|"delete", screen: Screen|None, id: str|None}])`
- `validate.html_issues(html) -> list[ValidationIssue]` (code `HTML_UNSAFE`)
- `validate.design_issues(out, requirements: dict) -> list[ValidationIssue]` (codes `HTML_UNSAFE`, `TOO_LARGE`, `REQ_UNKNOWN`, `MUST_UNCOVERED`, `FLOW_UNKNOWN`)
- `agent.NAME = "ui_designer"`; modes `generate` and `chat`
- `agent.render_shots(ctx, screens) -> list[Shot]`: uses `mcp.get_browser()`, one navigate + one screenshot per screen, `media.put`
- `register()`: KindSpec `design`; mock defaults `design_output`, `design_changes`, `design_chat_output`; `INPUT_CHECKS["design"] = lambda c: False`

- [ ] Tests:
  - `test_html_rejects_script_iframe_object_embed`
  - `test_html_rejects_on_handlers` (`<button onclick=…>`)
  - `test_html_rejects_external_css_url`: `<img src="https://x">`, `<link href="http://x">`, `style="background:url(https://x)"` → `HTML_UNSAFE`; `href="#S-002"` and `data:` images are allowed
  - `test_must_requirement_uncovered` → `MUST_UNCOVERED` naming `FR-001`
  - `test_generate_renders_one_shot_per_screen` (FakeMcp: 3 screens → 3 navigates, 3 media docs)
  - `test_chat_rewrites_one_screen_and_rerenders_only_it`
- [ ] Implement. Validate the HTML with `html.parser.HTMLParser` (stdlib) plus a regex for `url(` in `style`; no new dependency.
- [ ] Run → pass.

### Task 6: Wire the four stages (graph, policy, staleness, conversations, API)

**Files (modify):**
- `orchestration/stages.py`: `STAGES`, `AGENTS`, `DEPENDS_ON`, `LABEL`; `AVAILABLE` += `design` only
- `orchestration/service._resolve_inputs`:
  - design: approved requirements + architecture
  - implementation: approved code + design + architecture + requirements + plan
  - testing: approved implementation + requirements + design
  - deploy: approved implementation + testing
  - chat adds the latest revision of its own stage
- `agents/orchestrator/{policy,prompts,mock_fixture}.py`: `ORDER`, `DEPENDS_ON`; `IMPLEMENTED`/`REQUIRED_APPROVAL` += `design`
- `artifacts/registry.DEPENDENTS`:
  - `requirements` += `design, testing`
  - `architecture` += `design, implementation`
  - `plan` += `implementation`
  - `code` = `[implementation]`
  - `design` = `[implementation, testing]`
  - `implementation` = `[testing, deploy]`
  - `testing` = `[deploy]`
- `delivery/{graph,nodes,routing,facts,threads}.py`: nodes for the four stages; `AGENT_FOR` extended. **`deploy` is never auto-routed**: `route()` returns `DONE` once testing is approved. The deploy run starts only from `POST /api/projects/{id}/stages/deploy/run`.
- `conversations/{models,router,hooks}.py`: chat for `design`, `implementation` and `testing` (deploy has no chat)
- `artifacts/router.py`: `Kind` += the four kinds

**Tests:**
- `tests/delivery/test_graph_d1.py`:
  - `test_flow_through_design_after_code`
  - `test_route_stops_before_deploy`: with testing approved, the route is `DONE`, not deploy
  - `test_code_change_marks_implementation_and_testing_stale`
  - `test_rates_change_keeps_design_fresh`
- Update the C3 tests that assert `DONE` right after code, so they now go through design. Implementation shows `UNAVAILABLE` until Task 9.

- [ ] Write the tests and see them fail; wire; run `uv run pytest -q && uv run ruff check .` → green.

### Task 7: Design page (frontend)

**Files:**
- Create: `src/lib/schemas/design.ts`
- Create: `src/components/design/{design-view,screen-grid,screen-detail}.tsx`, `design.module.scss`
- Create: `app/(app)/projects/[id]/design/page.tsx`
- Modify: `src/components/projects/routes.tsx` (`DesignRoute`), the stage card ICON/routes, `src/lib/nav.ts`, the agent-graph labels and intake `AGENTS`
- Test: `src/components/design/__tests__/design-view.test.tsx`

**Pattern:** a gallery of screenshots, a detail slide-over with the live HTML in `<iframe sandbox="" srcdoc>`, a device toggle (Desktop 1280 / Mobile 375), and a theme token strip.

- [ ] Vitest:
  - the grid shows one card per screen with its name and requirement chips
  - opening a card sets `srcdoc` and `sandbox=""`
  - Escape closes the slide-over and focus goes back to the card
  - the device toggle changes the iframe width
  - a skeleton while loading
  - empty state copy: "Approve the scaffold to start the UI/UX design"
- [ ] Run typecheck, lint, vitest → green.

### Task 8: Sandbox runner

**Files:**
- Modify: `app/core/config.py` (`sandbox_root: Path = Path("/tmp/ampera-sandbox")`, `sandbox_enabled: bool = True`)
- Create: `app/modules/sandbox/{__init__,spec,docker,service,router}.py`
- Modify: `app/main.py` (router + startup task `reap_idle()` every 60 s)
- Test: `tests/sandbox/test_spec.py`, `tests/sandbox/test_docker_args.py`, `tests/sandbox/test_service.py`, `tests/sandbox/test_real_docker.py` (`@docker`)

**Interfaces (Produces):**
- `spec.SandboxService(name "^[a-z][a-z0-9-]{0,20}$", dir, runtime: Literal["python3.12","node22"], install: list[str], start: list[str], port: int (1024–65535), health: str = "/health")`
- `spec.SandboxSpec(services: list[SandboxService])` (1–4 services, unique names, `dir` must be an existing manifest folder)
- `spec.IMAGES = {"python3.12": "python:3.12-slim", "node22": "node:22-slim"}`
- `docker.run_args(project_id: str, svc: SandboxService, workdir: Path) -> list[str]`: the exact flags from Global Constraints. The container command is `["sh","-c", shlex.join(install) + " && exec " + shlex.join(start)]`.
- `service.start(db, project, files: list[CodeFile], spec: SandboxSpec) -> SandboxState`
- `service.stop(project_id)`
- `service.status(project_id) -> SandboxState`
- `service.logs(project_id, svc, tail=200) -> str`
- `SandboxState(status: Literal["stopped","starting","healthy","failed"], services: list[{name, url, healthy, error}])`
- `GET /api/projects/{id}/sandbox` → `SandboxState`; `POST …/sandbox/restart`, `POST …/sandbox/stop`, `GET …/sandbox/logs/{svc}`. All owner only.
- `service.touch(project_id)` updates `last_seen`; any sandbox GET calls it.

- [ ] Tests:
  - `test_run_args_flags`: contains every flag; no `/var/run/docker.sock`; exactly one `-v`, ending `:/app`; `-p 127.0.0.1::8000`
  - `test_shell_metachar_in_install_is_quoted`: `install=["pip","install","x; rm -rf /"]` → the command string contains `'x; rm -rf /'`
  - `test_spec_rejects_unknown_runtime_bad_port_five_services`
  - `test_start_removes_stale_containers`: the fake docker exec records `rm -f ampera-<id>-backend` before `run`
  - `test_health_timeout_marks_failed_with_logs` (fake clock + fake exec)
  - `test_reap_idle_stops_after_30_min`
  - `@docker test_two_service_fixture_healthy`: a tiny Python http.server backend + a node static frontend come up healthy and `stop` removes them
- [ ] Implement `docker.py` as a thin `exec(*argv) -> (rc, out)` that tests replace with a fake.
- [ ] Run → pass (`-m docker` run manually once).

### Task 9: Implementation agent (backend)

**Files:**
- Create: `app/modules/agents/implementation/{__init__,schema,prompts,agent,chat,mock_fixture}.py`
- Modify: `stages.AVAILABLE`, policy `IMPLEMENTED`/`REQUIRED_APPROVAL` (+ `implementation`)
- Test: `tests/agents/test_implementation_agent.py`

**Interfaces:**
- Consumes:
  - `code.agent.request_changes(ctx, out, user, allowed)`
  - `code.validate.apply_changes`, `manifest_issues`
  - `sandbox.service.start/status/logs`
  - `diagrams.schema.DataModel`
- Produces:
  - `ImplementationContent(summary, files: list[CodeFile], sandbox: SandboxSpec, health: list[{service, healthy, log_excerpt}], components_done: list[str])`
  - `agent.NAME = "implementer"`
  - `agent.components(files) -> list[str]`: the top-level folders that contain a dependency manifest

**Run:**
1. Start from the approved scaffold's files.
2. For each component, make one `request_changes` call with `allowed` = the files under that folder. The prompt holds: the digest (requirements MUST list, data model entities, design screens' names + theme, the component's file list and stub contents ≤ 6 000 chars).
3. One `call_structured` for `sandbox.json` → `SandboxSpec`. It is also written as a file `sandbox.json`.
4. `sandbox.start`.
5. Repair loop: at most 2 rounds. For each failing service, `request_changes` with logs ≤ 4 000 chars and `allowed` = that service's files.
6. Save.

Health failures after repair become approval errors (`SANDBOX_UNHEALTHY`).

- [ ] Tests (mock provider + fake sandbox service):
  - `test_one_call_per_component_scope_enforced`: a change outside the folder is rejected with `SCOPE`
  - `test_repair_sends_only_failing_service_logs_and_files`
  - `test_unhealthy_after_two_rounds_blocks_approval`
  - `test_digest_contains_data_model_entities`
  - `test_direction_from_testing_failures_reaches_prompt`
- [ ] Run → pass.

### Task 10: Implementation page (frontend)

**Files:**
- Create: `src/lib/schemas/implementation.ts`, `src/lib/schemas/sandbox.ts`
- Create: `src/components/implementation/{implementation-view,preview-pane,service-status,logs-panel}.tsx`, `implementation.module.scss`
- Create: `app/(app)/projects/[id]/implementation/page.tsx`
- Reuse: the file tree and viewer from `src/components/code/scaffold-panels.tsx`
- Modify: routes, nav, stage card and labels as in Task 7

**Pattern:** a split pane.
- Left: the preview iframe, with an address bar (read-only URL plus an **Open in new tab** icon button with a tooltip), a device toggle, and **Restart sandbox** / **Stop sandbox**.
- Right: tabs **Services** (health with icon + text and live progress from run events), **Logs** (per service, monospace, last 200 lines, error lines highlighted), and **Files**.
- Poll `GET …/sandbox` every 3 s while the status is `starting`.

- [ ] Vitest:
  - the iframe `src` equals the frontend service url
  - stopped → empty state with **Start sandbox**
  - failed → the error card shows the first error line plus **View logs**
  - health is shown as icon + text, never colour only
  - at 375 px the panes stack (CSS class check)
- [ ] Run typecheck, lint, vitest → green.

### Task 11: Testing agent (backend)

**Files:**
- Create: `app/modules/agents/testing/{__init__,schema,validate,prompts,agent,chat,mock_fixture,tools}.py`
- Modify: `stages.AVAILABLE`, policy (+ `testing`)
- Test: `tests/agents/test_testing_validate.py`, `tests/agents/test_testing_agent.py`

**Interfaces (Produces):**
- `TestCase(id "^TC-\d{3}$", requirement_id, title, kind: Literal["ui","api"], steps: list[str])`
- `TestPlan(summary, cases)`
- `Step(tool: Literal["browser_navigate","browser_click","browser_type","browser_snapshot","browser_take_screenshot","http_request","report_result"], args: dict[str, str])`
- `CaseResult(TestCase + passed: bool, evidence: str, screenshot_media_id: str|None, steps_used: int)`
- `TestReport(summary, results, passed: int, failed: int, must_failed: list[str])`
- `validate.coverage_issues(plan, requirements) -> list` (code `MUST_UNCOVERED`)
- `tools.guard(step, allowed_origins: set[str]) -> str | None`: returns a refusal message for a URL outside the sandbox origins
- `agent.NAME = "tester"`; schema names `test_plan` and `test_step`

**Run:** make sure the sandbox is healthy (start it from the approved implementation if it is stopped) → plan → for each case, a step loop (≤ 15). After 15 steps without `report_result`, the case fails with evidence "No verdict within 15 steps". The final screenshot is stored in media.

Approval is blocked when `must_failed` is non-empty (`MUST_FAILED`). The API action `POST /api/projects/{id}/stages/testing/send-failures` starts an implementation run with `direction` = the failed cases' titles + evidence.

- [ ] Tests (MockProvider scripted per schema name, FakeMcp):
  - `test_plan_must_cover_all_must_criteria`
  - `test_step_loop_passes_case`: navigate → snapshot → `report_result{passed:"true"}`
  - `test_navigate_outside_sandbox_refused`: the refusal text goes back to the model, no MCP call is made, and the step counts
  - `test_case_fails_after_15_steps`
  - `test_tool_result_truncated_to_3000`
  - `test_send_failures_starts_implementation_with_direction`
- [ ] Run → pass.

### Task 12: Testing page (frontend)

**Files:**
- Create: `src/lib/schemas/testing.ts`
- Create: `src/components/testing/{testing-view,results-table,case-detail}.tsx`, `testing.module.scss`
- Create: `app/(app)/projects/[id]/testing/page.tsx`
- Modify: routes, nav and labels

**Pattern:** a list + detail view.
- Summary bar: passed/failed counts, with MUST failures first.
- Table columns: status icon + text, id, requirement, title, steps used. Filters: **All / Failed / MUST**.
- Detail: steps, evidence, screenshot.
- Primary action when failures exist: **Send failures to implementation**.

- [ ] Vitest:
  - failed MUST rows sort first
  - the Failed filter works, and its empty state reads "No failing tests"
  - the detail shows the screenshot img from the media URL
  - the send-failures button calls the endpoint and shows a toast
  - the approve button is disabled while MUST failures exist, with a reason tooltip
- [ ] Run typecheck, lint, vitest → green.

### Task 13: Deployment agent (backend)

**Files:**
- Create: `app/modules/agents/deploy/{__init__,schema,prompts,agent,git,mock_fixture}.py`
- Modify: `stages.AVAILABLE`, policy (+ `deploy`); `stages.dependency_blockers` adds the reason "Add the repository URL and token on the Requirements page" when `repo_url` or the token is missing
- Test: `tests/agents/test_deploy_agent.py` (uses a local bare repo; no network)

**Interfaces (Produces):**
- `DeployContent(repo_url, branch, commit_sha, files: list[str], pushed_at, commit_message)`
- `git.push(files: list[CodeFile], repo_url: str, token: str, branch: str, message: str) -> str` (sha):
  - temp dir, `git init -b main`, commit as `Ampera <noreply@amperatech.ai>`
  - `git push <url> HEAD:refs/heads/<branch>` with env `GIT_ASKPASS=<temp script>`, `AMPERA_GIT_TOKEN=<token>`, `GIT_TERMINAL_PROMPT=0`
  - the askpass script prints `x-access-token` for the Username prompt and the token for the Password prompt
- `git.scrub(text: str, token: str) -> str`
- `agent.NAME = "deployer"`; schema `deploy_message` (`{summary, message}`)
- `branch = f"ampera/{slug}-r{implementation_revision}"`

- [ ] Tests:
  - `test_push_to_bare_repo`: the branch exists in the bare repo and `git ls-tree` equals the manifest paths
  - `test_existing_branch_rejected_no_force`: a second push → `AppError("BRANCH_EXISTS")`; the remote sha is unchanged
  - `test_push_error_is_scrubbed`: a fake remote failure whose stderr contains the token → the error message and the run events don't contain it
  - `test_token_never_in_prompt_events_artifact`: grep `MockProvider.calls`, the run events and the saved content for the token → none
  - `test_blocked_without_repo_credentials`
  - `test_route_never_auto_runs_deploy` (also in Task 6; keep one)
- [ ] Run → pass.

### Task 14: Live agent universe (Three.js + Motion)

**Files:**
- Backend: create `app/modules/agents/subtask.py` with `async def subtask(ctx, id: str, label: str, status: Literal["running","passed","failed"], message: str)`, which calls `ctx.emit("step", message, {"subtask": {"id": id, "label": label, "status": status}})`. Call it from the design agent (per screen render), implementation (per component + per sandbox service health), testing (per case) and deploy (`commit`, `push`).
- Frontend modify: `src/components/agent-graph/{view-model,scene,agent-graph}.tsx`, `agent-graph.module.scss`, `src/lib/schemas/workflow.ts` (`STAGE_LABEL` + the four stages)
- Frontend create: `src/components/agent-graph/{moons,mission-hud,handoff-beam,mini-universe}.tsx`
- Modify: the Design/Implementation/Testing/Deploy page headers to render `<MiniUniverse stage=…>` while that stage's run is live
- Test: `tests/agents/test_subtask_events.py`; `src/components/agent-graph/__tests__/{view-model,mission-hud}.test.ts(x)`

**Interfaces (Produces):**
- `view-model`: `ORDER` grows to 9 stages; `AGENT_NAME` += `design: "UI/UX Designer"`, `implementation: "Implementation Agent"`, `testing: "Testing Agent"`, `deploy: "Deployment Agent"`
- `GraphNode.moons?: Moon[]` with `Moon {id, label, status: "running"|"passed"|"failed"}`, derived from the `subtask` data in events of the stage's latest run. The last status per id wins. Moons drop 10 s after the run's last event when the run isn't running. Cap 40, with `overflow: number`.
- `buildMissionHud(view, events) -> {agent: string, done: number, total: number, ticker: string[]}`: `ticker` holds the last 6 step messages, newest first.

- [ ] Backend test: `test_each_agent_emits_subtasks`. Mock runs of design (3 screens), implementation (2 components + 2 services), testing (2 cases) and deploy emit `subtask` data with the expected ids and a final passed/failed.
- [ ] Vitest:
  - `moons_from_subtask_events`: last status wins
  - `moons_cap_40_with_overflow`
  - `moons_expire_10s_after_run_end`
  - `nine_stage_orbits_monotonic` (radius and period increase)
  - HUD: the ticker shows the 6 newest; the progress ring has `aria-valuenow = done` and `aria-valuemax = total`
  - reduced motion → no `animate` props
  - WebGL unavailable → the HUD renders as a list with every moon
- [ ] Scene:
  - moons orbit their planet (radius 0.35 + 0.08·i, `useFrame`; static when reduced motion)
  - a running moon pulses its emissive intensity
  - the handoff beam is a drei `Line` with an animated dash offset along the orchestrator → agent edge while that edge is active
  - `AnimatePresence` for HUD ticker items, `layout` on the progress card
- [ ] Use the `parallax-threejs:checkpoint` skill once for a visual snapshot of a mock run with moons, and `performance_start_trace` once (frame budget < 4 ms). Note the result in the task report.
- [ ] Run typecheck, lint, vitest and the backend test → green.

### Task 15: Deploy page, end-to-end, UX audit, docs

**Files:**
- Create: `src/lib/schemas/deploy.ts`, `src/components/deploy/{deploy-view}.tsx`, `deploy.module.scss`, `app/(app)/projects/[id]/deploy/page.tsx`
- Modify: routes, nav and labels
- Create: `e2e/d1-flow.spec.ts`
- Modify: `poc-backend/README.md`, `poc-frontend/README.md`, CLAUDE.md priority line

**Pattern:** a summary card.
- Before pushing:
  - the repo URL and branch preview
  - the file count
  - a missing-credentials state that links to the Requirements Repository card
  - a primary **Push to repo** button behind a confirm dialog that names the repo and branch
- After pushing: the commit sha, branch and pushed time, with a link to the branch.

- [ ] Vitest:
  - the confirm dialog names the repo and branch
  - the missing-credentials state links to `/projects/{id}/requirements#repository`
  - after a push it shows the sha (first 7 characters) and the branch
- [ ] e2e (mock provider, FakeMcp and the fake sandbox enabled by `ENVIRONMENT=test` env flags, a local bare repo as the remote): approve through code → the design gallery shows screens → the implementation preview pane renders → the testing report shows all passing → set repo credentials → push → the deploy card shows the sha.
- [ ] Run the `vectorlab-ux-skills:ux-audit` skill over the new screens (Database tab, Repository card, Design, Implementation, Testing, Deploy). Fix every finding or record why it was skipped.
- [ ] Full gates:
  - `uv run pytest -q && uv run ruff check .`
  - `npm run typecheck && npm run lint && npx vitest run && npx playwright test`
  - all green
- [ ] Docs:
  - READMEs: the new stages, Docker/Node/git as runtime requirements, `SECRET_BOX_KEY` (generate with `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`), and the sandbox and egress shortcut notes
