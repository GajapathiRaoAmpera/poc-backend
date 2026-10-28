# Increment C2 — Plan and Budget Agents

- **Date:** 2026-10-27
- **Status:** Draft, awaiting review
- **Builds on:** `2026-10-24-agentic-core-design.md` (C1: LangGraph delivery graph, orchestrator, stage chats)
- **Repos:** `poc-backend` (FastAPI + MongoDB), `poc-frontend` (Next.js 16, SCSS modules)

## 1. Why

C1 stops after an approved architecture. Plan, Budget and Code show as "Coming soon". C2 adds the
**Plan Agent** and the **Budget Agent**. After architecture is approved, the graph produces a work
breakdown with a computed schedule, and then a costed budget. Each one is approved separately.
C3 (scaffold script) follows in its own spec and reads the approved plan.

### Owner decisions (2026-10-27)

| Topic | Decision |
|---|---|
| Budget basis | **Effort × rate card.** Code computes the money, never the LLM. |
| Rate card | **Platform default** (Super Admin) **plus an optional per-project override**. |
| Rate unit | **Hourly rate per role**, set by the Super Admin. A card-level `hours_per_day` (default 8) converts plan days to hours. No per-employee roster. |
| Plan content | **WBS plus computed schedule.** The LLM writes milestones and tasks; code validates them and computes the dates. |
| Budget LLM role | **Non-labour costs plus contingency** only. Code computes labour and totals. |
| Shape | **Two normal stages** (`plan`, `budget`), each with its own artifact, approval and chat. |
| Order | `architecture → plan → budget`, run one after the other. This replaces C1's "parallel" note: the budget needs the plan's effort. |

### Success criteria (automated tests unless noted)

1. After architecture approval, with no clicks, the Plan Agent runs. Its revision covers every MUST
   requirement, has no dependency cycles, and has computed task and milestone dates. The graph
   then pauses for plan approval.
2. Approving the plan runs the Budget Agent. Labour equals `Σ effort_days × hours_per_day × hourly_rate[role]` exactly,
   and the total equals labour + one-off + monthly × months + contingency. The graph pauses for
   budget approval and ends when the budget is approved.
3. Changing the effective rate card marks only the budget stale. The re-run reprices it with
   **zero** LLM calls.
4. Changing the architecture marks the plan stale. When the new plan is approved, the budget goes
   stale too.
5. A chat message on the plan or budget page produces a revised revision through that stage's agent.
6. With an empty rate card, the budget stage pauses with a clear message and never invents rates.
7. Ownership holds on all new routes (other users get 404). Only the Super Admin can edit the
   platform rate card (others get 403). No test calls Groq.

## 2. Scope

**In:** Plan Agent, Budget Agent, scheduler, budget calculator, rate card (platform default plus
project override), Preferences fields `start_date` and `team_size`, Plan and Budget pages,
admin rate card page, graph/policy wiring.

**Out:** editing task titles and dependencies by hand (changes go through chat), PDF/CSV export, multiple
currencies or FX, tracking actual costs, C3.

## 3. Plan Agent (`app/modules/agents/plan/`, agent `project_planner`, stage `plan`)

Inputs: approved architecture revision, approved requirements revision, effective rate card
(roles only), preferences. Modes: `generate`, `chat` (revise with an instruction), `reschedule`
(no LLM, §3.3).

### 3.1 LLM output `plan_output` (`plan/1.0`)

```
summary: str (≤600)
milestones: [{id: "M-01", title (≤120), goal (≤400)}]           # 1–12
tasks: [{id: "T-001", title (≤160), milestone_id, role,
         effort_days: 0.5–20 (step 0.5), depends_on: [task id],
         requirement_ids: [req id], component_ids: [node id]}]   # 1–150
risks: [{id: "R-01", description (≤300), impact: low|medium|high, mitigation (≤300)}]  # ≤10
```

### 3.2 Validation (`plan/validate.py`; errors go to the existing repair loop)

- Task, milestone and risk IDs are unique. `milestone_id` and `depends_on` refer to existing IDs.
- `depends_on` has no cycles. The error names one cycle path.
- Every `requirement_id` exists in the approved requirements, and every `component_id` exists in the
  approved architecture.
- Every MUST requirement is covered by at least one task.
- Every `role` is in the effective rate card. The error message lists the allowed roles.

### 3.3 Schedule (`plan/schedule.py`, pure function, no LLM)

- `start_date` = preferences `start_date`, otherwise the date the run starts.
- Working days are Monday to Friday. Holidays are not modelled.
- List scheduling: process tasks in topological order (ties by ID). A task starts on the first
  working day after all its dependencies end, when its role has a free slot. Each role has
  `team_size[role]` slots (default 1). Duration = `ceil(effort_days)` working days.
- Output added to the stored content: `schedule {start, end, working_days}`, each task's
  `start`/`end`, and each milestone's `start`/`end` (min/max of its tasks).
- **Warning** (not an error): `end` is after preferences `target_delivery_date`.
- **Timeline edits (added 2026-10-28):** a person can drag a bar later or resize it on the Plan page
  (or use the keyboard or the "Adjust" dialog). `POST /projects/{id}/plan/edits`
  `{base_revision, edits: [{task_id, not_before?, effort_days?}]}` stores a task's `not_before`
  ("start no earlier than") and effort, recomputes the schedule in code (no LLM) and saves a `user`
  revision. The scheduler never starts a task before its `not_before`; dependencies and team size
  still apply. `schedule.start` is always the plan's start date. Chat revisions keep the pins of tasks
  that still exist.
- `reschedule` mode: `team_size` or `start_date` changed but the tasks did not. Copy the
  latest revision's tasks, recompute the schedule, and save a new revision (origin `system`,
  summary "Rescheduled with updated team or start date"). No LLM call.

The revision's `sources` = approved requirements and architecture revisions. `summary` comes from the LLM.

## 4. Budget Agent (`app/modules/agents/budget/`, agent `budget_estimator`, stage `budget`)

Inputs: approved plan revision, approved architecture revision, effective rate card, preferences
budget range. Modes: `generate`, `chat`, `reprice` (no LLM).

### 4.1 Labour (code, `budget/calc.py`)

`hours_task = effort_days × hours_per_day` and `labour_task = hours_task × hourly_rate[role]`,
totalled (hours and money) per role and per milestone. Uses `Decimal`
throughout and rounds half-up to 2 places, only on displayed totals.

### 4.2 LLM output `budget_output` (`budget/1.0`)

```
summary: str (≤600)
items: [{component_id | null, category: infra|licence|third_party|other,
         description (≤200), recurrence: one_off|monthly, amount ≥ 0}]   # ≤40
contingency_pct: 0–30
contingency_reason: str (≤400), must cite at least one plan risk id when > 0
```

Validation: `component_id` (when set) exists in the architecture. Every risk ID cited in
`contingency_reason` exists. The amounts are in the rate card currency, which the prompt states;
the output has no currency field.

### 4.3 Totals (code)

```
months      = ceil(plan.schedule.working_days / 21)  (at least 1)
non_labour  = Σ one_off + Σ monthly × months
subtotal    = labour + non_labour
contingency = subtotal × contingency_pct / 100
total       = subtotal + contingency
```

Stored content: `rate_card` snapshot `{currency, hours_per_day, roles[]}`, `labour {by_role[], by_milestone[],
total}`, `items[]`, `months`, `non_labour`, `contingency_pct`, `contingency_reason`,
`contingency`, `total`, `warnings[]`. **Warning:** `total` is outside the preferences budget range.
The comparison applies only when the currencies match. Otherwise the warning is "Budget range is
in XXX; the rate card is in YYY".

`reprice` mode: the plan revision equals the last budget's plan source, but the effective rate
card (any rate, role, currency or `hours_per_day`) differs from its snapshot. Keep the items and contingency, recompute labour and totals, and
save a new revision (origin `system`, summary "Repriced with updated rates"). No LLM call.

The revision's `sources` = approved plan and architecture revisions.

## 5. Rate card

- **Platform default:** `settings` document `{_id: "rate_card", currency, hours_per_day,
  roles: [{role, hourly_rate}], updated_at, updated_by}`. `init-db` seeds USD with 8 hours per day
  and these hourly rates: frontend_engineer 60, backend_engineer 65, devops_engineer 75, qa_engineer 50,
  designer 55, project_manager 60 (placeholders the Super Admin should edit). The seed is only
  written when the document is missing.
- **Project override:** `projects.rate_card` (same shape, including its own `hours_per_day`; nullable).
- **Effective card** = the override if set, otherwise the default. `rate_card.effective(db, project)` is the
  only reader.
- Rules: 1–20 roles, role matches `^[a-z][a-z0-9_]{1,39}$` and is unique, `hourly_rate` is > 0 and
  ≤ 10000, `hours_per_day` is 1–12 in steps of 0.5, currency is ISO 4217 (3 uppercase letters). If preferences `budget.currency` is set,
  a project override must use it (422 `CURRENCY_MISMATCH`).
- Changes: saving the platform default or a project override marks the budget stale ("Rates
  changed") for every affected project whose budget snapshot differs. For the platform default
  that is every project without an override, done as one `update_many` on the artifacts. Then it
  calls `delivery.notify(trigger="rates_changed")`, but only for the project whose override was
  saved. Other projects pick the change up on their next graph run.
- Audit: `rate_card.updated` (platform) and `project.rate_card_updated`, written with the existing `record_audit`.

## 6. Graph, policy, staleness

- `orchestration/stages.py`: `AVAILABLE` += `plan`, `budget`. `AGENTS` += the two agent names.
  `DEPENDS_ON` += `plan: [requirements, architecture]` and `budget: [plan, architecture]`.
- `delivery/nodes.py`: `AGENT_FOR`, `LABEL` += Plan Agent and Budget Agent. `delivery/graph.py`
  adds the `plan` and `budget` nodes, which reuse `run_agent_node`. `routing.Node` += `plan`, `budget`.
- `agents/orchestrator/policy.py`: `IMPLEMENTED` += `plan`, `budget`. `approval_required` is forced
  true for both. The default plan includes both. The orchestrator may exclude them, but an excluded
  `plan` also excludes `budget` (dependency rule).
- `routing.route` is unchanged. Mode choice in `_run`: `plan` uses `reschedule` when the facts say
  only team or start changed, otherwise `generate`. `budget` uses `reprice` when the facts say only
  rates changed, otherwise `generate`. `facts.py` gains `reprice_only` / `reschedule_only` flags,
  computed by comparing the latest revision's sources and snapshot with the current state.
- New gate reason: `needs_input` with `reason = "Set rates before the budget can be calculated"`
  when the effective rate card has no roles.
- Staleness already flows through `sources` (`mark_dependents_stale` on approval). Preferences save
  marks `plan` stale ("Team or start date changed") when `team_size` or `start_date` changed.
- Each new agent registers an `INPUT_CHECKS` entry returning False, because neither has user questions.

## 7. Preferences

The `Constraints` model gains `start_date: date | None` and `team_size: dict[role, 1–20] | None`,
with at most 20 entries. Roles not in the effective rate card are rejected with 422
`UNKNOWN_ROLE`. Both fields flow into the brief like the other constraints.

## 8. API (under `/api/v1`; project routes use `get_project_for`)

| Method | Path | Notes |
|---|---|---|
| GET/PUT | `/admin/rate-card` | Super Admin only. PUT validates per §5. |
| GET | `/projects/{id}/rate-card` | `{effective, override, source: "project" \| "platform"}` |
| PUT | `/projects/{id}/rate-card` | body = card, or `null` to clear the override |
| — | `/projects/{id}/artifacts/plan…`, `…/budget…` | existing generic artifact routes (get, revisions, approve, request changes) |
| — | `/projects/{id}/conversations/{plan\|budget}/messages` | existing chat routes; `plan` and `budget` added to the allowed stages |

## 9. Prompts and token budget

The Groq free tier caps a request at about 8k input tokens (`GROQ_MAX_INPUT_TOKENS`). Prompts get
a **compact digest**, never full artifacts:
- Plan: requirements as `id | priority | title`, components as `id | type | name`, edges as
  `from→to`, roles, preferences.
- Budget: components as `id | type | name | technology`, per-component task effort totals, the
  milestone count, months, risks, currency.
- Chat: the digest plus the latest revision in compact form plus the instruction.

## 10. Frontend (`poc-frontend`, SCSS modules)

- **`/projects/[id]/plan`** (same layout as the requirements page: artifact on the left,
  `AgentChatPanel` on the right, `ApprovalBar` on top):
  - milestones, each with a task table (id, title, role, days, start–end, requirement and component chips)
  - a timeline with one bar per task over working days, built in SCSS with no chart library; the bars are
    `aria-hidden` and the table holds the same data
  - risks and warnings
- **`/projects/[id]/budget`** (same layout):
  - summary tiles (labour, non-labour, contingency, total, budget-range status)
  - labour by role and by milestone, each with days, hours and cost
  - non-labour items
  - contingency with its reason
  - "Rates used" snapshot (hourly rate per role, hours per day), with a link to the rates
- **Preferences:** a new "Rates & team" section with start date, team size per role, and a rate
  override (same fields as the admin page) with a "Use platform rates" toggle.
- **`/admin/rate-card`:** currency, "Hours per working day", and a table of role and "Hourly rate"
  (add, remove, edit). This is a form page.
- Stage cards, breadcrumbs, the 3D graph and the list view use backend stage status, so Plan and Budget
  appear when available. Add their icons to `ICON` and their routes to the stage links.
- Money is formatted with `Intl.NumberFormat(locale, {style: "currency", currency})`.

## 11. Security

- The rate card is business data. Normal users see only the effective card for their own projects.
- The platform default is written only by the Super Admin (`require_super_admin`).
- All validation happens on the backend (Pydantic). The frontend zod schemas mirror it for form feedback only.
- LLM output never sets money directly. Every total is recomputed from validated inputs.

## 12. Testing

- **Unit:**
  - validators (each rule in §3.2 and §4.2)
  - scheduler (dependency order, role slots, weekends, ceil of durations, target-date warning, stable tie order)
  - calculator (hours = days × `hours_per_day`, per role and milestone, months, contingency, range warning and
    currency mismatch, Decimal rounding)
  - rate card validation (rejects `hours_per_day` 0 and 13, and zero or negative `hourly_rate`)
- **Agents (mock provider):** generate, repair, and give up for both agents. `reprice` and `reschedule` make
  zero provider calls (assert `provider.calls == []`).
- **Graph:** architecture approved → plan → gate approval → approve → budget → gate approval → approve → END.
  Empty rate card → `needs_input`. Rates changed, or only `hours_per_day` changed → reprice with zero LLM calls. Architecture changed → plan stale, then budget stale.
- **API:** rate card auth (403 / 404), validation (422 roles, currency, unknown team role),
  `null` override resets.
- **Frontend:**
  - Vitest for the plan view (tables, timeline, warnings), the budget view (totals and formatting) and the
    rate card form (validation, add/remove role)
  - Playwright + axe for the new pages; extend `e2e/agentic.spec.ts` to run through to an approved budget

## 13. Risks

- **Plan size vs the 8k token cap:** 150 tasks may not fit in the output. Mitigation: the prompt
  asks for 10–60 tasks at a granularity of 0.5–20 days, plus `max_output_tokens` and `TOO_LARGE`
  surfacing as in C1.
- **Unrealistic estimates:** the validator checks structure, not truth. Mitigation: the plan is
  approved by a person, and the chat can revise it.
- **Default rates are placeholders:** the admin page shows a banner until the Super Admin saves rates
  once (`updated_by` is null).
