import logging
import os

from cryptography.fernet import InvalidToken

from app.core.config import get_settings
from app.core.documents import now
from app.core.errors import AppError
from app.modules.agents.base import AgentContext, AgentOutcome
from app.modules.agents.code.schema import CodeFile
from app.modules.agents.deploy import git, prompts
from app.modules.agents.deploy.schema import DeployContent, DeployMessage
from app.modules.agents.structured import call_structured
from app.modules.agents.subtask import subtask
from app.modules.artifacts import service as artifacts
from app.modules.projects import service as projects

logger = logging.getLogger(__name__)

NAME = "deployer"
MISSING = "Add the repository URL and token on the Requirements page"


def _remote(url: str) -> str:
    """Test-only: e2e pushes to a local bare repo while the stored URL stays https (REPO_URL_RE)."""
    fake = os.environ.get("AMPERA_FAKE_GIT_REMOTE")
    return fake if get_settings().environment == "test" and fake else url


async def _token(ctx: AgentContext) -> str:
    try:
        token = await projects.repo_token(ctx.db, ctx.project)
    except (InvalidToken, AppError):  # unset or rotated key: clear state, no decrypt details
        raise AppError(
            "REPO_TOKEN_UNREADABLE",
            "The repository token can't be read. Enter it again on the Requirements page.",
            409,
        ) from None
    if not token or not ctx.project.repo_url:
        raise AppError("REPO_NOT_SET", MISSING, 409)
    return token


async def run(ctx: AgentContext) -> AgentOutcome:
    token = await _token(ctx)
    refs = {s.kind: s for s in ctx.run.inputs}
    impl = await artifacts.get_revision(
        ctx.db, ctx.project, "implementation", refs["implementation"].revision
    )
    tests = await artifacts.get_revision(ctx.db, ctx.project, "testing", refs["testing"].revision)
    code_ref = next((s for s in impl.sources if s.kind == "code"), None)
    code = code_ref and await artifacts.get_revision(ctx.db, ctx.project, "code", code_ref.revision)
    slug = (code and code.content.get("project_slug")) or "app"
    branch = f"ampera/{slug}-r{impl.revision}"
    files = [CodeFile.model_validate(f) for f in impl.content["files"]]
    paths = [f.path for f in files]
    covered = sorted({r["requirement_id"] for r in tests.content["results"] if r["passed"]})

    msg, usage = await call_structured(
        ctx,
        schema_model=DeployMessage,
        schema_name="deploy_message",
        system=prompts.SYSTEM,
        user=prompts.message_prompt(impl.content.get("summary", ""), paths, covered),
        validate=lambda m: [],
        max_output_tokens=800,
    )
    await subtask(ctx, "commit", "Commit", "running", f"Committing {len(files)} files")
    committed = False

    async def on_commit() -> None:
        nonlocal committed
        committed = True
        await subtask(ctx, "commit", "Commit", "passed", "Committed")
        await subtask(
            ctx, "push", "Push", "running", f"Pushing {len(files)} files to branch {branch}"
        )

    try:
        remote = _remote(ctx.project.repo_url)
        sha = await git.push(files, remote, token, branch, msg.message, on_commit)
    except Exception:
        sid, label = ("push", "Push") if committed else ("commit", "Commit")
        await subtask(ctx, sid, label, "failed", f"{label} failed")
        raise
    await subtask(ctx, "push", "Push", "passed", f"Pushed to {branch}")
    content = DeployContent(
        repo_url=ctx.project.repo_url, branch=branch, commit_sha=sha, files=paths,
        pushed_at=now(), commit_message=msg.message,
    )  # fmt: skip
    by_kind = {s.kind: s for s in [*impl.sources, *tests.sources, *ctx.run.inputs]}
    summary = f"Pushed {len(files)} files to {branch} ({sha[:7]})"
    rev = await artifacts.add_revision(
        ctx.db,
        ctx.project,
        "deploy",
        content.model_dump(mode="json"),
        sources=list(by_kind.values()),  # all transitive upstream kinds, for the staleness cascade
        origin="agent",
        summary=summary,
        created_by=None,
        run_id=ctx.run.id,
    )
    output = {"artifact_id": str(rev.artifact_id), "revision": rev.revision}
    await ctx.emit("step", summary, output)
    return AgentOutcome(output=output, summary=summary, usage=usage)
