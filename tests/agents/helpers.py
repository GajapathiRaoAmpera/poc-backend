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


def good_architecture(**over):
    def node(id_, type_, label, reqs=()):
        return {
            "id": id_,
            "type": type_,
            "label": label,
            "description": "",
            "technology": "",
            "responsibilities": [],
            "config": {},
            "requirement_ids": list(reqs),
            "position": {"x": 0, "y": 0},
            "group": None,
        }

    def edge(id_, s, t, label=""):
        return {
            "id": id_,
            "source": s,
            "target": t,
            "label": label,
            "protocol": "HTTPS",
            "data": None,
            "requirement_ids": [],
        }

    content = {
        "schema_version": "1.0",
        "diagram_type": "system",
        "nodes": [
            node("n_web", "frontend", "Web app", ["FR-001"]),
            node("n_api", "backend", "API", ["FR-001"]),
            node("n_worker", "worker", "Worker"),
            node("n_db", "database", "Database"),
            node("n_auth", "auth", "Identity provider"),
            node("n_mon", "monitoring", "Monitoring"),
        ],
        "edges": [
            edge("e_web_api", "n_web", "n_api", "REST"),
            edge("e_api_worker", "n_api", "n_worker", "jobs"),
            edge("e_api_db", "n_api", "n_db", "SQL"),
            edge("e_web_auth", "n_web", "n_auth", "OIDC"),
            edge("e_api_mon", "n_api", "n_mon", "metrics"),
        ],
        "groups": [],
        "decisions": [
            {
                "id": "ADR-001",
                "title": "Accessibility",
                "decision": "Use accessible components",
                "alternatives": [],
                "rationale": "WCAG",
                "requirement_ids": ["NFR-001"],
            }
        ],
        "notes": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    content.update(over)
    return {"content": content, "summary": "API-centric architecture with async worker."}


async def approved_requirements(db, project, owner):
    from app.modules.agents.requirements.service import save_answers
    from app.modules.artifacts import service as artifacts

    await run_stage(db, project, owner, "requirements", lambda *a: good_requirements())
    await save_answers(db, project, owner, {"Q-001": "Azure AD"}, base_revision=1)
    await artifacts.approve(db, project, "requirements", 2, owner, None)
