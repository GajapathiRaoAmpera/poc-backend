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
| `LLM_PROVIDER` | `groq` (default) or `mock`. `mock` is for tests/offline demos and is refused when `ENVIRONMENT=production`; runs made with it are labelled "Mock provider" in the UI. |
| `GROQ_API_KEY` | Groq API key (server-side only; never logged or sent to the browser). Required when `LLM_PROVIDER=groq`. |
| `GROQ_MODEL` | Default `openai/gpt-oss-120b`. Must be a Groq model that supports `response_format: json_schema`. |
| `PLATFORM_DAILY_RUN_LIMIT` | Agent runs per user per rolling 24 h (default 50; a Super Admin can override per user). |
| `AGENT_TIMEOUT_S` | Per-attempt agent timeout in seconds (default 180). |
| `APP_ORIGIN` | Origin of the web app, e.g. `http://localhost:3000`. Mutating requests from other origins are rejected. |
| `ENVIRONMENT` | `development`, `test` or `production` (default when unset). Cookies are `Secure` and reset links are never logged outside development/test. |
| `TRUSTED_PROXY_IPS` | Comma-separated IPs of reverse proxies allowed to set `X-Forwarded-For`. **Set this to the Next.js server's IP when it runs on a different host**, otherwise every user shares one IP for login throttling. |

## Run

```bash
uv sync
uv run python -m app.cli init-db          # creates indexes (also runs on API startup)
uv run python -m app.cli create-superadmin --email you@example.com --name "Your Name"
uv run uvicorn app.main:app --reload --port 8000   # API
uv run python -m app.worker                         # agent worker (separate terminal)
```

AI agents run in the **worker**, not the API: if runs stay "Queued", the worker isn't running.
Several workers can run at once; jobs are leased in MongoDB, retried with backoff (max 3
attempts), time out after `AGENT_TIMEOUT_S`, and are picked up again if a worker crashes.

## Agentic delivery workflow

Design: [`docs/specs/2026-10-24-agentic-core-design.md`](docs/specs/2026-10-24-agentic-core-design.md)

A project starts from one sentence (`POST /api/v1/projects/intake`). Each project then has a
**LangGraph delivery graph** (`app/modules/delivery/`), run by the worker as `graph_step` jobs
and checkpointed to MongoDB (`graph_checkpoints`, `graph_checkpoint_writes`):

```
START → orchestrator ⇄ requirements | architecture | gate (interrupt) → END
```

- The **Project Orchestrator Agent** (`agents/orchestrator/`) turns the idea and the current state
  into a versioned plan (`plans`): which stages run, their inputs, approvals, questions, and the
  project brief (saved as a specification revision). A deterministic policy validates every plan;
  after one failed repair a safe default plan is used and labelled.
- A pure **router** (`delivery/routing.py`) picks the next node from the plan and fresh facts.
- **Gates** pause the graph for the user: orchestrator questions, requirements questions,
  approvals, loop budget (3 automatic re-runs per stage), quota, a busy stage.
- Approvals, answers, edits, preferences and chat messages call `delivery.notify`, which resumes
  the paused graph (one graph job per project; duplicates are no-ops).
- Each stage has its own **agent chat** (`/projects/{id}/conversations/{intake|requirements|
  architecture}/messages`); chat runs reply, record answers, revise drafts or propose change sets.
- Thread state for the UI: `GET /projects/{id}/delivery` (`delivery_threads`); live updates arrive
  on the existing SSE stream as `graph` and `message` events.

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
- **Agents available:** Project Orchestrator, Requirements, Architecture. Planning, Budget,
  Development, Validation and Repair agents are planned (C2/C3) and shown as unavailable.
- **Checkpointer:** the official `MongoDBSaver` uses a synchronous client in a thread pool (one
  extra MongoDB connection pool per worker).
- **Login throttling** is per email + IP (`login_attempts`, auto-deleted after 24 h by a TTL index).
  Expired sessions and reset tokens are also removed by TTL indexes (MongoDB checks about once a
  minute; the API also checks expiry on every request).
