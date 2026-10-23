import copy

from app.core.db import Db
from app.core.errors import NotFound, ValidationFailed
from app.modules.artifacts import service as artifacts
from app.modules.artifacts.models import Revision
from app.modules.auth.models import User
from app.modules.projects.models import Project

MAX_ANSWER = 2000


async def save_answers(
    db: Db, project: Project, user: User, answers: dict[str, str | None], base_revision: int
) -> Revision:
    """Answers become a new revision; existing revisions are never modified."""
    latest = await artifacts.latest_revision(db, project, "requirements")
    if latest is None:
        raise NotFound("There are no requirements yet")
    content = copy.deepcopy(latest.content)
    known = {q["id"]: q for q in content.get("open_questions", [])}
    unknown = sorted(set(answers) - set(known))
    if unknown:
        raise ValidationFailed(f"Unknown question {unknown[0]}", {"unknown": unknown})
    for qid, text in answers.items():
        text = (text or "").strip()[:MAX_ANSWER]
        known[qid]["answer"] = text or None
    n = sum(1 for t in answers.values() if (t or "").strip())
    return await artifacts.add_revision(
        db,
        project,
        "requirements",
        content,
        sources=latest.sources,
        origin="user",
        summary=f"Answered {n} question{'s' if n != 1 else ''}",
        created_by=user.id,
        base_revision=base_revision,
    )
