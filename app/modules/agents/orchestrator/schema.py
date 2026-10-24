"""Structured output of the Project Orchestrator Agent (``plan/1.0``)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.projects.spec_schema import ProjectSpecification

Stage = Literal["requirements", "architecture", "plan", "budget", "code"]


def _text(n: int):
    return Annotated[str, StringConstraints(strip_whitespace=True, max_length=n)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StagePlan(_Model):
    stage: Stage
    include: bool
    rationale: _text(300)
    inputs: list[Stage] = []
    approval_required: bool


class MissingInfo(_Model):
    id: Annotated[str, StringConstraints(pattern=r"^I-\d{3}$")]
    question: _text(500)
    why_it_matters: _text(500)
    blocking: bool


class PlanContent(_Model):
    project_name: _text(160)
    objective_summary: _text(1000)
    project_type: Literal[
        "web_app", "mobile_app", "data_pipeline", "integration", "internal_tool", "other"
    ]
    # Same shape as the wizard's specification so the Requirements Agent reads it unchanged.
    brief: ProjectSpecification
    stages: list[StagePlan] = Field(max_length=10)
    missing_info: list[MissingInfo] = Field(default=[], max_length=7)
    next_action: Literal["run_stage", "ask_user", "wait_approval", "done"]
    next_stage: Stage | None = None
    decision_summary: _text(600)


class OrchestratorOutput(_Model):
    content: PlanContent
    summary: _text(600)
