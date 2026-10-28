import re
from collections import Counter

from app.modules.artifacts.models import ValidationIssue
from app.modules.diagrams.schema import ArchitectureContent

AUTH_HINT = re.compile(
    r"\b(log ?in|sign ?in|sign ?up|authenticat\w*|password|user accounts?|sso)\b", re.I
)


def _e(code, message, path=None, refs=()):
    return ValidationIssue(severity="error", code=code, message=message, path=path, refs=list(refs))


def _w(code, message, path=None, refs=()):
    return ValidationIssue(
        severity="warning", code=code, message=message, path=path, refs=list(refs)
    )


def _name(c: ArchitectureContent, node_id: str) -> str:
    return next((n.label for n in c.nodes if n.id == node_id), "a removed component")


def _edge(c: ArchitectureContent, e) -> str:
    return f"{_name(c, e.source)} → {_name(c, e.target)}"


def _item(c: ArchitectureContent, kind: str, item) -> str:
    if kind == "nodes":
        return item.label
    if kind == "edges":
        return f"Connection {_edge(c, item)}"
    return f"{item.id} {item.title}"


def validate_structure(c: ArchitectureContent) -> list[ValidationIssue]:
    """Save-blocking: the diagram must be internally consistent."""
    out: list[ValidationIssue] = []
    for name, items in (
        ("nodes", c.nodes),
        ("edges", c.edges),
        ("groups", c.groups),
        ("decisions", c.decisions),
        ("notes", c.notes),
    ):
        for item_id, n in Counter(i.id for i in items).items():
            if n > 1:
                out.append(
                    _e(
                        "DUPLICATE_ID",
                        f"Two {name} have the same internal ID; remove one of them",
                        name,
                        [item_id],
                    )
                )
    node_ids = {n.id for n in c.nodes}
    group_ids = {g.id for g in c.groups}
    for i, e in enumerate(c.edges):
        for end in (e.source, e.target):
            if end not in node_ids:
                out.append(
                    _e(
                        "UNKNOWN_NODE",
                        f"A connection from {_name(c, e.source if end == e.target else e.target)} "
                        "points to a component that no longer exists",
                        f"edges.{i}",
                        [e.id, end],
                    )
                )
        if e.source == e.target:
            out.append(
                _e(
                    "SELF_LOOP",
                    f"{_name(c, e.source)} is connected to itself",
                    f"edges.{i}",
                    [e.id, e.source],
                )
            )
    for i, n in enumerate(c.nodes):
        if n.group is not None and n.group not in group_ids:
            out.append(
                _e(
                    "UNKNOWN_GROUP",
                    f"{n.label} is in a group that no longer exists",
                    f"nodes.{i}.group",
                    [n.id],
                )
            )
    return out


def validate_semantics(
    c: ArchitectureContent,
    requirement_ids: set[str],
    must_ids: set[str],
    requirement_texts: list[str],
) -> list[ValidationIssue]:
    """Errors here block approval (not saving); warnings are advice."""
    out: list[ValidationIssue] = []
    cited: set[str] = set()
    for kind, items in (("nodes", c.nodes), ("edges", c.edges), ("decisions", c.decisions)):
        for i, item in enumerate(items):
            for rid in item.requirement_ids:
                cited.add(rid)
                if rid not in requirement_ids:
                    out.append(
                        _e(
                            "UNKNOWN_REQUIREMENT",
                            f"{_item(c, kind, item)} references {rid}, which isn't in the "
                            "approved requirements",
                            f"{kind}.{i}.requirement_ids",
                            [item.id],
                        )
                    )
    for rid in sorted(must_ids - cited):
        out.append(
            _e(
                "UNCOVERED_REQUIREMENT",
                f"MUST requirement {rid} isn't linked to any component, connection or decision",
            )
        )

    connected = {e.source for e in c.edges} | {e.target for e in c.edges}
    for i, n in enumerate(c.nodes):
        if n.id not in connected and len(c.nodes) > 1:
            out.append(_w("ISOLATED_NODE", f"{n.label} has no connections", f"nodes.{i}", [n.id]))
    types = {n.type for n in c.nodes}
    if "auth" not in types and any(AUTH_HINT.search(t) for t in requirement_texts):
        out.append(
            _w(
                "NO_AUTH",
                "Requirements mention users signing in, but there is no authentication component",
            )
        )
    if c.nodes and "monitoring" not in types:
        out.append(_w("NO_MONITORING", "No monitoring/observability component"))
    pairs = Counter((e.source, e.target, e.label) for e in c.edges)
    for (s, t, label), n in pairs.items():
        if n > 1:
            out.append(
                _w("DUPLICATE_EDGE", f"{n} identical connections {s} → {t} ({label or 'no label'})")
            )
    return out
