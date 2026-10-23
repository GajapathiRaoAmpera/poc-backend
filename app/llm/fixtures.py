"""Default mock outputs, filled in as agents are added (keyed by schema name)."""

from typing import Any

_DEFAULTS: dict[str, Any] = {}


def register_default(schema_name: str, fn) -> None:
    _DEFAULTS[schema_name] = fn


def default_output(schema_name: str, system: str, user: str) -> dict[str, Any]:
    fn = _DEFAULTS.get(schema_name)
    if fn is None:
        return {}
    return fn(system, user)
