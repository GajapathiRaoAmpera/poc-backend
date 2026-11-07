from app.modules.agents.requirements.service import save_answers
from app.modules.agents.requirements.validate import REPO_QUESTION
from app.modules.artifacts import service as artifacts
from tests.agents.helpers import good_requirements, run_stage, setup_project


def _repo_qs(content):
    return [q for q in content["open_questions"] if q["question"] == REPO_QUESTION]


async def test_requirements_adds_repo_question_once(db):
    owner, project = await setup_project(db)
    await run_stage(db, project, owner, "requirements", lambda *a: good_requirements())
    rev = await artifacts.get_revision(db, project, "requirements", 1)
    [q] = _repo_qs(rev.content)
    assert q["blocking"] is False and q["id"] == "Q-002"
    # re-draft from the previous revision: the model returns it again, no duplicate
    await run_stage(
        db,
        project,
        owner,
        "requirements",
        lambda *a: {**good_requirements(), "content": rev.content},
    )
    rev2 = await artifacts.get_revision(db, project, "requirements", 2)
    assert len(_repo_qs(rev2.content)) == 1


async def test_repo_answer_prefills_url(db):
    owner, project = await setup_project(db)
    await run_stage(db, project, owner, "requirements", lambda *a: good_requirements())
    rev = await artifacts.get_revision(db, project, "requirements", 1)
    qid = _repo_qs(rev.content)[0]["id"]
    url = "https://github.com/acme/app.git"
    await save_answers(db, project, owner, {qid: url}, base_revision=1)
    assert (await db.projects.find_one({"_id": project.id}))["repo_url"] == url
