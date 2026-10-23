# Ampera Project Delivery — Backend (poc-backend)

FastAPI service for the Ampera AI-powered project-delivery platform.
Design: [`docs/specs/2026-10-23-foundation-design.md`](docs/specs/2026-10-23-foundation-design.md)

## Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Docker (for the test database)
- A PostgreSQL database (Supabase in real use)

## Configuration

Copy `.env.example` to `.env`:

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://…`. For Supabase use the **direct or session-pooler** string (port 5432); the transaction pooler (6543) is not supported. |
| `APP_ORIGIN` | Origin of the web app, e.g. `http://localhost:3000`. Mutating requests from other origins are rejected. |
| `ENVIRONMENT` | `development`, `test` or `production`. Cookies are `Secure` only in production. |

## Run

```bash
uv sync
uv run alembic upgrade head
uv run python -m app.cli create-superadmin --email you@example.com --name "Your Name"
uv run fastapi dev app/main.py
```

`create-superadmin` prompts for the password twice; no default accounts exist.

## Test

Tests use a throwaway local Postgres and never touch Supabase.

```bash
docker compose -f compose.test.yml up -d --wait
uv run pytest
uv run ruff check .
```

## Known limitations (Foundation release)

- **Password reset email:** no email provider is configured. In `development` the reset link is
  written to the server log; in other environments the request is accepted but nothing is sent.
- **AI agents:** not part of this release; `daily_run_limit` is stored for later use.
- **Login throttling** is per email + IP and stored in Postgres (`login_attempts`); old rows are
  not pruned automatically yet.
