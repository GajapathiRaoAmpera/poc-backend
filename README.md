# Ampera Project Delivery — Backend (poc-backend)

FastAPI service for the Ampera AI-powered project-delivery platform, backed by MongoDB.
Design: [`docs/specs/2026-10-23-foundation-design.md`](docs/specs/2026-10-23-foundation-design.md)

## Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Docker (for the local test database)
- A MongoDB database: Atlas in real use (must be a replica set — Atlas always is — because
  user deletion and the last-Super-Admin check use transactions)

## Configuration

Copy `.env.example` to `.env`:

| Variable | Meaning |
|---|---|
| `MONGODB_URI` | Atlas connection string (`mongodb+srv://…`). In Atlas → Network Access, allow the IP of the machine running the API. |
| `MONGODB_DB` | Database name (default `ampera`). |
| `APP_ORIGIN` | Origin of the web app, e.g. `http://localhost:3000`. Mutating requests from other origins are rejected. |
| `ENVIRONMENT` | `development`, `test` or `production` (default when unset). Cookies are `Secure` and reset links are never logged outside development/test. |
| `TRUSTED_PROXY_IPS` | Comma-separated IPs of reverse proxies allowed to set `X-Forwarded-For`. **Set this to the Next.js server's IP when it runs on a different host**, otherwise every user shares one IP for login throttling. |

## Run

```bash
uv sync
uv run python -m app.cli init-db          # creates indexes (also runs on API startup)
uv run python -m app.cli create-superadmin --email you@example.com --name "Your Name"
uv run fastapi dev app/main.py
```

`create-superadmin` prompts for the password twice; no default accounts exist.

## Test

Tests use a throwaway local MongoDB replica set (`mongo:9.0` — 8.x refuses to start on Linux kernels ≥ 6.19) and never touch Atlas: they ignore `MONGODB_URI` and refuse to run unless `TEST_MONGODB_URI` (default `mongodb://localhost:27018`) is local and `TEST_MONGODB_DB` (default `ampera_test`) ends in `_test`.

```bash
docker compose -f compose.test.yml up -d --wait
uv run pytest
uv run ruff check .
```

## Known limitations (Foundation release)

- **Password reset email:** no email provider is configured. In `development` the reset link is
  written to the server log; in other environments the request is accepted but nothing is sent.
- **AI agents:** not part of this release; `daily_run_limit` is stored for later use.
- **Login throttling** is per email + IP (`login_attempts`, auto-deleted after 24 h by a TTL index).
  Expired sessions and reset tokens are also removed by TTL indexes (MongoDB checks about once a
  minute; the API also checks expiry on every request).
