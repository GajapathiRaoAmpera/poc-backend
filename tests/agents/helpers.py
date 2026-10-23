import copy

from app.llm.mock import MockProvider
from app.modules.orchestration.service import start_stage
from app.modules.projects.models import Project, ProjectSpecification
from app.worker.runner import Worker
from tests.factories import make_user

SPEC = {
    "basics": {"name": "Client portal", "description": "Self-service onboarding portal"},
    "problem": {"business_problem": "Onboarding takes 3 weeks", "goals": ["Onboard in 2 days"]},
    "requirements": {"functional": ["Clients upload documents"], "non_functional": ["WCAG 2.2 AA"]},
}


def good_requirements(**over):
    content = {
        "summary": "A client onboarding portal.",
        "goals": ["Onboard in 2 days"],
        "personas": [{"name": "Client admin", "description": "Uploads documents"}],
        "requirements": [
            {
                "id": "FR-001",
                "type": "functional",
                "title": "Upload documents",
                "description": "Clients upload onboarding documents.",
                "priority": "MUST",
                "acceptance_criteria": ["PDF up to 20 MB can be uploaded"],
                "source": "stated",
            },
            {
                "id": "NFR-001",
                "type": "non_functional",
                "title": "Accessibility",
                "description": "Meets WCAG 2.2 AA.",
                "priority": "MUST",
                "acceptance_criteria": ["No serious axe violations"],
                "source": "stated",
            },
        ],
        "assumptions": ["Single region"],
        "open_questions": [
            {
                "id": "Q-001",
                "question": "Which identity provider?",
                "why_it_matters": "Drives auth design",
                "blocking": True,
                "answer": None,
            },
        ],
        "risks": [
            {"description": "Large files", "likelihood": "medium", "mitigation": "Chunked upload"}
        ],
        "dependencies": ["Document storage"],
    }
    content.update(over)
    return {"content": content, "summary": "Drafted 2 requirements and 1 open question."}


async def setup_project(db, email="owner@x.com"):
    owner = await make_user(db, email=email)
    project = Project(owner_id=owner.id, name="Client portal")
    await db.projects.insert_one(project.to_doc())
    await db.project_specifications.insert_one(
        ProjectSpecification(project_id=project.id, revision=1, data=copy.deepcopy(SPEC)).to_doc()
    )
    return owner, project


async def run_stage(db, project, owner, stage, script, mode="generate", **kw):
    provider = MockProvider(script=script)
    run, _ = await start_stage(db, project, stage, mode, owner, **kw)
    await Worker(db, provider_factory=lambda: provider, heartbeat_s=0.05, poll_s=0.01).run_once()
    return run, provider, await db.agent_runs.find_one({"_id": run.id})
