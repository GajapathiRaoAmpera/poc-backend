"""Delivery-graph state (checkpointed) and runtime dependencies (never checkpointed)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypedDict

from app.core.db import Db
from app.llm.base import LLMProvider


class DeliveryState(TypedDict, total=False):
    project_id: str
    plan_version: int | None
    # Routing decision taken by the orchestrator node (routing.Decision as a dict).
    decision: dict[str, Any] | None
    # A gate a stage node hit before running (quota, busy stage); the orchestrator routes to it.
    forced_gate: dict[str, Any] | None
    loops: dict[str, int]
    last_event: dict[str, Any] | None
    # Why the graph was (re)entered: "intake", "user_message", "approved:<kind>", "answers", …
    trigger: str | None


@dataclass
class GraphDeps:
    db: Db
    provider_factory: Callable[[], LLMProvider]
    cancelled: Callable[[], bool] = field(default=lambda: False)
