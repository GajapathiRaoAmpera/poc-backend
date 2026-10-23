# Foundation (Sub-project A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Branded, accessible web app where people self-register, sign in, create private projects through a 5-step wizard with revisioned specifications, and a Super Admin manages users.

**Architecture:** FastAPI modular monolith (`poc-backend`) on Postgres (Supabase in real use, local Docker in tests), with Alembic migrations and cookie sessions. Next.js App Router frontend (`poc-frontend`) proxies `/api/*` to FastAPI so cookies are first-party; all authorization lives in the backend.

**Tech Stack:** Python 3.12, uv, FastAPI, SQLAlchemy 2 (async, asyncpg), Alembic, Pydantic v2 + pydantic-settings, argon2-cffi, pytest + httpx + pytest-asyncio, ruff · Next.js (App Router, TS), Tailwind CSS v4, Radix UI, TanStack Query, react-hook-form + zod, lucide-react, Vitest + Testing Library, Playwright + @axe-core/playwright.

**Spec:** `poc-backend/docs/specs/2026-10-23-foundation-design.md`

## Global Constraints

- Two roles only: `USER`, `SUPER_ADMIN`. Statuses: `ACTIVE`, `DISABLED`. Project statuses: `DRAFT`, `ACTIVE`, `ARCHIVED`.
- API base path `/api/v1`; errors always `{"error":{"code","message","details"}}` with codes `VALIDATION_ERROR` 422, `UNAUTHENTICATED` 401, `FORBIDDEN` 403, `NOT_FOUND` 404, `CONFLICT` 409, `RATE_LIMITED` 429, `INTERNAL` 500.
- Another user's project → **404**, never 403. `/admin/*` for non-admin → 403.
- Cookie `ampera_session`: HttpOnly, SameSite=Lax, Path=/, Secure unless `ENVIRONMENT` in {`development`,`test`}; 32-byte token; DB stores SHA-256 hex only; 7-day expiry, extended at most once per hour.
- Passwords: argon2id, 10–128 chars, must not equal email (case-insensitive).
- Login throttle: >5 failed attempts for same email+IP within 15 min → 429.
- Reset tokens: 32 bytes, SHA-256 stored, 30-min expiry, single use; request endpoint always 202; link logged at INFO only when `ENVIRONMENT=development`.
- Mutating requests (POST/PUT/PATCH/DELETE) require `Origin` == `APP_ORIGIN`, else 403.
- Pagination: `page` ≥1, `page_size` default 20, max 100; response `{items,page,page_size,total}`.
- Spec text caps: short 300, long 5000, lists ≤50 items; unknown keys rejected; strings trimmed.
- Name caps: user 1–120, project 1–160.
- Secrets only via env (`DATABASE_URL`, `APP_ORIGIN`, `ENVIRONMENT`); never logged; `.env` git-ignored.
- Tests never touch Supabase or Groq; backend tests use `compose.test.yml` Postgres at `postgresql+asyncpg://postgres:postgres@localhost:54329/ampera_test`.
- Brand tokens exactly as spec §8; no gradients/glass/neon; WCAG 2.2 AA; visible focus ring (2px magenta, 2px offset).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Frontend work on branch `feat/foundation` in `poc-frontend`.

## Review Focus

1. **Email case/whitespace:** `" Ana@X.com "` registers; login as `ana@x.com` succeeds; registering `ANA@x.com` again → 409. (Task 4)
2. **Session of deleted or disabled user:** existing cookie → 401 on next request, frontend redirects to `/login?expired=1`. (Tasks 6, 12)
3. **Concurrent spec saves:** two simultaneous PUTs both succeed with revisions 2 and 3, or one gets 409 — never a 500 or duplicate revision. (Task 8)
4. **Double-submit in wizard step 1:** clicking "Next" twice creates exactly one project. (Task 14)
5. **Admin edits own account:** sole Super Admin demoting or disabling self → 409 with readable inline message. (Tasks 6, 15)

---

## Backend (`poc-backend`, branch `feat/foundation`)

### Task 1: Backend scaffold, config, error format, test harness

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `compose.test.yml`, `README.md`
- Create: `app/__init__.py`, `app/main.py`, `app/core/config.py`, `app/core/errors.py`, `app/core/db.py`
- Test: `tests/conftest.py`, `tests/test_health.py`

**Interfaces:**
- Produces: `Settings` (`database_url: str`, `app_origin: str`, `environment: Literal["development","test","production"]`), `get_settings() -> Settings` (lru_cached); `create_app() -> FastAPI`; `AppError(code: str, message: str, status: int, details: Any = None)` plus subclasses `NotFound`, `Forbidden`, `Unauthenticated`, `Conflict`, `RateLimited`; `engine`, `SessionLocal`, `get_db() -> AsyncIterator[AsyncSession]`; fixtures `client` (httpx `AsyncClient`, `base_url="http://test"`, default header `Origin: http://localhost:3000`) and `db` (AsyncSession).

- [ ] **Step 1:** `uv init --app`, add deps: `fastapi[standard] sqlalchemy[asyncio] asyncpg alembic pydantic-settings argon2-cffi`; dev: `pytest pytest-asyncio httpx ruff`. `pyproject` sets `asyncio_mode = "auto"`, ruff line length 100. `.gitignore` covers `.env`, `.venv`, `__pycache__`.
- [ ] **Step 2:** `compose.test.yml`: service `db`, image `postgres:17`, port `54329:5432`, `POSTGRES_PASSWORD=postgres`, `POSTGRES_DB=ampera_test`, tmpfs data dir.
- [ ] **Step 3: Write failing tests** in `tests/test_health.py`:
  - `test_health_ok`: `GET /api/v1/health` → 200, `{"status":"ok"}`.
  - `test_unknown_route_uses_error_format`: `GET /api/v1/nope` → 404, body `["error"]["code"] == "NOT_FOUND"`.
  - `test_validation_error_format`: a test-only route with a Pydantic body posted `{}` → 422, code `VALIDATION_ERROR`, `details` is a list.
- [ ] **Step 4:** `docker compose -f compose.test.yml up -d && uv run pytest` → FAIL (no app).
- [ ] **Step 5:** Implement `create_app()`: router prefix `/api/v1`, exception handlers for `AppError`, `RequestValidationError`, Starlette `HTTPException` (map 404/405), and bare `Exception` → 500 `INTERNAL` "Something went wrong" (log traceback, never return it). Lifespan runs `SELECT 1` and exits with a clear message naming `DATABASE_URL` (value not printed) if it fails. `conftest.py` sets `ENVIRONMENT=test`, `DATABASE_URL` to the test URL, `APP_ORIGIN=http://localhost:3000` before importing the app.
- [ ] **Step 6:** `uv run pytest` → PASS; `uv run ruff check .` clean.
- [ ] **Step 7:** README: prerequisites, env vars, how to run tests. `.env.example` with placeholders only. Commit `feat(backend): scaffold app, config, error format, test harness`.

### Task 2: Database models and initial migration

**Files:**
- Create: `app/core/models.py` (Base, `TimestampMixin`, UUID pk mixin), `app/modules/auth/models.py` (`User`, `Session`, `PasswordResetToken`, `LoginAttempt`), `app/modules/projects/models.py` (`Project`, `ProjectSpecification`), `app/modules/audit/models.py` (`AuditLog`), `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`
- Test: `tests/test_migrations.py`; update `tests/conftest.py`

**Interfaces:**
- Produces: ORM classes with columns exactly per spec §5; enums `UserRole`, `UserStatus`, `ProjectStatus` (Python `StrEnum`, Postgres enum types `user_role`, `user_status`, `project_status`). Conftest: session-scoped `alembic upgrade head`; function-scoped `TRUNCATE … RESTART IDENTITY CASCADE` of all tables after each test.

- [ ] **Step 1: Write failing test** `test_upgrade_downgrade_roundtrip`: on a fresh schema run `upgrade head` → `downgrade base` → `upgrade head` via Alembic API; assert tables `users, sessions, password_reset_tokens, login_attempts, projects, project_specifications, audit_logs` exist and `alembic_version` is `0001`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Write models and a hand-reviewed migration: `CREATE EXTENSION IF NOT EXISTS citext` (downgrade does not drop the extension), unique `users.email`, unique `sessions.token_hash`, unique `password_reset_tokens.token_hash`, unique `(project_id, revision)`, indexes `sessions(user_id)`, `sessions(expires_at)`, `login_attempts(email, ip, created_at)`, `projects(owner_id, updated_at DESC)`, `audit_logs(created_at DESC)`; FK cascades per spec. `alembic/env.py` reads URL from `get_settings()` and runs async.
- [ ] **Step 4:** Run → PASS. Commit `feat(backend): add data model and initial migration`.

### Task 3: Security primitives

**Files:**
- Create: `app/core/security.py`
- Test: `tests/test_security.py`

**Interfaces:**
- Produces: `hash_password(pw: str) -> str`, `verify_password(pw: str, hashed: str) -> bool` (False on mismatch, never raises), `new_token() -> str` (`secrets.token_urlsafe(32)`), `hash_token(token: str) -> str` (SHA-256 hex), `validate_password(pw: str, email: str) -> None` (raises `AppError` 422 `VALIDATION_ERROR` with message naming the rule).

- [ ] **Step 1: Write failing tests:** hash/verify roundtrip and wrong password → False; hash starts with `$argon2id$`; `hash_token` deterministic, 64 hex chars, differs from input; `validate_password` rejects 9 chars, 129 chars, password equal to email in different case; accepts 10 chars.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. **Step 5:** Commit `feat(backend): password hashing and token utilities`.

### Task 4: Register, login, logout, me, sessions, origin check

**Files:**
- Create: `app/core/deps.py`, `app/core/origin.py`, `app/modules/auth/{router.py,service.py,schemas.py}`
- Test: `tests/auth/test_session_flow.py`, `tests/test_origin.py`, `tests/factories.py`

**Interfaces:**
- Consumes: Task 3 functions; `User`, `Session` models.
- Produces: `current_user(request, db) -> User` dependency (401 `UNAUTHENTICATED` if cookie missing/unknown/expired, user missing, or user `DISABLED`; slides expiry if last extension >1h ago); `require_super_admin(user) -> User` (403); `UserOut` schema (`id, email, name, role, status, last_login_at, created_at`); service `create_session(db, user, ip, ua) -> str` (returns raw token) and `revoke_sessions(db, user_id, except_token_hash: str | None = None) -> None`; test factories `make_user(db, email=..., password="correct-horse-1", role=UserRole.USER, status=UserStatus.ACTIVE) -> User` and `login(client, email, password) -> None` (cookie stored on client).
- Origin middleware: for POST/PUT/PATCH/DELETE, missing or mismatched `Origin` → 403 `FORBIDDEN`.

- [ ] **Step 1: Write failing tests:**
  - `test_register_signs_in_and_me_returns_user`: POST `/auth/register` `{name:"Ana", email:" Ana@X.com ", password:"correct-horse-1"}` → 201, `Set-Cookie` contains `ampera_session` and `HttpOnly`; `GET /auth/me` → `email == "ana@x.com"`, `role == "USER"`.
  - `test_register_duplicate_email_case_insensitive` → second register with `ANA@x.com` → 409 `CONFLICT`.
  - `test_register_weak_password` → 422.
  - `test_login_logout`: login → 200 & cookie; logout → 204; `/auth/me` → 401.
  - `test_login_wrong_password_generic_message` and `test_login_unknown_email_same_message`: both 401 with identical `message`.
  - `test_disabled_user_cannot_login` → 401.
  - `test_expired_session_rejected`: set session `expires_at` in the past → `/auth/me` 401.
  - `test_session_sliding_expiry`: session last extended 2h ago → `/auth/me` pushes `expires_at` to now+7d; extended 10 min ago → unchanged.
  - `test_session_db_stores_hash_only`: stored `token_hash` ≠ cookie value and == `hash_token(cookie)`.
  - `test_cross_origin_post_rejected`: register with `Origin: https://evil.example` → 403; with no Origin → 403.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Email normalised with `.strip().lower()` in schemas. Login records a `LoginAttempt` row and updates `last_login_at` on success. Cookie `Secure` per Global Constraints.
- [ ] **Step 4:** Run → PASS. **Step 5:** Commit `feat(backend): registration, login, sessions, origin check`.

### Task 5: Login throttling, change password, password reset

**Files:**
- Modify: `app/modules/auth/{router.py,service.py,schemas.py}`
- Test: `tests/auth/test_throttle.py`, `tests/auth/test_password_reset.py`, `tests/auth/test_change_password.py`

**Interfaces:**
- Consumes: Task 4 `create_session`, `revoke_sessions`, factories.
- Produces: endpoints `POST /auth/password-reset/request {email}` → 202; `POST /auth/password-reset/confirm {token, password}` → 204; `POST /auth/change-password {current_password, new_password}` → 204.

- [ ] **Step 1: Write failing tests:**
  - `test_sixth_failed_login_is_rate_limited`: 5 wrong passwords → 401 each; 6th (even with correct password) → 429 `RATE_LIMITED`; attempts older than 15 min (backdate rows) don't count.
  - `test_reset_request_always_202` for existing and unknown email; unknown creates no token row.
  - `test_reset_confirm_works_once`: obtain raw token by monkeypatching the service's token generator; confirm → 204; login with new password OK; reuse token → 422; existing sessions revoked (old cookie → 401).
  - `test_reset_token_expires` (backdate 31 min) → 422.
  - `test_reset_link_logged_only_in_development` via `caplog`: `ENVIRONMENT=test` → link not logged.
  - `test_change_password_revokes_other_sessions`: two logins (clients A and B); A changes password → A still authorised, B → 401; wrong current password → 401.
- [ ] **Step 2:** FAIL. **Step 3:** Implement; reset link format `{APP_ORIGIN}/reset-password?token=…`. **Step 4:** PASS. **Step 5:** Commit `feat(backend): login throttling, password reset and change`.

### Task 6: Admin user management, audit log, bootstrap CLI

**Files:**
- Create: `app/modules/users/{router.py,service.py,schemas.py}`, `app/modules/audit/service.py`, `app/cli.py`, `app/core/pagination.py`
- Test: `tests/admin/test_users_admin.py`, `tests/admin/test_last_admin.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `require_super_admin`, `UserOut`, `revoke_sessions`, `hash_password`, `validate_password`.
- Produces: `Page[T]` generic (`items, page, page_size, total`) and `PageParams` dependency in `app/core/pagination.py` (reused by Task 7); `record_audit(db, actor_id: UUID | None, action: str, target_type: str, target_id: UUID | None, metadata: dict | None = None) -> None`; endpoints per spec §6 Admin; `AdminUserUpdate` (`name?, role?, status?, daily_run_limit? (int ≥0 or null)`); CLI `python -m app.cli create-superadmin --email EMAIL [--name NAME]` reading password via `getpass` twice.

- [ ] **Step 1: Write failing tests:**
  - `test_non_admin_forbidden_everywhere`: USER hitting each of the 5 admin routes → 403.
  - `test_list_search_filter_paginate`: 25 users; `page_size=10&page=3` → 5 items, `total` 25; `q=ana` matches name or email; `role=SUPER_ADMIN` filter; `page_size=101` → 422.
  - `test_create_user_and_duplicate` → 201 then 409.
  - `test_disable_user_revokes_sessions`: user logged in on another client; admin PATCH `status=DISABLED` → user's next request 401.
  - `test_delete_user_cascades_projects` and their old cookie → 401.
  - `test_audit_rows_written` for create, update (role change has `metadata.role` from→to), delete; metadata never contains `password`.
  - `test_last_admin_cannot_be_demoted_disabled_or_deleted` → 409 each; with two admins, demotion succeeds.
  - `test_admin_cannot_delete_self` → 409.
  - `test_cli_creates_superadmin`: invoke with patched `getpass` → user exists with role `SUPER_ADMIN`; audit `user.bootstrap_superadmin`; mismatched passwords → non-zero exit, no user.
- [ ] **Step 2:** FAIL. **Step 3:** Implement; last-admin check counts `role=SUPER_ADMIN AND status=ACTIVE` inside the same transaction with `SELECT … FOR UPDATE`. **Step 4:** PASS. **Step 5:** Commit `feat(backend): admin user management, audit log, superadmin CLI`.

### Task 7: Projects CRUD with ownership isolation

**Files:**
- Create: `app/modules/projects/{router.py,service.py,schemas.py}`
- Test: `tests/projects/test_projects.py`, `tests/projects/test_isolation.py`

**Interfaces:**
- Consumes: `current_user`, `Page`, `PageParams`.
- Produces: `get_project_for(db, user: User, project_id: UUID) -> Project` (raises `NotFound` unless owner or SUPER_ADMIN) — the single access gate reused by Task 8; `ProjectOut` (`id, name, description, status, owner_id, created_at, updated_at, completeness: float`); `GET /projects` accepts `q`, `status`, pagination; sorted `updated_at DESC`.

- [ ] **Step 1: Write failing tests:**
  - CRUD happy path: create → 201 with `status == "DRAFT"`, `completeness == 0.0`; patch name/status; delete → 204 then GET 404.
  - Validation: empty name, 161-char name → 422.
  - `test_isolation`: users A, B; A's project; B: GET, PATCH, DELETE, `GET …/specification`, `PUT …/specification` all → 404 and A's project unchanged; B's list excludes it; SUPER_ADMIN GET → 200 and list includes both users' projects.
  - `test_random_uuid_and_malformed_id`: unknown UUID → 404; `"abc"` → 422.
- [ ] **Step 2:** FAIL. **Step 3:** Implement; `completeness` = filled sections / 4 (basics, problem, requirements, constraints; a section is filled when any field is non-empty) computed from the latest spec. **Step 4:** PASS. **Step 5:** Commit `feat(backend): private projects with ownership isolation`.

### Task 8: Project specifications (append-only revisions)

**Files:**
- Create: `app/modules/projects/spec_schema.py`
- Modify: `app/modules/projects/{router.py,service.py}`
- Test: `tests/projects/test_specification.py`

**Interfaces:**
- Consumes: `get_project_for`.
- Produces: `ProjectSpecification` Pydantic model (shape per spec §5, `extra="forbid"`, all optional, trimmed strings, caps per Global Constraints, `currency` matches `^[A-Z]{3}$`, `budget.min ≤ budget.max`); `SpecOut` (`revision: int, data: ProjectSpecification, created_at, created_by`); GET returns `{revision: 0, data: {}}` when none; PUT returns 201 `SpecOut`. Saving `basics.name` also updates `projects.name`.

- [ ] **Step 1: Write failing tests:** first PUT → revision 1; second → 2 and revision 1 row unchanged in DB; GET returns latest; unknown key → 422; 5001-char long text → 422; 51-item list → 422; `currency:"usd"` → 422; `min > max` → 422; `test_concurrent_puts` with `asyncio.gather` of two PUTs on separate sessions → statuses ⊆ {201, 409}, revisions unique, no 500.
- [ ] **Step 2:** FAIL. **Step 3:** Implement: `next = coalesce(max(revision),0)+1`; on `IntegrityError` retry once, then raise `Conflict`. Touch `projects.updated_at`. **Step 4:** PASS; full suite `uv run pytest` PASS; `ruff` clean. **Step 5:** Commit `feat(backend): revisioned project specifications`.

---

## Frontend (`poc-frontend`, branch `feat/foundation`)

### Task 9: Next.js scaffold, design tokens, fonts, UI primitives

**Files:**
- Create via `npx create-next-app@latest . --ts --app --src-dir --tailwind --eslint --no-turbopack --import-alias "@/*"`; then: `src/styles/tokens.css`, `src/app/globals.css`, `src/app/layout.tsx`, `public/brand/ampera-logo.png`, `public/brand/ampera-mark.png`, `src/app/icon.png`, `public/fonts/` (Switzer 300/400/500 woff2, Fragment Mono 400 woff2), `src/components/ui/{button,input,textarea,label,field,select,dialog,alert-dialog,dropdown-menu,badge,skeleton,table,toast,empty-state,error-state}.tsx`, `vitest.config.ts`, `src/test/setup.ts`, `next.config.ts`, `.env.example`, `README.md`
- Test: `src/components/ui/__tests__/button.test.tsx`, `field.test.tsx`, `dialog.test.tsx`

**Interfaces:**
- Produces: CSS variables exactly per spec §8 exposed to Tailwind via `@theme` (`bg-primary`, `text-muted`, `rounded-pill`, `font-sans`, …); `Button` props `variant: "primary"|"secondary"|"tertiary"|"danger"`, `size: "sm"|"md"`, `loading?: boolean` (sets `aria-busy`, disables); `Field` (`label`, `error?`, `hint?`, wires `aria-describedby`/`aria-invalid`); `EmptyState({title, description, action?})`; `ErrorState({message, onRetry?})`. `next.config.ts` rewrites `/api/:path*` → `${BACKEND_URL ?? "http://localhost:8000"}/api/:path*`.

- [ ] **Step 1:** Scaffold; add deps `@radix-ui/react-{dialog,alert-dialog,dropdown-menu,select,label,slot} @tanstack/react-query react-hook-form zod @hookform/resolvers lucide-react clsx`; dev `vitest @vitejs/plugin-react jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom`. Download brand PNGs from the site URLs recorded in the spec; download Switzer from Fontshare and Fragment Mono from Google Fonts into `public/fonts`, `@font-face` with `font-display: swap`.
- [ ] **Step 2: Write failing tests:** Button renders each variant; `loading` → `disabled` and `aria-busy="true"`; Field associates label with input (`getByLabelText`) and error sets `aria-invalid="true"` and is announced via `aria-describedby`; Dialog traps focus and closes on Escape returning focus to trigger.
- [ ] **Step 3:** `npx vitest run` → FAIL. **Step 4:** Implement primitives (Radix where listed). **Step 5:** PASS; `npm run lint` and `npx tsc --noEmit` clean.
- [ ] **Step 6:** Commit `feat(frontend): scaffold, Ampera design tokens, UI primitives`.

### Task 10: API client and auth pages

**Files:**
- Create: `src/lib/api/client.ts`, `src/lib/api/auth.ts`, `src/lib/schemas/auth.ts`, `src/app/providers.tsx`, `src/app/(auth)/layout.tsx`, `src/app/(auth)/{login,register,forgot-password,reset-password}/page.tsx`, `src/middleware.ts`
- Test: `src/app/(auth)/__tests__/login.test.tsx`, `register.test.tsx`, `reset-password.test.tsx`, `src/lib/api/__tests__/client.test.ts`

**Interfaces:**
- Produces: `apiFetch<T>(path: string, init?: RequestInit & {json?: unknown}): Promise<T>` (prefix `/api/v1`, `credentials: "include"`, JSON body; non-2xx throws `ApiError {status, code, message, details}`; 204 → `undefined`); global handler: on `ApiError.status === 401` outside auth pages → `window.location.assign("/login?expired=1&next=" + encodeURIComponent(path))`; hooks `useMe()`, `useLogin()`, `useRegister()`, `useLogout()`, `useRequestReset()`, `useConfirmReset()`; zod schemas mirroring backend rules (password 10–128, name 1–120). Middleware: if no `ampera_session` cookie and path is under the app group → redirect `/login?next=…`; if cookie present on `/login` or `/register` → redirect `/dashboard`.

- [ ] **Step 1: Write failing tests** (mock `fetch`): `apiFetch` maps error body to `ApiError`; login shows field errors for empty submit, shows server message on 401, shows "Too many attempts. Try again in 15 minutes." on 429, navigates to `next` or `/dashboard` on success; `/login?expired=1` shows "Your session expired. Please sign in again."; register validates password length client-side; forgot-password always shows "If an account exists, we've sent a reset link."; reset-password with missing token shows an error state.
- [ ] **Step 2:** FAIL. **Step 3:** Implement auth layout (navy brand panel with logo on cream chip + value line "AI-assisted project delivery, from idea to approved plan."; form on paper; single column <768px), password visibility toggle. **Step 4:** PASS. **Step 5:** Commit `feat(frontend): API client and authentication pages`.

### Task 11: App shell with role-aware navigation

**Files:**
- Create: `src/app/(app)/layout.tsx`, `src/components/shell/{sidebar,header,breadcrumbs,profile-menu}.tsx`, `src/lib/nav.ts`
- Test: `src/components/shell/__tests__/sidebar.test.tsx`, `profile-menu.test.tsx`

**Interfaces:**
- Consumes: `useMe`, `useLogout`.
- Produces: `navItems(role: "USER"|"SUPER_ADMIN"): NavItem[]` (`Dashboard`, `Projects`, `Account`, plus `Admin › Users` for SUPER_ADMIN); `Breadcrumbs` reads a `useBreadcrumbs(items)` context set by pages; sidebar collapsed state in `localStorage` key `ampera.sidebar.collapsed` wrapped in try/catch.

- [ ] **Step 1: Write failing tests:** USER sees no Admin link; SUPER_ADMIN sees it; current route link has `aria-current="page"`; collapse button toggles `aria-expanded` and persists; below 768px sidebar renders as a dialog drawer opened by a "Menu" button; profile menu shows name + role badge and "Sign out" calls logout then routes to `/login`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement (expanded: logo PNG height 28 on cream chip; collapsed: mark 28×28; skip-to-content link first in DOM). **Step 4:** PASS. **Step 5:** Commit `feat(frontend): application shell and role-aware navigation`.

### Task 12: Dashboard and projects list

**Files:**
- Create: `src/lib/api/projects.ts`, `src/lib/schemas/project.ts`, `src/app/(app)/dashboard/page.tsx`, `src/app/(app)/projects/page.tsx`, `src/components/projects/{project-table,status-badge,completeness}.tsx`
- Test: `src/app/(app)/__tests__/dashboard.test.tsx`, `projects.test.tsx`, `src/lib/api/__tests__/unauthorized.test.ts`

**Interfaces:**
- Produces: hooks `useProjects({q?, status?, page?})`, `useProject(id)`, `useCreateProject()`, `useUpdateProject(id)`, `useDeleteProject()`, `useSpecification(id)`, `useSaveSpecification(id)`; `Project`/`Spec` TS types mirroring `ProjectOut`/`SpecOut`.

- [ ] **Step 1: Write failing tests:** dashboard empty state "Create your first project" when list empty; shows up to 6 projects and a "Drafts to finish" section listing `DRAFT` with completeness <1; projects page search debounces (300 ms) and passes `q`; row actions rename (inline dialog) and archive (PATCH `status: "ARCHIVED"`) update the row; delete asks confirmation and removes the row; loading shows skeletons; API failure shows ErrorState with working Retry; any query returning 401 triggers redirect to `/login?expired=1…`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. **Step 5:** Commit `feat(frontend): dashboard and projects list`.

### Task 13: Project wizard and project overview

**Files:**
- Create: `src/app/(app)/projects/new/page.tsx`, `src/app/(app)/projects/[id]/edit/page.tsx`, `src/app/(app)/projects/[id]/page.tsx`, `src/components/wizard/{wizard,stepper,step-basics,step-problem,step-requirements,step-constraints,step-review,list-input}.tsx`, `src/lib/schemas/spec.ts`
- Test: `src/components/wizard/__tests__/wizard.test.tsx`, `src/app/(app)/projects/[id]/__tests__/overview.test.tsx`

**Interfaces:**
- Consumes: project/spec hooks from Task 12.
- Produces: `<Wizard projectId?: string initialStep?: number />`. Step 1 "Next" without `projectId` calls `useCreateProject` then `router.replace("/projects/{id}/edit?step=2")`; every Next/Save draft PUTs the whole spec; Review "Finish" PATCHes `status: "ACTIVE"` and routes to overview. `ListInput` edits string arrays (add with Enter, remove button with accessible label "Remove {item}").

- [ ] **Step 1: Write failing tests:**
  - Only name is required; Next on step 1 with empty name shows "Project name is required".
  - **Double-click Next on step 1 calls create exactly once** (button `loading` while pending).
  - Leaving step with unsaved edits triggers confirm dialog; `beforeunload` is registered while dirty.
  - Revisiting `/projects/{id}/edit?step=3` pre-fills from latest spec.
  - Review lists empty fields as "Missing — will be treated as an assumption" and Finish sets ACTIVE.
  - Stepper: completed steps clickable, current has `aria-current="step"`.
  - Overview shows spec summary, "Edit" link, and five locked stages labelled "Available in a coming release"; unknown/foreign project (404) shows not-found state.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. **Step 5:** Commit `feat(frontend): project creation wizard and overview`.

### Task 14: Admin users and account pages

**Files:**
- Create: `src/lib/api/admin.ts`, `src/app/(app)/admin/users/page.tsx`, `src/components/admin/{users-table,user-panel,create-user-dialog,delete-user-dialog}.tsx`, `src/app/(app)/account/page.tsx`
- Test: `src/app/(app)/admin/__tests__/users.test.tsx`, `src/app/(app)/account/__tests__/account.test.tsx`

**Interfaces:**
- Produces: hooks `useAdminUsers({q?, role?, status?, page?})`, `useCreateUser()`, `useUpdateUser(id)`, `useDeleteUser()`, `useChangePassword()`.

- [ ] **Step 1: Write failing tests:** USER visiting `/admin/users` sees forbidden state (driven by API 403, not just hidden link); table filters by role/status and paginates; edit panel saves role/status/limit; 409 from server shown inline in the panel; delete button disabled until typed email matches exactly; account change-password shows success toast and validation errors; wrong current password message shown.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. **Step 5:** Commit `feat(frontend): admin user management and account page`.

### Task 15: End-to-end and accessibility tests, docs

**Files:**
- Create: `playwright.config.ts`, `e2e/global-setup.ts`, `e2e/{auth-and-wizard,admin,isolation,a11y}.spec.ts`
- Modify: `poc-frontend/README.md`, `poc-backend/README.md`

**Interfaces:**
- Consumes: backend running with test DB (`ENVIRONMENT=test`, migrated) on :8000 and `next dev` on :3000, both started by Playwright `webServer`; global setup truncates tables and creates a Super Admin via `uv run python -m app.cli` with password from env `E2E_ADMIN_PASSWORD` (set in config, test-only).

- [ ] **Step 1: Write tests:**
  - `auth-and-wizard`: register → dashboard empty state → wizard steps 1–2 → navigate away → reopen project → edit resumes at saved data → finish → overview shows ACTIVE → sign out → `/dashboard` redirects to login.
  - `admin`: admin disables a logged-in user (second browser context) → that user's next navigation lands on `/login?expired=1`.
  - `isolation`: user B opens user A's project URL → not-found state; A's name never appears.
  - `a11y`: axe on login, register, dashboard, wizard step 1, admin users → zero `serious`/`critical`.
- [ ] **Step 2:** `npx playwright test` → fix failures until PASS.
- [ ] **Step 3:** READMEs: local setup for both repos, Supabase direct/session pooler note, superadmin bootstrap, test commands, known limitations (no email provider, no dark mode, AI stages locked).
- [ ] **Step 4:** Run full verification: backend `uv run pytest && uv run ruff check .`; frontend `npx tsc --noEmit && npm run lint && npx vitest run && npx playwright test`. All PASS.
- [ ] **Step 5:** Commit in each repo `test: e2e and accessibility coverage; docs`.
