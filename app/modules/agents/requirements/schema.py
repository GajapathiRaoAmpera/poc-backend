from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Persona(_M):
    name: Short
    description: Text


class Requirement(_M):
    id: Annotated[str, StringConstraints(pattern=r"^(FR|NFR)-\d{3}$")]
    type: Literal["functional", "non_functional"]
    title: Short
    description: Text
    priority: Literal["MUST", "SHOULD", "COULD", "WONT"]
    acceptance_criteria: Annotated[
        list[Annotated[str, StringConstraints(max_length=500)]], Field(max_length=10)
    ] = []
    source: Literal["stated", "inferred", "assumption"]


class OpenQuestion(_M):
    id: Annotated[str, StringConstraints(pattern=r"^Q-\d{3}$")]
    question: Short
    why_it_matters: Text
    blocking: bool
    answer: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None


class Risk(_M):
    description: Text
    likelihood: Literal["low", "medium", "high"]
    mitigation: Text


class RequirementsContent(_M):
    summary: Text
    goals: Annotated[list[Short], Field(max_length=30)] = []
    personas: Annotated[list[Persona], Field(max_length=10)] = []
    requirements: Annotated[list[Requirement], Field(max_length=150)]
    assumptions: Annotated[list[Text], Field(max_length=50)] = []
    open_questions: Annotated[list[OpenQuestion], Field(max_length=30)] = []
    risks: Annotated[list[Risk], Field(max_length=30)] = []
    dependencies: Annotated[list[Short], Field(max_length=30)] = []


class RequirementsAgentOutput(_M):
    content: RequirementsContent
    summary: Annotated[str, StringConstraints(max_length=500)]
