"""Change-set operations on a diagram. Applied in memory, never directly to stored revisions."""

import copy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.modules.diagrams.schema import ArchitectureContent, Decision, Edge, Group, Node


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddNode(_Op):
    op: Literal["add_node"]
    node: Node


class UpdateNode(_Op):
    op: Literal["update_node"]
    id: str
    fields: dict[str, Any]


class RemoveNode(_Op):
    op: Literal["remove_node"]
    id: str


class AddEdge(_Op):
    op: Literal["add_edge"]
    edge: Edge


class UpdateEdge(_Op):
    op: Literal["update_edge"]
    id: str
    fields: dict[str, Any]


class RemoveEdge(_Op):
    op: Literal["remove_edge"]
    id: str


class AddGroup(_Op):
    op: Literal["add_group"]
    group: Group


class AddDecision(_Op):
    op: Literal["add_decision"]
    decision: Decision


Op = Annotated[
    AddNode | UpdateNode | RemoveNode | AddEdge | UpdateEdge | RemoveEdge | AddGroup | AddDecision,
    Field(discriminator="op"),
]
_ops_adapter = TypeAdapter(list[Op])


class OpError(Exception):
    def __init__(self, index: int, message: str, ref: str | None = None) -> None:
        super().__init__(f"Operation {index + 1}: {message}" if index >= 0 else message)
        self.index = index
        self.message = message
        self.ref = ref  # raw id, for LLM repair feedback only


def parse_ops(raw: list[dict[str, Any]]) -> list[Op]:
    try:
        return _ops_adapter.validate_python(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = first.get("loc", [])
        index = loc[0] if loc and isinstance(loc[0], int) else -1
        raise OpError(index, first.get("msg", "invalid operation")) from exc


def _find(items: list[dict], item_id: str) -> int | None:
    return next((i for i, it in enumerate(items) if it["id"] == item_id), None)


def _label(d: dict, coll: str, item_id: str) -> str:
    item = d[coll][_find(d[coll], item_id)]
    return item.get("label") or "a connection"


def apply_ops(base: ArchitectureContent, ops: list[Op]) -> ArchitectureContent:
    d = copy.deepcopy(base.model_dump(mode="json"))
    for i, op in enumerate(ops):
        if isinstance(op, AddNode):
            if _find(d["nodes"], op.node.id) is not None:
                raise OpError(i, f"{op.node.label} is already in the diagram", op.node.id)
            d["nodes"].append(op.node.model_dump(mode="json"))
        elif isinstance(op, UpdateNode | UpdateEdge):
            coll, model = ("nodes", Node) if isinstance(op, UpdateNode) else ("edges", Edge)
            idx = _find(d[coll], op.id)
            if idx is None:
                raise OpError(
                    i,
                    f"That {'component' if coll == 'nodes' else 'connection'} isn't in the diagram",
                    op.id,
                )
            if "id" in op.fields:
                raise OpError(i, "IDs can't be changed; remove and add instead")
            merged = {**d[coll][idx], **op.fields}
            try:
                d[coll][idx] = model.model_validate(merged).model_dump(mode="json")
            except ValidationError as exc:
                raise OpError(
                    i, f"Invalid change to {_label(d, coll, op.id)}: {exc.errors()[0]['msg']}"
                ) from exc
        elif isinstance(op, RemoveNode):
            if _find(d["nodes"], op.id) is None:
                raise OpError(i, "That component isn't in the diagram", op.id)
            d["nodes"] = [n for n in d["nodes"] if n["id"] != op.id]
            d["edges"] = [e for e in d["edges"] if op.id not in (e["source"], e["target"])]
        elif isinstance(op, AddEdge):
            if _find(d["edges"], op.edge.id) is not None:
                raise OpError(i, "That connection is already in the diagram", op.edge.id)
            for end in (op.edge.source, op.edge.target):
                if _find(d["nodes"], end) is None:
                    raise OpError(
                        i, "The new connection points to a component that isn't in the diagram", end
                    )
            d["edges"].append(op.edge.model_dump(mode="json"))
        elif isinstance(op, RemoveEdge):
            if _find(d["edges"], op.id) is None:
                raise OpError(i, "That connection isn't in the diagram", op.id)
            d["edges"] = [e for e in d["edges"] if e["id"] != op.id]
        elif isinstance(op, AddGroup):
            if _find(d["groups"], op.group.id) is not None:
                raise OpError(i, f"Group {op.group.label} already exists")
            d["groups"].append(op.group.model_dump(mode="json"))
        elif isinstance(op, AddDecision):
            if _find(d["decisions"], op.decision.id) is not None:
                raise OpError(i, f"Decision {op.decision.id} {op.decision.title} already exists")
            d["decisions"].append(op.decision.model_dump(mode="json"))
    try:
        return ArchitectureContent.model_validate(d)
    except ValidationError as exc:
        raise OpError(-1, f"Result is invalid: {exc.errors()[0]['msg']}") from exc
