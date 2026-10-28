# Increment C3 — Development Agent (scaffold script), Validation, Repair

- **Date:** 2026-10-28
- **Status:** Implemented 2026-10-28
- **Builds on:** `2026-10-27-c2-plan-budget-design.md` (C2: plan and budget stages)
- **Repos:** `poc-backend` (FastAPI + MongoDB), `poc-frontend` (Next.js 16, SCSS modules)

## 1. Why

C2 ends at an approved plan and budget. The `code` stage still shows as "Coming soon". C3 adds the
**Development Agent**. From the approved architecture and plan it produces **one downloadable bash
script** that scaffolds the project on the user's machine. **Validation** checks the script
statically with `bash -n` and `shellcheck`. **Repair** sends the findings back to the agent and
fixes the files that failed.

### Owner decisions (2026-10-28)

| Topic | Decision |
|---|---|
| Deliverable | **One `.sh` script.** The user downloads it, reads it, and runs it on their own machine. |
| Execution | **The platform never runs generated code.** There is no sandbox. This replaces the "isolated sandbox" item in CLAUDE.md for C3: validation is static only (`bash -n` parses, `shellcheck` lints, and neither one executes the script). |
| Script powers | **Write files only.** `mkdir` plus quoted heredocs. No network, no installs, no `git init`, no `sudo`, no `rm`. It ends by printing the next steps for the user to run. |
| Validation | `bash -n` plus `shellcheck` (bundled through the `shellcheck-py` pip package, so deployment needs no apt step). |
| Repair | Bounded LLM fix of only the failing files, using the findings. |
| Shape | One normal stage `code` (artifact kind `code`). It has an artifact, an approval, a chat, and a staleness cascade like the other stages. Validation and repair are steps inside the stage run, not separate stages. |

### Success criteria (automated tests unless noted)

1. After the plan is approved (and after the budget, when the budget stage is planned), the
   Development Agent runs with no clicks. The graph then pauses for code approval and ends when
   the code is approved.
2. The stored revision holds a file manifest. The script is **rendered by code** from that manifest,
   and the same manifest always gives a byte-identical script.
3. `bash -n` and `shellcheck` run on the rendered script and on every shell file in the manifest. The
   results are stored on the revision. Any `error` or `warning` finding blocks approval.
4. Using the mock provider, a manifest with a deliberate shellcheck warning is repaired in one round
   by real shellcheck. The repair only touches the failing file.
5. Running the downloaded script in a temp directory (test only, with the trusted mock fixture)
   creates exactly the manifest's files with their exact content. A second run into the same
   non-empty directory exits non-zero and changes nothing.
6. Changing the approved architecture or plan marks the code stale. Changing rates does not.
7. Path traversal (`../x`, `/etc/x`), a duplicate path, a `.env` file, or a file that is too large is
   rejected before rendering.
8. Ownership holds on the download route (other users get 404). No test calls Groq.

## 2. Scope

**In:** Development Agent (generate, chat, repair), manifest schema and validator, script renderer,
static validator (bash -n plus shellcheck), the download route, the Code page, and graph, policy and
staleness wiring.

**Out:**
- running the script anywhere
- installing dependencies, generators (`create-next-app`, `uv init`) or `git init` inside the script
- zip export
- editing files by hand (changes go through chat)
- syntax highlighting
- a CI pipeline for the generated project

## 3. Development Agent (`app/modules/agents/code/`, agent `developer`, stage `code`)

**Inputs (from `run.inputs`, approved revisions):**
- architecture (nodes, edges and technologies)
- plan (milestones and tasks)
- preferences: `preferred_technologies`, `deployment_preference`

**Modes:**
- `generate`: a full manifest
- `chat`: file changes from an instruction
- repair: an internal step of both modes, not a run mode

### 3.1 LLM output `code_output` (`code/1.0`), generate mode

```
summary: str (≤500)
project_slug: ^[a-z][a-z0-9-]{1,39}$            # default target directory name
files: [{path, content, executable: bool}]       # 1–60
next_steps: [str ≤200]                           # ≤10, printed at the end, e.g. "cd api && uv sync"
```

The prompt asks for a runnable skeleton, not a finished app:
- one folder per deployable architecture component, with an entry point, a manifest
  (`package.json`, `pyproject.toml`, …) and a health endpoint
- a root `README.md`
- `.env.example`
- `docker-compose.yml` when there is more than one service
- small `scripts/*.sh` helpers

Files are kept short (stubs and TODO comments that cite task IDs).

### 3.2 Chat and repair output `code_changes`

```
summary: str (≤500)
changes: [{op: "write", path, content, executable} | {op: "delete", path}]   # 1–30
```

The changes are applied to the current manifest in code, and the result is validated like a fresh
manifest. Sending changes instead of the whole manifest keeps prompts inside the ~8k input cap.

### 3.3 Code-generated files (no LLM)

The renderer adds `PLAN.md`: the plan's milestones as headings and its tasks as `- [ ] T-001 title
(role, Nd)`. It is generated from the source plan revision, so it always matches the approved plan.
If the LLM writes `PLAN.md`, that file is rejected.

## 4. Manifest validation (`code/validate.py`, save-blocking errors, sent to the repair loop)

- `path`:
  - is relative and matches `^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$`, ≤ 200 characters
  - has no segment `.` or `..`, and no segment that starts with `.git`
  - is unique when compared case-insensitively
  - does not collide with a directory (`a` and `a/b` cannot both exist)
- No file is named `.env`, `*.pem` or `id_rsa*`, or under `.ssh/` (only `.env.example`, etc.).
- `content` is UTF-8 text with no NUL bytes, ≤ 20 000 characters per file and ≤ 300 000 in total.
- **Secret scan:** a line matching common key patterns (`AKIA…`, `-----BEGIN … PRIVATE KEY`,
  `sk-…`) produces an error naming the file and line. Placeholders like `changeme` are fine.

## 5. Script renderer (`code/render.py`, pure function)

```bash
#!/usr/bin/env bash
# Scaffold for <project_slug>, generated by Ampera
# Sources: architecture rA, plan rP. Review before running. Writes files only.
set -euo pipefail
target="${1:-<project_slug>}"
if [ -e "$target" ] && [ -n "$(ls -A "$target" 2>/dev/null)" ]; then
  echo "Refusing to write into non-empty directory: $target" >&2
  exit 1
fi
mkdir -p "$target"
cd "$target"
mkdir -p 'api/app' 'web/src' …                 # every parent directory, sorted
cat > 'api/app/main.py' <<'AMPERA_EOF'
…content verbatim…
AMPERA_EOF
chmod +x 'scripts/dev.sh'                      # only executable files
printf '%s\n' "Scaffold written to $target" "Next steps:" '  cd api && uv sync' …
```

- Files are written in sorted path order, so the output is deterministic.
- Paths are already restricted by §4, so single quotes are safe. Next-step strings are
  single-quoted with `'` escaped as `'\''`.
- The heredoc delimiter is `AMPERA_EOF`. If any line of the content equals it, the renderer appends
  `_1`, `_2`, … until it is unique for that file. Quoted delimiters mean the content is never
  expanded.
- Content is normalised at save: CRLF becomes LF, and a missing trailing newline is added. The
  stored manifest is exactly what the script writes.
- The script's SHA-256 is stored on the revision and shown in the UI.

## 6. Validation (`code/check.py`)

- Runs `bash -n` on the rendered script, and `shellcheck -f json1 -S warning -s <dialect>` on:
  - the rendered script
  - every manifest file that ends in `.sh` or starts with a `#!/…sh` or `#!/…bash` shebang
- The script is passed on **stdin**, so nothing is written to disk and nothing is executed.
- Runs through `asyncio.create_subprocess_exec` (no shell) with a 20 s timeout per call, no network,
  and an empty environment.
- Findings: `{tool: "bash-n" | "shellcheck", file: "<script>" | path, line, code ("SC2086" or
  null), level: error|warning, message}`.
  - shellcheck `error` maps to the `error` level; shellcheck `warning` also maps to `error` for
    approval.
  - `info` and `style` findings are filtered out by `-S warning`.
- If shellcheck is missing or times out, the result is a stored error
  `VALIDATOR_UNAVAILABLE`. It blocks approval, the UI says "Validation could not run", and it is
  never reported as a pass.
- Stored on the revision as `validation_report {bash_n: ok|failed, shellcheck: ok|failed|unavailable,
  shellcheck_version, findings[], checked_at}`. Approval errors are mirrored into
  `rev.validation.issues`, so the existing approval blockers apply.

## 7. Repair loop

After generation (or after applying chat changes):
1. Validate the manifest (§4), render (§5), and check (§6).
2. If there are findings, send the **failing files only** (each file's path, content, and findings),
   plus the slug, to the LLM. Ask for `code_changes` that fix exactly those findings.
3. Apply the changes and go back to step 1. The loop runs at most **2 repair rounds**.
4. Then save the revision whatever the result. Remaining findings block approval, and the stage
   shows `NEEDS_REVISION` with the first three findings. The user can ask the chat to fix them.

Findings in the renderer's own lines (outside any heredoc) are bugs in our code, not in the LLM's
output. They are logged as errors and never sent to repair (they still block approval). A unit test keeps the
renderer itself shellcheck-clean.

Each round emits an activity step, for example "Validation: 2 shellcheck findings in
scripts/dev.sh — repairing (round 1 of 2)", then "Validation passed".

## 8. Graph, policy, staleness

- `orchestration/stages.py`:
  - `AVAILABLE` += `code`
  - `AGENTS["code"] = "developer"`
  - `DEPENDS_ON["code"] = ["architecture", "plan"]`
  - `LABEL["code"]` becomes "Scaffold"
- `artifacts/registry.DEPENDENTS`: `architecture` += `code` and `plan` += `code`. Budget is not a
  source, so rate changes never stale the scaffold.
- `delivery/graph.STAGE_NODES` += `code`. `nodes.AGENT_FOR` and `LABEL` += "Development Agent".
  `routing.Node` += `code`.
- `orchestrator/policy.py`:
  - `IMPLEMENTED` += `code`, with `approval_required` forced true
  - the default plan includes it
  - excluding `plan` also excludes `code`
  - ordering follows `STAGES`, so `code` runs after `budget` when the budget is planned
- `INPUT_CHECKS["code"]` returns False.

## 9. API (under `/api/v1`, using `get_project_for`)

| Method | Path | Notes |
|---|---|---|
| — | `/projects/{id}/artifacts/code…` | existing generic routes (get, revisions, approve, request changes) |
| GET | `/projects/{id}/artifacts/code/revisions/{n}/script` | `text/x-shellscript; charset=utf-8`, `Content-Disposition: attachment; filename="scaffold-<slug>-r<n>.sh"`, header `X-Content-SHA256`. Rendered on request from the stored manifest (deterministic). |
| — | `/projects/{id}/conversations/code/messages` | existing chat route; `code` added to the allowed stages |

## 10. Prompts and token budget

- **Generate:**
  - components as `id | type | name | technology`
  - edges as `from→to`
  - milestones as `id | title`
  - tasks as `id | title | component_ids`, trimmed to fit the cap
  - preferred technologies and deployment preference
  - `max_output_tokens` 14000
- **Chat:** the digest plus the manifest as `path (N lines)` plus the full content of files whose path
  appears in the instruction (up to 3).
- **Repair:** the failing files and findings only (§7).

## 11. Frontend (`poc-frontend`, SCSS modules)

`/projects/[id]/code`, page title "Scaffold". It uses the same layout as plan and budget: the
artifact on the left, `AgentChatPanel` on the right, and `ApprovalBar` on top. The left side holds:

- **Download card:**
  - a "Download script" button (the primary action) with the file name and size
  - the SHA-256, with a copy button
  - the run instructions `bash scaffold-<slug>-r<n>.sh ./my-project` with a copy button
  - the note "Read it first. It only writes files."
- **Validation panel:**
  - `bash -n` and `shellcheck` rows, each with a pass, failed or unavailable status and the
    shellcheck version
  - the findings list (file:line, SC code linked to the shellcheck wiki, message)
  - a repair summary taken from the run's activity steps
- **Files:**
  - a tree of the manifest (directories collapsible, `PLAN.md` marked "From the plan")
  - a selected-file viewer in a `<pre>` with line numbers
  - executable files marked
  - for screens under 768px, the tree stacks above the viewer

Other changes:
- The stage card, `ICON`, the stage routes and the 3D graph label get "Scaffold". The ghosted Code
  planet becomes a real one.
- zod schema `lib/schemas/code.ts` mirrors §3 and §6 for display only.

## 12. Security

- The platform never executes the script or any manifest file. `bash -n` and `shellcheck` are
  static. Subprocesses use exec with arguments (no shell), stdin input, a timeout and an empty
  environment.
- Path rules (§4) and quoted heredocs mean a file cannot escape the target directory or inject
  shell code.
- The script refuses non-empty targets and has no `rm`, `sudo`, `curl` or network calls. The
  renderer owns every line outside the heredocs.
- Secrets: no `.env` files, plus a key-pattern scan. Prompts get no secrets, because the inputs are
  approved artifacts only.
- Download access is owner-only (404 for others), like the other artifact routes.

## 13. Testing

- **Unit:**
  - manifest validator (each rule in §4)
  - renderer: determinism, a delimiter collision, the trailing newline, single-quote escaping in next
    steps, and the renderer's own output being shellcheck-clean
  - checker: bash -n syntax error, a shellcheck warning mapped to error, the unavailable and timeout
    paths, and dialect from the shebang
  - change application (write, delete, a delete of a missing path is an error)
  - `PLAN.md` from the plan
- **Agents (mock provider):**
  - generate → validation passes
  - generate with a bad `scripts/dev.sh` → one repair round → passes; the repair prompt holds only
    that file
  - repair that never fixes → saved after 2 rounds, with approval blocked
  - chat changes
- **Integration (test only):** run the rendered mock-fixture script with `bash` into `tmp_path`. Then
  compare the files on disk with the manifest, check that the second run exits 1, and check that a
  file named `AMPERA_EOF` in the content round-trips.
- **Graph:** plan approved (and budget approved) → code → gate → approve → END. An architecture
  change marks the code stale. A rate change leaves the code fresh.
- **API:** the download's headers and body equal `render(manifest)`; another user gets 404.
- **Frontend:**
  - Vitest for the code view (tree, viewer, validation states including unavailable, download link
    and copy)
  - Playwright + axe: extend `e2e/plan-budget.spec.ts` through an approved scaffold and a download
    whose body starts with `#!/usr/bin/env bash`; check for overflow at 375px

## 14. Risks

- **Output size against the token caps:** about 60 short files may still be cut off. Mitigations:
  stubs-only prompting, the 14k output cap, and `TOO_LARGE` surfacing as in C1. Chat can add files
  later.
- **A lint-clean skeleton that doesn't run:** validation only checks shell syntax and lint, not
  whether `npm install` works. The UI says what was checked. The user approves the scaffold.
- **shellcheck missing in a deployment:** it is a pinned runtime dependency (`shellcheck-py`), and
  `init-db` or startup logs its version. A missing binary fails closed (`VALIDATOR_UNAVAILABLE`).
