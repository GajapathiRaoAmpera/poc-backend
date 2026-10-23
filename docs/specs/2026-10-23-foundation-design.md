# Sub-project A — Foundation: Design Spec

- **Date:** 2026-10-23
- **Status:** Draft — awaiting review
- **Product:** Ampera AI-Powered Project Delivery Automation Platform
- **Repos:** `poc-backend` (FastAPI), `poc-frontend` (Next.js)

## 1. Context and goal

The platform turns a client's project idea into approved requirements, architecture, plans, budgets and code, using AI agents behind human approval gates. Work is split into sub-projects, each with its own spec → plan → implementation cycle:

| # | Sub-project | Outcome |
|---|---|---|
| **A** | **Foundation (this spec)** | Branded app, accounts, admin user management, private projects, project-creation wizard |
| B | Requirements → Architecture Studio | Requirements agent, editable architecture diagrams, revisions, approval |
| C | Plan + Budget + staleness | Plan from approved architecture, estimates, stale-artifact propagation |
| D | Code generation, validation, repair, hardening | Later specs |

**Goal of A:** a person can sign up, sign in, create and edit a private project through a guided wizard, and a Super Admin can manage users — all on a branded, accessible, tested foundation that B and C build on.

### Success criteria

1. A new visitor registers and lands in the app as an active user.
2. The user creates a project via the wizard, leaves mid-way, returns, and finds the draft intact.
3. A second user cannot see, read, edit or delete the first user's project (verified by automated tests against the API).
4. The Super Admin can list, search, create, edit, disable and delete users; a normal user cannot reach any admin endpoint.
5. Every screen uses the Ampera design tokens and passes automated axe checks with no serious/critical violations.
6. Backend and frontend test suites run and pass locally without network access to Supabase or Groq.

## 2. Decisions (confirmed with the product owner)

| Decision | Choice |
|---|---|
| Database | PostgreSQL hosted on **Supabase**, used purely as Postgres (connection string supplied by owner). Supabase Auth is **not** used. |
| AI provider | **Groq**, plus a deterministic mock provider. Integration lands in sub-project B, not A. |
| Registration | **Open self-registration**; new accounts are immediately `ACTIVE`. |
| Roles | Exactly two: `USER` and `SUPER_ADMIN`. One role per user. |
| Tenancy | No workspaces, no project sharing. Each project is private to its owner; Super Admin can see all. |
| Stack | Next.js (App Router, TypeScript) + FastAPI (Python 3.12). Modular monolith. |
| Brand | Derived from amperatech.ai (see §7). Framer's default `#0099FF` is not a brand color. |

The seven-role RBAC, workspaces and invitation flows described in the original master prompt are explicitly **out of scope** by owner decision.

## 3. Out of scope for A

- Any AI agent, Groq integration, usage tracking (→ B).
- Document upload and object storage (→ B).
- Requirements, architecture, plan, budget screens beyond locked placeholders (→ B, C).
- Real email delivery. Password-reset links are written to the server log in development; choosing an email provider is an open item (§12).
- Dark mode.
- OAuth / social login, MFA.

## 4. Architecture

```
Browser ──► Next.js (poc-frontend)
              │  /api/*  rewrite (same origin → cookies work)
              ▼
           FastAPI (poc-backend)  ──►  Supabase Postgres
```

- The Next.js server proxies `/api/*` to FastAPI via `rewrites`, so the session cookie is first-party and no CORS configuration is needed in production.
- Next.js `middleware` redirects unauthenticated visitors away from app routes **for UX only**. All authorization is enforced by FastAPI.
- No background workers in A.

### 4.1 Backend layout (`poc-backend`)

```
app/
  main.py                 # FastAPI app factory, routers, error handlers
  core/
    config.py             # pydantic-settings; reads env only
    db.py                 # async SQLAlchemy engine/session
    security.py           # argon2 hashing, token generation/hashing
    errors.py             # AppError types → uniform JSON errors
    deps.py               # current_user, require_super_admin
  modules/
    auth/      router.py service.py schemas.py models.py
    users/     router.py service.py schemas.py           # admin CRUD
    projects/  router.py service.py schemas.py models.py
    audit/     service.py models.py
  cli.py                  # create-superadmin
alembic/                  # migrations
tests/
```

Each module exposes a router (HTTP), a service (business rules) and schemas (Pydantic). Routers never touch the ORM directly; services take the current user and enforce ownership.

**Tooling:** `uv` (deps, venv), `ruff` (lint + format), `pytest` + `httpx` (tests), `alembic` (migrations), SQLAlchemy 2 async with `asyncpg`, `argon2-cffi`, `pydantic-settings`.

**Supabase connection:** use the **direct or session-pooler** connection string (port 5432). The transaction pooler (port 6543) breaks prepared statements and is not used. Configured only via the `DATABASE_URL` environment variable; never committed.

### 4.2 Frontend layout (`poc-frontend`)

```
src/
  app/
    (auth)/login  register  forgot-password  reset-password
    (app)/dashboard  projects  projects/new  projects/[id]  account
    (app)/admin/users
    layout.tsx  globals.css
  components/ui/          # owned Radix-based primitives (Button, Input, Dialog, …)
  components/shell/       # Sidebar, Header, Breadcrumbs, ProfileMenu
  lib/api/                # typed fetch client + TanStack Query hooks
  lib/schemas/            # zod schemas mirroring backend contracts
  styles/tokens.css       # design tokens (§7)
middleware.ts
```

**Libraries:** Next.js, TypeScript, Tailwind CSS v4 (reads CSS-variable tokens), Radix UI primitives, TanStack Query, react-hook-form + zod, lucide-react. Tests: Vitest + Testing Library, Playwright, `@axe-core/playwright`.

## 5. Data model

All tables use UUID primary keys and `created_at` / `updated_at` (`timestamptz`, server default `now()`). Managed exclusively through Alembic migrations, each with a working downgrade.

### users
| Column | Type | Notes |
|---|---|---|
| email | `citext` | unique |
| name | text | 1–120 chars |
| password_hash | text | argon2id |
| role | enum `user_role` (`USER`, `SUPER_ADMIN`) | default `USER` |
| status | enum `user_status` (`ACTIVE`, `DISABLED`) | default `ACTIVE` |
| daily_run_limit | int, nullable | null = platform default; used from B onward |
| last_login_at | timestamptz, nullable | |

### sessions
| Column | Type | Notes |
|---|---|---|
| user_id | FK users, `ON DELETE CASCADE` | indexed |
| token_hash | text | unique; SHA-256 of the cookie token |
| expires_at | timestamptz | indexed |
| ip, user_agent | text, nullable | |

### password_reset_tokens
`user_id` (FK, cascade), `token_hash` (unique), `expires_at`, `used_at` (nullable).

### login_attempts
`email` (citext), `ip` (text), `succeeded` (bool), `created_at`. Index on `(email, ip, created_at)`. Rows older than 24 h may be pruned.

### projects
| Column | Type | Notes |
|---|---|---|
| owner_id | FK users, `ON DELETE CASCADE` | indexed |
| name | text | 1–160 chars |
| description | text, nullable | |
| status | enum `project_status` (`DRAFT`, `ACTIVE`, `ARCHIVED`) | default `DRAFT` |

Index on `(owner_id, updated_at DESC)` for the project list.

### project_specifications
| Column | Type | Notes |
|---|---|---|
| project_id | FK projects, `ON DELETE CASCADE` | |
| revision | int | starts at 1; unique with `project_id` |
| data | jsonb | validated by `ProjectSpecification` Pydantic model |
| created_by | FK users, `ON DELETE SET NULL` | |

Rows are **append-only**: saving creates revision *n+1*; no row is updated. The current spec is the highest revision. Concurrent saves are serialised by the unique constraint; the losing request retries once with the next number, then returns `409 CONFLICT`.

### audit_logs
`actor_id` (FK users, `SET NULL`), `action` (text, e.g. `user.updated`), `target_type`, `target_id`, `metadata` (jsonb, never contains secrets or password hashes), `created_at`. Index on `created_at DESC`.

### ProjectSpecification schema (`data`)

All fields optional, so incomplete submissions are allowed:

```
basics:        { name, description }
problem:       { business_problem, target_users[], goals[], expected_outcomes[] }
requirements:  { functional[], non_functional[], expected_scale }
constraints:   { preferred_technologies[], deployment_preference,
                 budget: { min, max, currency (ISO 4217) },
                 target_delivery_date, team_assumptions,
                 compliance[], existing_integrations[] }
```

String fields are trimmed and length-capped (short text 300, long text 5000, list ≤ 50 items). Unknown keys are rejected.

## 6. API

Base path `/api/v1`. JSON only. All mutating requests must carry an `Origin` header matching the configured app origin (CSRF defense alongside `SameSite=Lax`).

### Error format

```json
{ "error": { "code": "NOT_FOUND", "message": "Project not found", "details": null } }
```

Codes: `VALIDATION_ERROR` (422), `UNAUTHENTICATED` (401), `FORBIDDEN` (403), `NOT_FOUND` (404), `CONFLICT` (409), `RATE_LIMITED` (429), `INTERNAL` (500, generic message, details only in server logs).

### Auth
| Method | Path | Notes |
|---|---|---|
| POST | `/auth/register` | name, email, password → creates `USER`, signs in |
| POST | `/auth/login` | sets session cookie; rate-limited |
| POST | `/auth/logout` | deletes session, clears cookie |
| GET | `/auth/me` | current user |
| POST | `/auth/password-reset/request` | always `202` (no account enumeration) |
| POST | `/auth/password-reset/confirm` | token + new password; revokes all sessions |
| POST | `/auth/change-password` | current + new password; revokes other sessions |

### Admin users (Super Admin only)
| Method | Path | Notes |
|---|---|---|
| GET | `/admin/users?q=&role=&status=&page=&page_size=` | paginated (default 20, max 100) |
| POST | `/admin/users` | create with initial password |
| GET | `/admin/users/{id}` | |
| PATCH | `/admin/users/{id}` | name, role, status, daily_run_limit |
| DELETE | `/admin/users/{id}` | hard delete; cascades projects |

Disabling a user revokes all their sessions.

### Projects
| Method | Path | Notes |
|---|---|---|
| GET | `/projects?page=&page_size=` | owner's projects; Super Admin sees all |
| POST | `/projects` | name (+ optional description) |
| GET | `/projects/{id}` | |
| PATCH | `/projects/{id}` | name, description, status |
| DELETE | `/projects/{id}` | |
| GET | `/projects/{id}/specification` | latest revision (or empty) |
| PUT | `/projects/{id}/specification` | creates new revision; returns it |

Pagination response: `{ items, page, page_size, total }`.

## 7. Security rules

1. **Passwords:** argon2id; min 10 chars, max 128; rejected if equal to email.
2. **Sessions:** 32-byte random token in cookie `ampera_session` (`HttpOnly`, `Secure` outside local dev, `SameSite=Lax`, `Path=/`). DB stores SHA-256 hash only. 7-day expiry, extended on activity at most once per hour.
3. **Login throttling:** more than 5 failed attempts for the same email+IP in 15 minutes → `429` until the window passes. Error message never reveals whether the email exists.
4. **Password reset:** 32-byte token, SHA-256 stored, 30-minute expiry, single use. In development, the full link is logged at INFO; in production, logging the link is disabled and the endpoint requires an email provider (open item).
5. **Ownership:** every project query is scoped `owner_id = current_user.id` unless the user is `SUPER_ADMIN`. Accessing another user's project returns **404**, never 403.
6. **Admin:** all `/admin/*` routes require `SUPER_ADMIN` (403 otherwise). The system refuses to delete, disable or demote the last active Super Admin (`409`). A Super Admin cannot delete themselves.
7. **Disabled users:** cannot log in; existing sessions are revoked.
8. **Bootstrap:** `uv run python -m app.cli create-superadmin --email …` prompts for the password interactively. No default credentials exist anywhere.
9. **Secrets:** only via environment variables (`DATABASE_URL`, `APP_ORIGIN`, `ENVIRONMENT`). `.env` is git-ignored; `.env.example` contains placeholders only.
10. **Logging:** passwords, tokens, cookies and `DATABASE_URL` are never logged.
11. **Audit:** user create/update/disable/delete, role changes and Super Admin bootstrap are written to `audit_logs`.

## 8. Design system (Ampera brand)

Derived from amperatech.ai on 2026-10-23.

### Tokens (`styles/tokens.css`)

| Layer | Token | Value |
|---|---|---|
| Primitive | `--ampera-magenta-600` | `#9C369A` (logo "A", primary) |
| | `--ampera-magenta-700` | `#7E2B7C` (hover/pressed, derived) |
| | `--ampera-magenta-50` | `#F7EDF7` (tinted surfaces, derived) |
| | `--ampera-lavender-500` | `#8070B4` (sampled from logo wordmark — **assumption**, replace with official value) |
| | `--ampera-navy-900` | `#000F5C` |
| | `--ampera-ink-950` | `#050505` |
| | `--ampera-paper` | `#FCFCFC` |
| | `--ampera-cream` | `#FFFEFA` |
| | `--ampera-gray-500` | `#969696` (decorative only; fails AA as text) |
| Semantic | `--color-bg` | paper |
| | `--color-surface` | `#FFFFFF` |
| | `--color-text` | ink |
| | `--color-text-muted` | ink at 65% opacity (≥ 4.5:1 on paper) |
| | `--color-border` | ink at 10% |
| | `--color-border-strong` | ink at 25% |
| | `--color-primary` / `-hover` / `-fg` | magenta-600 / magenta-700 / white |
| | `--color-sidebar-bg` / `-fg` | navy-900 / cream |
| | `--color-focus-ring` | magenta-600, 2px solid + 2px offset |
| | `--color-success/warning/danger/info` | AA-checked greens/ambers/reds/navy-tints, defined in implementation |
| Shape | `--radius-pill` | 40px (buttons, matching site) |
| | `--radius-card` / `--radius-input` | 12px / 8px |
| Shadow | `--shadow-sm` / `--shadow-md` | subtle, low-opacity ink |
| Type | `--font-sans` | Switzer (self-hosted, Fontshare license), fallbacks `Inter, system-ui, sans-serif` |
| | `--font-mono` | Fragment Mono |
| | weights | 300 (display), 400 (body), 500 (labels/emphasis) |
| | scale | 12 / 14 / 16 / 17 (body) / 20 / 24 / 30 px |
| Spacing | 4px base | 4, 8, 12, 16, 24, 32, 48, 64 |

### Usage rules

- Logo: the official PNG (810×226, transparent) is displayed at a fixed height (28px expanded sidebar on a cream chip, since the lavender wordmark lacks contrast on navy), never stretched, recolored or cropped. The collapsed sidebar and favicon use the "A" mark (50×51).
- Buttons: pill-shaped; primary = magenta fill; secondary = ink outline; tertiary = text. Uppercase labels reserved for primary marketing-style CTAs on auth screens, matching the site; in-app actions use sentence case for scanability.
- No gradients, glassmorphism or neon — the site doesn't use them.
- Accessibility (a stated Ampera value): WCAG 2.2 AA contrast, full keyboard operation, visible focus on every interactive element, labelled form controls, `prefers-reduced-motion` respected.

## 9. Screens

### Auth (unauthenticated)
- **Login, Register, Forgot password, Reset password.** Two-column layout on desktop (brand panel with logo + one-line value statement on navy; form on paper), single column on mobile. Inline field validation, server error banner, loading state on submit, password visibility toggle.

### App shell (authenticated)
- **Sidebar** (collapsible, state remembered per browser): Dashboard, Projects, Account; **Admin → Users** only for Super Admin. Collapses to icon rail; becomes a drawer below 768px.
- **Header:** breadcrumbs, profile menu (name, role badge, Account, Sign out).
- **Expired session:** any `401` clears client state and redirects to Login with a "Your session expired" notice and a `next` return path.

### Dashboard
- "New project" primary action.
- **Your projects:** up to 6 most recently updated, each showing name, status, last updated, spec completeness (fraction of wizard sections with content).
- **Drafts to finish:** projects in `DRAFT` with incomplete specs.
- Empty state with explanation and "Create your first project".
- No charts or statistics in A (no meaningful data yet).

### Projects list
Table: name, status, completeness, updated. Search by name, filter by status, pagination. Row actions: open, rename, archive, delete (confirm dialog).

### Project creation wizard (`/projects/new`, `/projects/[id]/edit`)
Steps with a progress indicator: **1 Basics → 2 Problem & goals → 3 Requirements → 4 Constraints & delivery → 5 Review**.
- Completing step 1 creates the project. Each "Next" / "Save draft" saves a new specification revision.
- Users can jump between visited steps; nothing beyond the name is mandatory.
- Review step lists each section, flags empty fields as "Missing — will be treated as an assumption", and offers "Finish" (sets status `ACTIVE`).
- Unsaved-changes warning on navigation away.

### Project overview (`/projects/[id]`)
Header (name, status, owner, updated), specification summary with "Edit", and the **delivery pipeline**: Requirements, Architecture, Plan, Budget, Code — each shown as a locked stage card labelled "Available in a coming release".

### Admin → Users
Table: name, email, role, status, last login, created. Search, role/status filters, pagination. "Create user" dialog. Row → side panel to edit name, role, status, daily run limit. Disable/enable and delete actions with confirmation; delete requires typing the user's email. Errors such as "cannot demote the last Super Admin" shown inline.

### Account
Profile (name, email read-only), change password form.

### Required states (every data view)
Loading (skeletons), empty, error with retry, not-found, forbidden.

## 10. Testing

### Backend (pytest, local Docker Postgres via `docker compose -f compose.test.yml`)
- Register → me → logout; duplicate email → 409; weak password → 422.
- Login: wrong password, disabled user, throttle after 5 failures.
- Session expiry and revocation on password change / disable.
- Password reset: valid token works once; expired/used token rejected; request always 202.
- **Isolation:** user B gets 404 on GET/PATCH/DELETE/spec of user A's project; B's list excludes A's projects; Super Admin sees both.
- Admin: normal user → 403 on every `/admin/*` route; last-Super-Admin protections; self-delete refused; audit rows written.
- Specification: revisions increment, old revisions unchanged, unknown keys rejected, length caps enforced.
- Origin check rejects cross-origin mutations.
- Migrations: upgrade head → downgrade base → upgrade head on an empty DB.

### Frontend
- **Vitest + Testing Library:** wizard navigation and draft save, form validation messages, sidebar role-based items, error/empty states.
- **Playwright E2E** (against local backend + test DB): register → create project via wizard → leave → reload → resume → finish; Super Admin edits and disables a user, who is then signed out; user B cannot open user A's project URL.
- **Accessibility:** `@axe-core/playwright` on login, register, dashboard, wizard, admin users — zero serious/critical violations.

No test touches Supabase or Groq.

## 11. Local development

- `poc-backend`: `uv sync`, `cp .env.example .env` (set `DATABASE_URL` to Supabase or local), `uv run alembic upgrade head`, `uv run python -m app.cli create-superadmin --email you@example.com`, `uv run fastapi dev`.
- `poc-frontend`: `npm install`, `npm run dev` (proxies `/api` to `BACKEND_URL`, default `http://localhost:8000`).
- `compose.test.yml` in `poc-backend` provides a throwaway Postgres for tests.
- READMEs in both repos document these steps.

## 12. Open items

| Item | Needed by | Owner |
|---|---|---|
| Supabase connection string (direct/session pooler) | Running against the real DB | Product owner |
| Email provider for password reset in production | Production deployment | Product owner |
| Official lavender hex and logo usage guidelines | Final visual polish | Product owner |
| Groq API key | Sub-project B | Product owner |

## 13. Risks

- **Open sign-up + Groq costs (B onward):** mitigated by per-user `daily_run_limit` and the Super Admin's ability to disable accounts.
- **Supabase pooler mode misconfiguration:** documented; app checks connectivity at startup and fails with a clear message.
- **Two repos without a shared contract package:** frontend zod schemas mirror backend Pydantic models manually; the backend's OpenAPI document is the source of truth and a later step may generate the client from it.

## Addendum (2026-10-23): database switched from PostgreSQL to MongoDB

Decided by the product owner after implementation. The HTTP API, frontend and behaviour are unchanged.

| Postgres mechanism | MongoDB replacement |
|---|---|
| Tables + Alembic migrations | Collections; `ensure_indexes()` on startup and `app.cli init-db` (idempotent) |
| `citext` unique email | Email stored lower-cased + unique index |
| Unique `(project_id, revision)` | Same, as a unique compound index; save retries once, then 409 |
| `SELECT … FOR UPDATE` on admins | Transaction that writes a shared guard document (`locks/super_admins`) so concurrent demotions conflict instead of write-skewing |
| `ON DELETE CASCADE` | Explicit deletes of sessions, reset tokens, projects and specs inside one transaction |
| Reset token row lock | Atomic `find_one_and_update` matching `used_at: null` |
| Manual pruning (not implemented) | TTL indexes on sessions, reset tokens (`expires_at`) and login attempts (24 h) |
| `ILIKE` search | Case-insensitive regex on `re.escape`d input |

Requirements: MongoDB must be a replica set (Atlas always is). Local tests use `mongo:9.0` as a
single-node replica set because MongoDB 8.x does not start on Linux kernel ≥ 6.19 (SERVER-121912).
Configuration: `MONGODB_URI`, `MONGODB_DB` replace `DATABASE_URL`.
