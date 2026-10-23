"""Agent contract: typed context in, structured outcome out. Agents never call each other."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.db import Db
from app.llm.base import LLMProvider
from app.modules.orchestration.models import AgentRun, EventType
from app.modules.projects.models import Project


@dataclass
class AgentContext:
    db: Db
    project: Project
    run: AgentRun
    provider: LLMProvider
    emit: Callable[[EventType, str, dict[str, Any] | None], Awaitable[None]]
    cancelled: Callable[[], bool]


@dataclass
class AgentOutcome:
    output: dict[str, Any]
    summary: str
    usage: dict[str, int]


class OutputInvalid(Exception):
    """The model's output still failed validation after the allowed repairs."""

    def __init__(self, issues: list[dict[str, Any]]) -> None:
        first = issues[0]["message"] if issues else "invalid output"
        super().__init__(first)
        self.issues = issues


AgentFn = Callable[[AgentContext], Awaitable[AgentOutcome]]
_REGISTRY: dict[str, AgentFn] = {}


def register_agent(name: str, fn: AgentFn) -> None:
    _REGISTRY[name] = fn


def get_agent(name: str) -> AgentFn:
    return _REGISTRY[name]
