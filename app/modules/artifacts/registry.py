"""Per-kind content model and validation, registered by each agent/domain module."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.modules.artifacts.models import SourceRef, ValidationIssue


@dataclass
class ValidationContext:
    """Upstream data a validator may need, e.g. the requirement IDs an architecture may cite."""

    sources: dict[str, Any] = field(default_factory=dict)


ContextLoader = Callable[[Any, Any, list[SourceRef]], Awaitable[ValidationContext]]


async def _no_context(db, project, sources) -> ValidationContext:
    return ValidationContext()


@dataclass
class KindSpec:
    kind: str
    schema_version: str
    model: type[BaseModel]
    # Errors here block saving; warnings are stored.
    validate_save: Callable[[Any, ValidationContext], list[ValidationIssue]]
    # Errors here are stored and block approval (not saving).
    validate_approval: Callable[[Any, ValidationContext], list[ValidationIssue]]
    load_context: ContextLoader = _no_context


_KINDS: dict[str, KindSpec] = {}

# Upstream → downstream. "specification" is the project spec (not an artifact).
DEPENDENTS: dict[str, list[str]] = {
    "specification": ["requirements"],
    "requirements": ["architecture", "plan", "design", "implementation", "testing", "deploy"],
    "architecture": ["plan", "budget", "code", "design", "implementation", "testing", "deploy"],
    "plan": ["budget", "code", "implementation", "testing", "deploy"],
    "code": ["implementation", "testing", "deploy"],
    "design": ["implementation", "testing", "deploy"],
    "implementation": ["testing", "deploy"],
    "testing": ["deploy"],
}


def register(spec: KindSpec) -> None:
    _KINDS[spec.kind] = spec


def unregister(kind: str) -> None:
    _KINDS.pop(kind, None)


def get(kind: str) -> KindSpec:
    try:
        return _KINDS[kind]
    except KeyError as exc:
        raise KeyError(f"Unknown artifact kind: {kind}") from exc


def kinds() -> list[str]:
    return list(_KINDS)
