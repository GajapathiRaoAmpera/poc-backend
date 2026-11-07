from app.modules.agents.requirements.schema import RequirementsContent
from app.modules.artifacts.models import ValidationIssue

MAX_NEW_QUESTIONS = 7
REPO_QUESTION = "Which git repository should the finished code be pushed to?"


def ensure_repo_question(content: dict) -> dict:
    """Add the non-blocking repo question once; it only gates the deploy stage."""
    qs = content.setdefault("open_questions", [])
    if not any(q["question"] == REPO_QUESTION for q in qs):
        n = max([int(q["id"][2:]) for q in qs] + [0]) + 1
        qs.append(
            {
                "id": f"Q-{n:03d}",
                "question": REPO_QUESTION,
                "why_it_matters": "The Deployment agent pushes the finished code there.",
                "blocking": False,
                "answer": None,
            }
        )
    return content


def _err(code: str, message: str, path: str | None = None) -> ValidationIssue:
    return ValidationIssue(severity="error", code=code, message=message, path=path)


def structure_issues(c: RequirementsContent, ctx=None) -> list[ValidationIssue]:
    """Save-blocking problems."""
    out, seen = [], set()
    for i, r in enumerate(c.requirements):
        if r.id in seen:
            out.append(
                _err("DUPLICATE_ID", f"Duplicate requirement ID {r.id}", f"requirements.{i}.id")
            )
        seen.add(r.id)
        expected = "functional" if r.id.startswith("FR-") else "non_functional"
        if r.type != expected:
            out.append(
                _err(
                    "ID_TYPE_MISMATCH",
                    f"{r.id} must have type {expected} (FR = functional, NFR = non-functional)",
                    f"requirements.{i}.type",
                )
            )
    qseen = set()
    for i, q in enumerate(c.open_questions):
        if q.id in qseen:
            out.append(
                _err("DUPLICATE_ID", f"Duplicate question ID {q.id}", f"open_questions.{i}.id")
            )
        qseen.add(q.id)
    return out


def must_criteria_issues(c: RequirementsContent) -> list[ValidationIssue]:
    return [
        _err(
            "MISSING_ACCEPTANCE_CRITERIA",
            f"MUST requirement {r.id} needs at least one acceptance criterion",
            f"requirements.{i}.acceptance_criteria",
        )
        for i, r in enumerate(c.requirements)
        if r.priority == "MUST" and not r.acceptance_criteria
    ]


def approval_issues(c: RequirementsContent, ctx=None) -> list[ValidationIssue]:
    out = must_criteria_issues(c)
    for i, q in enumerate(c.open_questions):
        if q.blocking and not q.answer:
            out.append(
                _err(
                    "UNANSWERED_QUESTION",
                    f"Answer blocking question {q.id} first: {q.question}",
                    f"open_questions.{i}.answer",
                )
            )
    return out


def has_unanswered_blocking(content: dict) -> bool:
    return any(q.get("blocking") and not q.get("answer") for q in content.get("open_questions", []))


def agent_issues(
    c: RequirementsContent, retired_ids: set[str], previous_question_ids: set[str]
) -> list[ValidationIssue]:
    out = structure_issues(c) + must_criteria_issues(c)
    for i, r in enumerate(c.requirements):
        if r.id in retired_ids:
            out.append(
                _err(
                    "RETIRED_ID",
                    f"{r.id} was removed earlier and must not be reused; use a new ID",
                    f"requirements.{i}.id",
                )
            )
    new_q = [q for q in c.open_questions if q.id not in previous_question_ids]
    if len(new_q) > MAX_NEW_QUESTIONS:
        out.append(
            _err(
                "TOO_MANY_QUESTIONS",
                f"Ask at most {MAX_NEW_QUESTIONS} new questions (got {len(new_q)}); "
                "keep only the ones that materially change scope or architecture",
            )
        )
    return out
