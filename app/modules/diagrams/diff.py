from typing import Any

from pydantic import BaseModel

from app.modules.diagrams.schema import ArchitectureContent


class Modified(BaseModel):
    id: str
    fields: dict[str, dict[str, Any]]


class Section(BaseModel):
    added: list[str] = []
    removed: list[str] = []
    modified: list[Modified] = []


class DiagramDiff(BaseModel):
    nodes: Section
    edges: Section
    groups: Section
    decisions: Section
    notes: Section
    # id -> human name, so clients never show raw ids like n_redis
    labels: dict[str, str] = {}


class Impact(BaseModel):
    added: list[str]
    removed: list[str]
    modified: list[str]
    requirement_ids: list[str]
    labels: dict[str, str] = {}


def labels(*contents: ArchitectureContent) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in contents:
        out.update({n.id: n.label for n in c.nodes})
        out.update({g.id: g.label for g in c.groups})
        out.update({d.id: f"{d.id} {d.title}".strip() for d in c.decisions})
        out.update({n.id: f"Note “{n.text[:40]}”" for n in c.notes})
    for c in contents:
        for e in c.edges:
            gone = "a removed component"
            ends = f"{out.get(e.source, gone)} → {out.get(e.target, gone)}"
            out[e.id] = f"{ends} ({e.label})" if e.label else ends
    return out


def _section(a: list, b: list, ignore: set[str]) -> Section:
    a_by = {x.id: x.model_dump(mode="json") for x in a}
    b_by = {x.id: x.model_dump(mode="json") for x in b}
    s = Section(
        added=[x.id for x in b if x.id not in a_by],
        removed=[x.id for x in a if x.id not in b_by],
    )
    for x in b:
        if x.id in a_by:
            old, new = a_by[x.id], b_by[x.id]
            fields = {
                k: {"from": old.get(k), "to": new.get(k)}
                for k in sorted(set(old) | set(new))
                if k not in ignore and old.get(k) != new.get(k)
            }
            if fields:
                s.modified.append(Modified(id=x.id, fields=fields))
    return s


def diff(
    a: ArchitectureContent, b: ArchitectureContent, include_layout: bool = False
) -> DiagramDiff:
    layout = set() if include_layout else {"position"}
    return DiagramDiff(
        nodes=_section(a.nodes, b.nodes, layout),
        edges=_section(a.edges, b.edges, set()),
        groups=_section(a.groups, b.groups, set()),
        decisions=_section(a.decisions, b.decisions, set()),
        notes=_section(a.notes, b.notes, layout),
        labels=labels(a, b),
    )


def impact(base: ArchitectureContent, result: ArchitectureContent) -> Impact:
    d = diff(base, result)
    sections = (d.nodes, d.edges, d.groups, d.decisions, d.notes)
    added = [i for s in sections for i in s.added]
    removed = [i for s in sections for i in s.removed]
    modified = [m.id for s in sections for m in s.modified]
    reqs: set[str] = set()
    for collection in ("nodes", "edges", "decisions"):
        for item in getattr(base, collection):
            if item.id in removed or item.id in modified:
                reqs.update(item.requirement_ids)
        for item in getattr(result, collection):
            if item.id in added or item.id in modified:
                reqs.update(item.requirement_ids)
    return Impact(
        added=added,
        removed=removed,
        modified=modified,
        requirement_ids=sorted(reqs),
        labels=labels(base, result),
    )
