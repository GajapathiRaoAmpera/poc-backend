# C2 Plan and Budget Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After architecture approval, the delivery graph produces an approved work plan with a computed schedule, then an approved budget priced from hourly role rates.

**Architecture:** `plan` and `budget` become two normal stages on the C1 machinery: an agent module each (generate, chat, plus a no-LLM `reschedule`/`reprice` mode), artifact kinds with revisions and approvals, `sources`-based staleness, and router/policy entries. Code does the scheduling and all the money; the LLM only writes tasks, non-labour items and contingency. A new `ratecard` module holds the platform default and per-project override.

**Tech Stack:** FastAPI, Pydantic v2, MongoDB (pymongo async), LangGraph; Next.js 16, React Query, zod, SCSS modules, Vitest, Playwright + axe.

**Spec:** `poc-backend/docs/specs/2026-10-27-c2-plan-budget-design.md`

## Global Constraints

- **No git commits.** The user commits their own work. Leave changes unstaged.
- **Styling is SCSS modules only.** Never add Tailwind or any CSS framework. Use `@use "@/styles/mixins" as *;` and the `--ui-*` tokens.
- **No new dependencies** in either repo, including chart libraries. The timeline and bars are plain elements styled with SCSS.
- **Money uses `Decimal`** on the backend and is rounded half-up to 2 places, only on totals. It is serialised as a string in JSON.
- Labour: `hours = effort_days × hours_per_day`, `cost = hours × hourly_rate[role]`.
- `months = max(1, ceil(working_days / 21))`. `total = subtotal + subtotal × contingency_pct / 100`.
- Rate card: 1–20 roles; role matches `^[a-z][a-z0-9_]{1,39}$`, unique; `hourly_rate` > 0 and ≤ 10000; `hours_per_day` is 1–12 in steps of 0.5; currency matches `^[A-Z]{3}$`.
- Seed (written only when missing): USD, 8 h/day, frontend_engineer 60, backend_engineer 65, devops_engineer 75, qa_engineer 50, designer 55, project_manager 60.
- Plan output limits: 1–12 milestones, 1–150 tasks (the prompt asks for 10–60), `effort_days` 0.5–20 in steps of 0.5, at most 10 risks.
- Budget output limits: at most 40 items, `contingency_pct` 0–30, `amount` ≥ 0.
- `team_size` values are 1–20, with at most 20 entries.
- Tests never call Groq. Agent tests use `MockProvider` (`tests/agents/helpers.py`, `tests/delivery/conftest.py`).
- Backend tests need the local Mongo: `docker compose -f compose.test.yml up -d --wait`.
- UI quality bar (user request: "best UI and UX"):
  - Before building each screen, load `frontend-design:frontend-design` and the vectorlab skills `page-patterns`, `forms`, `labels`, `errors`, `loaders`, `empty-states`, `keyboard` and `viewports`.
  - Before finishing the frontend, run `vectorlab-ux-skills:ux-audit`.
  - Reuse `components/ui/*` (Button, Field, Input, Select, Table, Badge, Dialog, Skeleton, states, toast).

## Review Focus

1. **A role in `team_size` or the plan that was later removed from the rate card.** Saving preferences rejects it (422 `UNKNOWN_ROLE`). Plan validation reports it with the list of allowed roles. The calculator never raises `KeyError`. Tests are in Tasks 2, 3 and 5.
2. **A budget range in EUR with a USD rate card.** The page shows a currency-mismatch warning and makes no false "over budget" claim. The test is in Task 5.
3. **Weekend start date** (`start_date` is a Saturday). The first task starts on Monday. The test is in Task 3.
4. **A rate change on a project whose plan is still unapproved.** Nothing reprices until a budget exists. The budget is not created early, and no error occurs. The test is in Task 7.
5. **Long plans on a phone (375px).** The task table and timeline scroll inside their own container, never the page, and the timeline has a table equivalent for screen readers. The test is in Task 9 (component) and Task 12 (e2e at 375px).

---

## File map

**Backend**
- `app/modules/ratecard/` (new):
  - `models.py`: `RateCard`, `RoleRate` (Pydantic, validation rules)
  - `service.py`: `get_default`, `save_default`, `effective`, `save_override`, `seed_default`, `mark_budgets_stale`
  - `router.py`: admin and project routes
- `app/modules/agents/plan/` (new):
  - `schema.py`: LLM output, stored content
  - `validate.py`
  - `schedule.py`: pure function
  - `prompts.py`
  - `agent.py`: generate, reschedule
  - `chat.py`
  - `mock_fixture.py`
  - `__init__.py`: `register()`
- `app/modules/agents/budget/` (new): the same layout, but `calc.py` replaces `schedule.py`.
- Modified files:
  - `orchestration/stages.py`, `orchestration/service.py` (`_resolve_inputs`)
  - `agents/orchestrator/policy.py`, `agents/orchestrator/prompts.py`
  - `delivery/{graph,nodes,routing,facts,threads}.py`
  - `conversations/{models,router,hooks}.py`
  - `artifacts/router.py` (`Kind`)
  - `projects/spec_schema.py` (`Constraints`), `projects/service.py` (`save_preferences`)
  - `agents/__init__.py` (`load_all`), `cli.py` and `main.py` (seed), `main.py` (routers)
- Tests:
  - `tests/ratecard/`
  - `tests/agents/test_plan_*.py`, `tests/agents/test_budget_*.py`
  - `tests/delivery/test_graph_c2.py`
  - `tests/api/test_ratecard_api.py`

**Frontend**
- `src/lib/schemas/{plan,budget,ratecard}.ts`
- `src/lib/api/{ratecard}.ts`, plus generic artifact hooks reused for `plan`/`budget`
- `src/lib/format.ts` (`formatMoney`, `formatHours`)
- `src/components/plan/` (new): `plan-view.tsx`, `timeline.tsx`, `task-table.tsx`, `plan.module.scss`
- `src/components/budget/` (new): `budget-view.tsx`, `summary-tiles.tsx`, `cost-breakdown.tsx`, `budget.module.scss`
- `src/components/ratecard/` (new): `rate-card-form.tsx` (shared by the admin page and Preferences), `ratecard.module.scss`
- New pages: `src/app/(app)/projects/[id]/{plan,budget}/page.tsx`, `src/app/(app)/admin/rate-card/page.tsx`
- Modified:
  - `components/projects/routes.tsx`
  - `components/preferences/preferences-form.tsx`
  - `lib/nav.ts`
  - `lib/schemas/{workflow,conversation}.ts`
  - `components/agent-graph/agent-graph.tsx` (`ICON`)
  - `components/workflow/stage-card.tsx` (descriptions)
  - `e2e/agentic.spec.ts`

---

### Task 1: Rate card backend

**Files:**
- Create: `app/modules/ratecard/{__init__,models,service,router}.py`
- Modify: `app/cli.py`, `app/main.py` (include router; call `seed_default` after `ensure_indexes`)
- Test: `tests/ratecard/test_ratecard.py`, `tests/api/test_ratecard_api.py`

**Interfaces:**
- Produces:
  - `RoleRate(role: str, hourly_rate: Decimal)`
  - `RateCard(currency: str, hours_per_day: Decimal, roles: list[RoleRate])` with `.rate(role) -> Decimal | None` and `.role_names() -> list[str]`
  - `async def effective(db, project) -> RateCard`
  - `async def get_default(db) -> RateCard`
  - `async def save_default(db, admin: User, card: RateCard) -> RateCard`
  - `async def save_override(db, project, user: User, card: RateCard | None) -> RateCard`
  - `async def seed_default(db) -> None`
  - `async def mark_budgets_stale(db, projects: list[Project]) -> list[Project]` returns the projects marked
  - `def snapshot(card: RateCard) -> dict` (JSON-safe, rates as strings)
  - Routes: `GET/PUT /api/v1/admin/rate-card` (SuperAdmin) and `GET/PUT /api/v1/projects/{id}/rate-card` (owner).
    The project GET returns `{effective, override, source: "project"|"platform", placeholder: bool}`, where
    `placeholder` = the default has never been saved (`updated_by is None`).

- [ ] **Step 1: Write failing tests**
  - `test_seed_written_once`: call `seed_default` twice. The default equals the Global Constraints seed, and `updated_by is None`.
  - `test_validation_rules`: rejects `hours_per_day` 0, 13 and 7.3; rejects `hourly_rate` 0 and -1; rejects duplicate roles, the role `"Bad Role"`, 21 roles, and currency `"usd"`.
  - `test_effective_prefers_override`: with no override, `effective` equals the default; after `save_override`, it equals the override; after `save_override(None)`, it equals the default again.
  - `test_override_currency_must_match_budget_pref`: with project preferences `budget.currency="EUR"`, `save_override` with USD raises a 422 `AppError` with code `CURRENCY_MISMATCH`.
  - `test_save_default_marks_only_differing_budgets_stale`: create two projects without an override that have budget artifacts. One snapshot equals the new card and one differs. Only the differing one becomes `stale=True` with `stale_reason="Rates changed"`. A project with an override is untouched.
  - `test_audit_written`: `rate_card.updated` and `project.rate_card_updated` exist.
  - API: a non-admin gets 403 on `PUT /admin/rate-card`; another user's project gets 404 on `GET /projects/{id}/rate-card`; an invalid body gets 422.
- [ ] **Step 2: Run** `uv run pytest tests/ratecard tests/api/test_ratecard_api.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement.**
  - The default lives in the `settings` collection as `{_id: "rate_card", …, updated_at, updated_by}`.
  - The override lives in `projects.rate_card`.
  - `save_override` calls `mark_budgets_stale(db, [project])` and then `delivery.notify(db, project, "rates_changed")`.
  - `save_default` calls `mark_budgets_stale` over projects with `rate_card: None` that have a budget artifact, and does not notify.
  - Add the comment `# shortcut: scans every project without an override, fine for the pilot; move to a background job past ~1k projects`.
  - `mark_budgets_stale` compares `snapshot(effective)` with the latest budget revision's `content.rate_card` and uses `artifacts.mark_stale(db, project, "budget", "Rates changed")`.
  - Add `"rates_changed"` to the triggers `delivery.notify` accepts, if they are enumerated.
- [ ] **Step 4: Run** the same command. Expected: PASS. Then run `uv run ruff check .`.

### Task 2: Preferences gain start date and team size

**Files:**
- Modify: `app/modules/projects/spec_schema.py` (`Constraints`), `app/modules/projects/service.py` (`save_preferences`)
- Test: `tests/projects/test_preferences_c2.py`

**Interfaces:**
- Consumes: `ratecard.service.effective` (Task 1).
- Produces:
  - `Constraints.start_date: date | None`
  - `Constraints.team_size: dict[str, Annotated[int, Field(ge=1, le=20)]] | None` (max 20 keys)

- [ ] **Step 1: Write failing tests**
  - `test_team_size_unknown_role_rejected`: `{"team_size": {"astronaut": 2}}` returns 422 with code `UNKNOWN_ROLE`, and the message lists the allowed roles.
  - `test_team_size_bounds`: 0 and 21 are rejected.
  - `test_changing_team_or_start_marks_plan_stale`: when a plan artifact exists, changing `team_size` gives `stale_reason == "Team or start date changed"`. Saving the same values again doesn't mark it stale.
  - `test_other_preference_change_does_not_touch_plan`.
- [ ] **Step 2: Run** `uv run pytest tests/projects/test_preferences_c2.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Check roles against `effective(...)`. Compare the old and new `start_date`/`team_size` before saving, then `mark_stale(db, project, "plan", "Team or start date changed")` only when they differ and the plan artifact exists.
- [ ] **Step 4: Run** the same command plus `uv run pytest tests/projects -q`. Expected: PASS.

### Task 3: Plan schema, validation and scheduler (pure)

**Files:**
- Create: `app/modules/agents/plan/{schema,validate,schedule}.py`
- Test: `tests/agents/test_plan_validate.py`, `tests/agents/test_plan_schedule.py`

**Interfaces:**
- Produces:
  - `schema.py`:
    - `Milestone(id: "M-\d{2}", title, goal)`
    - `Task(id: "T-\d{3}", title, milestone_id, role, effort_days: Decimal, depends_on: list[str], requirement_ids: list[str], component_ids: list[str])`
    - `Risk(id: "R-\d{2}", description, impact: Literal["low","medium","high"], mitigation)`
    - `PlanAgentOutput(summary, milestones, tasks, risks)` (`extra="forbid"`, limits from Global Constraints)
    - `PlanContent(PlanAgentOutput)` adds `schedule: Schedule`, `tasks` with `start: date, end: date`, `milestones` with `start, end`, `warnings: list[str]`
  - `validate.py`:
    - `def plan_issues(out: PlanAgentOutput, requirement_ids: set[str], must_ids: set[str], component_ids: set[str], roles: list[str]) -> list[ValidationIssue]`
    - Codes: `DUPLICATE_ID`, `UNKNOWN_REF`, `CYCLE`, `MUST_UNCOVERED`, `UNKNOWN_ROLE`
  - `schedule.py`:
    - `def compute_schedule(out: PlanAgentOutput, start: date, team_size: dict[str, int], target: date | None) -> PlanContent`

- [ ] **Step 1: Write failing tests**
  - Validation, one test per code:
    - A cycle `T-001→T-002→T-001` produces the message `"Dependency cycle: T-001 → T-002 → T-001"`.
    - An uncovered MUST `FR-003` gives `MUST_UNCOVERED` naming `FR-003`.
    - The unknown role message contains `"Allowed roles: backend_engineer, …"`.
  - Schedule:
    - `test_dependency_order`: B depends on A (2 days each, start Monday 2026-11-02). A runs 11-02 to 11-03, B runs 11-04 to 11-05.
    - `test_role_slots`: two independent backend tasks with `team_size={"backend_engineer": 1}` run one after the other; with `2` they run in parallel.
    - `test_weekend_start`: `start=2026-11-07` (Saturday) makes the first task start on 2026-11-09.
    - `test_fractional_effort_ceils`: 0.5 days takes one working day.
    - `test_target_warning`: an end date after the target gives `warnings == ["Ends 3 working days after your target date (2026-11-10)"]`. The exact copy includes the count of working days.
    - `test_milestone_dates_are_min_max_of_tasks`.
    - `test_stable_tie_order`: ties are broken by task ID.
- [ ] **Step 2: Run** `uv run pytest tests/agents/test_plan_validate.py tests/agents/test_plan_schedule.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Cycle detection uses DFS that returns the path.
  - The scheduler is list scheduling over a topological order (ties by ID), with per-role slot end-dates. A task's start is the first working day after max(dependency ends, earliest free slot of its role). Duration = `ceil(effort_days)` working days.
  - `schedule.working_days` counts working days from the plan start to the plan end, inclusive.
- [ ] **Step 4: Run** the same command. Expected: PASS.

### Task 4: Plan Agent (generate, chat, reschedule) and artifact kind

**Files:**
- Create: `app/modules/agents/plan/{prompts,agent,chat,mock_fixture,__init__}.py`
- Modify:
  - `app/modules/agents/__init__.py` (`load_all` calls `plan.register()`)
  - `app/modules/orchestration/service.py` (`_resolve_inputs`)
  - `app/modules/orchestration/stages.py` (`AVAILABLE`, `AGENTS`, `DEPENDS_ON` for plan)
- Test: `tests/agents/test_plan_agent.py`

**Interfaces:**
- Consumes: Task 3 functions, `ratecard.service.effective`, `call_structured` (`app/modules/agents/structured.py`), `artifacts.add_revision`, and `registry.KindSpec` (see `app/modules/diagrams/__init__.py` for the pattern).
- Produces:
  - `NAME = "project_planner"`
  - `async def run(ctx) -> AgentOutcome`, dispatching on `ctx.run.mode` in `"generate" | "chat" | "reschedule"`
  - Artifact kind `"plan"` (schema version `"1.0"`, model `PlanContent`)
  - Mock schema names `"plan_output"` and `"plan_chat_output"`
  - Inputs for `plan`: approved `requirements` and approved `architecture` revisions, plus the latest `plan` for `chat`/`reschedule`. Inputs for `budget` (used in Task 6): approved `plan` and approved `architecture`, plus the latest `budget` for `chat`/`reprice`.

- [ ] **Step 1: Write failing tests** (mock provider)
  - `test_generate_saves_scheduled_revision`: the revision has `sources` kinds `{requirements, architecture}`, every task has `start`/`end`, and every MUST requirement is covered.
  - `test_invalid_then_repaired`: the first mock output has a cycle and the second is valid. There are 2 provider calls, and the events contain `"self-correcting (1/2)"`.
  - `test_reschedule_makes_no_llm_call`: change `team_size`, then run with mode `reschedule`. Assert `provider.calls == []`, origin is `"system"`, the summary is `"Rescheduled with updated team or start date"`, the tasks are identical, and the dates change.
  - `test_chat_revise`: a chat reply with `action="revise"` produces a new revision and adds an agent message on channel `plan`. `action="none"` only adds the message.
  - `test_prompt_is_compact`: the user prompt contains no requirement `description` text, and its length is under 12000 characters for a 40-requirement and 25-component fixture.
- [ ] **Step 2: Run** `uv run pytest tests/agents/test_plan_agent.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - The prompt digest follows spec §9.
  - `chat.py` mirrors `agents/requirements/chat.py`: `PlanChatOutput(reply, action: Literal["none","revise"], instruction)`. `revise` calls `agent.generate` with the instruction, as `architecture/chat.py` does for `generate`.
  - `mock_fixture.plan_output` builds a valid plan from the IDs in the prompt (one task per MUST requirement, roles from the prompt), with the same signature as `architecture_output` in `agents/architecture/mock_fixture.py`.
  - `register()` sets `stages.INPUT_CHECKS["plan"] = lambda c: False`.
- [ ] **Step 4: Run** the same command and `uv run pytest tests/agents -q`. Expected: PASS.

### Task 5: Budget schema, validation and calculator (pure)

**Files:**
- Create: `app/modules/agents/budget/{schema,validate,calc}.py`
- Test: `tests/agents/test_budget_calc.py`, `tests/agents/test_budget_validate.py`

**Interfaces:**
- Produces:
  - `schema.py`:
    - `CostItem(component_id: str | None, category: Literal["infra","licence","third_party","other"], description, recurrence: Literal["one_off","monthly"], amount: Decimal)`
    - `BudgetAgentOutput(summary, items, contingency_pct: Decimal, contingency_reason)`
    - `BudgetContent`: `rate_card` (snapshot), `labour{by_role: [{role, days, hours, rate, cost}], by_milestone: [{milestone_id, title, days, hours, cost}], total}`, `items`, `months`, `non_labour`, `contingency_pct`, `contingency_reason`, `contingency`, `subtotal`, `total`, `currency`, `warnings`, `summary`
  - `validate.py`:
    - `def budget_issues(out: BudgetAgentOutput, component_ids: set[str], risk_ids: set[str]) -> list[ValidationIssue]`
    - Codes: `UNKNOWN_REF`, `CONTINGENCY_REASON` (a pct > 0 that cites no existing `R-xx`)
  - `calc.py`:
    - `def compute_budget(plan: PlanContent, card: RateCard, out: BudgetAgentOutput, budget_pref: Budget | None) -> BudgetContent`

- [ ] **Step 1: Write failing tests**
  - `test_labour_exact`: tasks backend 2.5 days and qa 1 day, 8 h/day, rates 65 and 50. Hours are 20 and 8, and costs are `Decimal("1300.00")` and `Decimal("400.00")`. Labour total is `1700.00`.
  - `test_hours_per_day_changes_cost`: at 7.5 h/day, backend 2.5 days gives 18.75 h and `1218.75`.
  - `test_months_and_total`: `working_days=43` gives `months=3`. One-off 1000, monthly 200, contingency 10%: `non_labour=1600`, `subtotal=3300`, `contingency=330.00`, `total=3630.00`.
  - `test_rounding_half_up_on_totals_only`.
  - `test_range_warning_same_currency`: max 3000 USD and total 3630 gives a warning that contains `"above your budget range"`.
  - `test_range_currency_mismatch` (Review Focus 2): with an EUR range, the warnings equal `["Budget range is in EUR; the rate card is in USD"]` and there is no "above" warning.
  - `test_role_missing_from_card` (Review Focus 1): raises nothing. The task is costed at 0, and the warning reads `"No rate for role astronaut; its 2 days are not costed"`.
  - Validation: an unknown component gives `UNKNOWN_REF`; `contingency_pct=10` with a reason that cites no risk gives `CONTINGENCY_REASON`.
- [ ] **Step 2: Run** `uv run pytest tests/agents/test_budget_calc.py tests/agents/test_budget_validate.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Use the formulas from Global Constraints with `Decimal` and `quantize(Decimal("0.01"), ROUND_HALF_UP)` on totals only.
- [ ] **Step 4: Run** the same command. Expected: PASS.

### Task 6: Budget Agent (generate, chat, reprice) and artifact kind

**Files:**
- Create: `app/modules/agents/budget/{prompts,agent,chat,mock_fixture,__init__}.py`
- Modify: `app/modules/agents/__init__.py`, `orchestration/service.py` (`_resolve_inputs` budget branch), `orchestration/stages.py` (budget entries)
- Test: `tests/agents/test_budget_agent.py`

**Interfaces:**
- Consumes: Task 5, `ratecard.service.effective`/`snapshot`, and plan revisions (Task 4).
- Produces:
  - `NAME = "budget_estimator"`
  - `run(ctx)` with modes `"generate" | "chat" | "reprice"`
  - Artifact kind `"budget"`
  - Mock schema names `"budget_output"` and `"budget_chat_output"`

- [ ] **Step 1: Write failing tests**
  - `test_generate`: `sources` kinds are `{plan, architecture}`, the snapshot equals the effective card, and `total` equals `compute_budget(...)`.
  - `test_reprice_no_llm`: change the override rates, then run `reprice`. Assert `provider.calls == []`, items and contingency equal the previous revision, labour reflects the new rates, and the summary is `"Repriced with updated rates"`.
  - `test_reprice_on_hours_per_day_only`: `provider.calls == []` and the cost changes.
  - `test_chat_revise_and_none`.
  - `test_prompt_compact`: the prompt has per-component effort totals and no task titles, and is under 8000 characters for a 60-task plan.
- [ ] **Step 2: Run** `uv run pytest tests/agents/test_budget_agent.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**, following the Plan Agent pattern from Task 4.
- [ ] **Step 4: Run** `uv run pytest tests/agents -q`. Expected: PASS.

### Task 7: Graph, policy, chat channels and routing

**Files:**
- Modify:
  - `agents/orchestrator/policy.py` (`IMPLEMENTED`, `REQUIRED_APPROVAL` += plan and budget; `DEPENDS_ON["budget"] = ["plan", "architecture"]`)
  - `agents/orchestrator/prompts.py`: wherever it says only requirements and architecture are available
  - `delivery/graph.py` (`STAGE_NODES`)
  - `delivery/nodes.py` (`AGENT_FOR`, `LABEL`)
  - `delivery/routing.py` (`Node`; mode choice in `_run`; empty rate card gate)
  - `delivery/facts.py` (`ARTIFACT_STAGES`; new `StageFacts.reschedule_only`, `reprice_only`; `Facts.rate_card_roles: int`)
  - `delivery/threads.py:92` (channel tuple)
  - `conversations/models.py` (`Channel`), `conversations/router.py` (`AGENT_LABEL`), `conversations/hooks.py:9`
  - `artifacts/router.py:30` (`Kind`)
- Test: `tests/delivery/test_graph_c2.py`, `tests/agents/test_orchestrator_policy.py` (extend)

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces:
  - `StageFacts.reschedule_only` is True when the plan is stale and its latest revision's requirements and architecture source revisions equal the current approved ones.
  - `StageFacts.reprice_only` is True when the budget is stale and its plan and architecture source revisions equal the current approved ones.
  - Routing: in `_run`, `plan` uses `"reschedule"` when `reschedule_only`; `budget` uses `"reprice"` when `reprice_only`; otherwise `"generate"`.
  - Before running `budget`, when `facts.rate_card_roles == 0`, route returns `_gate("needs_input", stage="budget", reason="Set rates before the budget can be calculated")`.

- [ ] **Step 1: Write failing tests**
  - `test_full_flow`: with intake, answers and approvals, after architecture is approved the thread pauses at `approval` for `plan`. Approve it, and it pauses at `approval` for `budget`. Approve that, and the thread status is `DONE`.
  - `test_rates_change_reprices`: after the budget is approved, `save_override` with new rates makes the graph run the budget in mode `reprice` (the run's `mode == "reprice"`), with zero provider calls, and pause at approval.
  - `test_architecture_change_cascades`: approve a new architecture revision. The plan is stale and regenerates, not reschedules. After plan approval the budget regenerates, because its plan source changed.
  - `test_empty_rate_card_gate`: set the default rate card roles to `[]` directly in the DB, since the API forbids it. The graph pauses at `needs_input` with that exact reason.
  - `test_rate_change_before_budget_exists` (Review Focus 4): `save_override` while the plan awaits approval. No budget artifact is created, there is no error, and the thread stays at plan approval.
  - Policy: including `budget` while `plan` is excluded gives `ORDER`; `approval_required=False` on plan gives `APPROVAL_REQUIRED`; `default_plan` includes all four stages.
  - Chat: `POST /projects/{id}/conversations/plan/messages` and `…/budget/messages` return 202 and produce an agent reply.
- [ ] **Step 2: Run** `uv run pytest tests/delivery/test_graph_c2.py tests/agents/test_orchestrator_policy.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the edits listed under Files. Grep for `("requirements", "architecture")` and `"requirements", "architecture"]` so no hard-coded tuple is missed.
- [ ] **Step 4: Run the full backend suite**: `uv run pytest -q && uv run ruff check .`. Expected: all pass, including the existing C1 tests unchanged.

### Task 8: Frontend data layer

**Files:**
- Create: `src/lib/schemas/{plan,budget,ratecard}.ts`, `src/lib/api/ratecard.ts`
- Modify:
  - `src/lib/schemas/workflow.ts` (`ArtifactKind` += `"plan" | "budget"`; `STAGE_LABEL` exists)
  - `src/lib/schemas/conversation.ts` (`Channel` += `"plan" | "budget"`)
  - `src/lib/format.ts`
  - `src/lib/nav.ts` (admin item "Rate card", `/admin/rate-card`, icon `BadgeDollarSign`)
- Test: `src/lib/__tests__/format.test.ts`, `src/lib/schemas/__tests__/ratecard.test.ts`

**Interfaces:**
- Produces:
  - zod schemas: `PlanContent`, `BudgetContent`, `RateCard`, `ProjectRateCard` (mirroring Tasks 1, 3 and 5; money fields are strings)
  - `formatMoney(amount: string, currency: string): string` uses `Intl.NumberFormat(undefined, {style: "currency", currency})`
  - `formatHours(h: string): string` returns `"18.75 h"`
  - Hooks: `useRateCard()`, `useSaveRateCard()`, `useProjectRateCard(pid)`, `useSaveProjectRateCard(pid)`
  - Plan and budget artifacts use the existing `useArtifact`/`useRevision`/`useRevisions` with the new kinds.

- [ ] **Step 1: Write failing tests.**
  - `formatMoney("1218.75","USD")` contains `"1,218.75"`.
  - The rate card schema rejects `hours_per_day` 13, duplicate roles and currency `"usd"`, and accepts the seed.
- [ ] **Step 2: Run** `npx vitest run src/lib`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npx vitest run src/lib && npm run typecheck`. Expected: PASS.

### Task 9: Plan page

**Files:**
- Create: `src/components/plan/{plan-view,timeline,task-table}.tsx`, `plan.module.scss`, and `src/app/(app)/projects/[id]/plan/page.tsx`
- Modify: `components/projects/routes.tsx` (`PlanRoute`), `components/agent-graph/agent-graph.tsx` (`ICON.plan = CalendarRange`), `components/workflow/stage-card.tsx` (description: "Milestones, tasks and a schedule built from the approved architecture.")
- Test: `src/components/plan/__tests__/plan-view.test.tsx`

**Interfaces:**
- Consumes: Task 8 hooks, plus `AgentChatPanel`, `ApprovalBar`, `PageHeader`, `useBreadcrumbs`, `StageStatusBadge` and `useProjectEvents`. Lay it out exactly like `components/requirements/requirements-view.tsx`.

**UX decisions:**
- **Page pattern:** split pane, with the artifact on the left and the chat on the right. Below 1024px, the chat becomes a toggle button with a badge for unread agent replies.
- **Header:** title, status badge, revision picker (as on the requirements page), and a key facts row: start to end dates, working days, task count, and total person-days.
- **Warnings:** a callout under the header, using the existing `FormAlert` tone `warning`.
- **Milestones:** collapsible sections, all open by default. Each shows its dates and day total.
- **Task table:** columns ID, title, role, days, start to end, and links.
  - Requirement chips link to `/projects/{id}/requirements#FR-001`, and component chips link to `/projects/{id}/architecture?node=<id>`.
  - Rows are focusable, and Enter opens a detail popover with dependencies.
- **Timeline:** one row per task in milestone order. Each bar is absolutely positioned by working-day index and colored by role from a fixed 6-step token palette; a legend lists the roles.
  - Hovering or focusing a bar shows the task title, dates and days.
  - The timeline is `aria-hidden`, because the table has the same data. It scrolls horizontally inside its own container, with a sticky task-name column.
- **Empty or running state:** a skeleton while the agent runs, with the live step message from events.
- **Not started:** an empty state that names the gate ("Waiting for architecture approval") and links to it.
- **Risks:** a list with an impact badge (low/medium/high).
- **Motion:** none on data. Expanding a section uses the motion tokens and honours reduced motion.

- [ ] **Step 1: Write failing tests**
  - Renders the milestone headings and the task rows from a fixture.
  - Shows the warning text.
  - A requirement chip has `href` ending in `/requirements#FR-001`.
  - The timeline bar for T-002 has an inline `--start` style greater than T-001's.
  - The not-started state shows "Waiting for architecture approval".
  - At least one bar per task is rendered inside an element with `aria-hidden="true"`.
- [ ] **Step 2: Run** `npx vitest run src/components/plan`. Expected: FAIL.
- [ ] **Step 3: Implement.** Load the UI skills listed in Global Constraints first.
- [ ] **Step 4: Run** `npx vitest run src/components/plan && npm run typecheck && npm run lint`. Expected: PASS.

### Task 10: Budget page

**Files:**
- Create: `src/components/budget/{budget-view,summary-tiles,cost-breakdown}.tsx`, `budget.module.scss`, and `src/app/(app)/projects/[id]/budget/page.tsx`
- Modify: `routes.tsx` (`BudgetRoute`), `agent-graph.tsx` (`ICON.budget = Wallet`), `stage-card.tsx` (description: "Labour from hourly rates, other costs and contingency.")
- Test: `src/components/budget/__tests__/budget-view.test.tsx`

**UX decisions:**
- Same split-pane shell as Task 9.
- **Summary tiles:** Labour, Other costs, Contingency (with %), and Total, which is emphasised.
  - Under the total sits a **budget range meter**: a horizontal track from range min to max, with a marker at the total. The marker is green inside the range, amber below it, and the danger color above it. The text says the same, so color is never the only signal.
  - With a currency mismatch, the meter is hidden and the warning is shown instead.
- **Cost breakdown:** a single stacked bar of cost by role (SCSS, `aria-hidden`), followed by tables by role (days, hours, rate/h, cost) and by milestone (days, hours, cost).
- **Other costs table:** category badge, component link, recurrence ("one-off" / "per month × N months"), and amount.
- **Contingency:** the % and the reason, with risk IDs shown as chips.
- **"Rates used" panel:** the snapshot (hourly rate per role, hours per day, currency) with a link: to Preferences "Rates & team" for owners, and to `/admin/rate-card` for the Super Admin.
  - If the effective card now differs from the snapshot, it shows the inline notice "Rates changed since this budget. It will be repriced automatically."
- Numbers are right-aligned with `font-variant-numeric: tabular-nums`, and all money goes through `formatMoney`.

- [ ] **Step 1: Write failing tests**
  - The tiles show the formatted total `"$3,630.00"`.
  - The meter text reads `"Above your range by $630.00"` for max 3000.
  - With a currency mismatch, there's no meter and the warning text is shown.
  - The rate panel lists `"backend_engineer"`, `"$65.00/h"` and `"8 h/day"`.
  - The "Rates changed" notice appears when the effective card differs from the snapshot.
- [ ] **Step 2: Run** `npx vitest run src/components/budget`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npx vitest run src/components/budget && npm run typecheck && npm run lint`. Expected: PASS.

### Task 11: Rate card form, admin page and Preferences "Rates & team"

**Files:**
- Create:
  - `src/components/ratecard/rate-card-form.tsx`
  - `src/components/ratecard/ratecard.module.scss`
  - `src/app/(app)/admin/rate-card/page.tsx` (SuperAdmin guard, same as `admin/users/page.tsx`)
- Modify: `src/components/preferences/preferences-form.tsx` (new section "Rates & team")
- Test: `src/components/ratecard/__tests__/rate-card-form.test.tsx`, plus the existing preferences tests extended

**Interfaces:**
- Produces: `RateCardForm({ value: RateCard, onSubmit(card: RateCard): Promise<void>, submitLabel: string })`, used by both pages.

**UX decisions:**
- **Form pattern:** a currency `Select` (common ISO codes plus free entry validated by the regex), "Hours per working day" as a number input with step 0.5, and an editable table of Role and Hourly rate.
  - Each row has a remove icon button with a tooltip and an `aria-label` that names the role.
  - "Add role" adds a row and focuses its role input.
  - Role input auto-formats to `snake_case` on blur.
- **Validation:** blur-first, cleared instantly on fix, with errors attached to the specific cell (`aria-describedby`) and a summary at the top on submit.
- **Submit button:** says what it does ("Save rates"). It's disabled only while saving, and a toast confirms the save.
- **Leaving the page:** with unsaved changes, a `beforeunload` guard and a confirm dialog.
- **Admin page:**
  - A placeholder banner when `placeholder` is true: "These are starting rates. Replace them with your company's real hourly rates."
  - Helper text: "Changing rates reprices budgets that use the platform rates."
- **Preferences "Rates & team":**
  - A start date input.
  - A team size stepper for each role in the effective card, with a default of 1.
  - A toggle "Use platform rates" (on means override `null`). Switching it off pre-fills the form with the platform card.
  - When a budget currency preference is set, the currency field is locked to it, with an explanation.

- [ ] **Step 1: Write failing tests**
  - Blurring an empty rate shows "Enter an hourly rate above 0".
  - A duplicate role shows "Each role can appear once".
  - "Add role" focuses the new role input.
  - Submit calls `onSubmit` with `hours_per_day` as a number and the rates as strings.
  - In Preferences, switching the toggle off pre-fills the rows with the platform rates.
  - The admin page shows the placeholder banner.
- [ ] **Step 2: Run** `npx vitest run src/components/ratecard src/components/preferences`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npx vitest run && npm run typecheck && npm run lint`. Expected: PASS.

### Task 12: End-to-end, accessibility, UX audit and docs

**Files:**
- Modify: `poc-frontend/e2e/agentic.spec.ts` (or a new `e2e/plan-budget.spec.ts` using the same fixtures), `poc-backend/README.md` and `poc-frontend/README.md` (Known limitations: drop Plan/Budget from "not built"; document the rate card and that the seed rates are placeholders)

- [ ] **Step 1: Extend e2e.** Run intake through to requirements approval, architecture approval, the plan page with the timeline visible, plan approval, the budget page with the total, and budget approval. Then:
  - The Super Admin edits a rate on `/admin/rate-card`.
  - The budget page shows a new revision "Repriced with updated rates".
  - Run axe on `/plan`, `/budget`, `/admin/rate-card` and `/preferences` with no serious or critical violations.
  - At a 375px viewport, `document.documentElement.scrollWidth <= 375` on `/plan` and `/budget` (Review Focus 5).
- [ ] **Step 2: Run** `npm run test:e2e`. Expected: PASS. It needs `docker compose -f ../poc-backend/compose.test.yml up -d --wait` first.
- [ ] **Step 3: Run `vectorlab-ux-skills:ux-audit`** on the four screens, fix what it reports, and re-run `npx vitest run && npm run lint`.
- [ ] **Step 4: Run both full suites**, `uv run pytest -q && uv run ruff check .` and `npm run typecheck && npm run lint && npx vitest run`. Expected: all green. Report the counts.
