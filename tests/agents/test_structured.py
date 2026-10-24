import json

from pydantic import BaseModel

from app.llm.mock import MockProvider
from app.modules.agents.base import AgentContext
from app.modules.agents.structured import call_structured
from app.modules.artifacts.models import ValidationIssue
from app.modules.orchestration.models import AgentRun
from tests.agents.helpers import setup_project


class Out(BaseModel):
    text: str


class LimitedMock(MockProvider):
    max_input_tokens = 3000


async def test_repair_prompt_fits_the_providers_input_limit(db):
    _, project = await setup_project(db)
    outputs = iter([{"text": "x" * 40000}, {"text": "ok"}])
    provider = LimitedMock(script=lambda *a: next(outputs))
    run = AgentRun(
        project_id=project.id,
        owner_id=project.owner_id,
        stage="requirements",
        agent="a",
        created_by=project.owner_id,
    )

    async def emit(*a, **k):
        return None

    ctx = AgentContext(db, project, run, provider, emit, lambda: False)
    out, _ = await call_structured(
        ctx,
        schema_model=Out,
        schema_name="out",
        system="sys",
        user="u" * 2000,
        validate=lambda o: (
            []
            if o.text == "ok"
            else [ValidationIssue(severity="error", code="BAD", message="too long")]
        ),
    )
    assert out.text == "ok"
    repair = provider.calls[1]
    size = len(repair["system"]) + len(repair["user"]) + len(json.dumps(Out.model_json_schema()))
    assert size <= 3000 * 4.5
    assert "too long" in repair["user"]
