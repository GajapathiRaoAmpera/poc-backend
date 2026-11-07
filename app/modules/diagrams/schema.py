"""Canonical, versioned architecture diagram (schema 1.0). Exports are derived from this."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

NodeType = Literal[
    "client",
    "frontend",
    "backend",
    "service",
    "worker",
    "database",
    "cache",
    "queue",
    "storage",
    "external_api",
    "auth",
    "gateway",
    "monitoring",
    "cdn",
    "deployment",
]
NODE_TYPES: tuple[str, ...] = NodeType.__args__  # type: ignore[attr-defined]

ReqId = Annotated[str, StringConstraints(max_length=12)]


def _s(n: int):
    return Annotated[str, StringConstraints(strip_whitespace=True, max_length=n)]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Position(_M):
    x: float = 0
    y: float = 0


class Node(_M):
    id: Annotated[str, StringConstraints(pattern=r"^n_[a-z0-9_]{1,40}$")]
    type: NodeType
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    description: _s(500) = ""
    technology: _s(80) = ""
    responsibilities: Annotated[list[_s(200)], Field(max_length=10)] = []
    config: dict[str, _s(200)] = {}
    requirement_ids: Annotated[list[ReqId], Field(max_length=50)] = []
    position: Position = Position()
    group: str | None = None

    @field_validator("config")
    @classmethod
    def _config_size(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 20:
            raise ValueError("at most 20 config entries")
        if any(len(k) > 60 for k in v):
            raise ValueError("config keys must be at most 60 characters")
        return v


class Edge(_M):
    id: Annotated[str, StringConstraints(pattern=r"^e_[a-z0-9_]{1,40}$")]
    source: str
    target: str
    label: _s(80) = ""
    protocol: _s(40) | None = None
    data: _s(200) | None = None
    requirement_ids: Annotated[list[ReqId], Field(max_length=50)] = []


class Group(_M):
    id: Annotated[str, StringConstraints(pattern=r"^g_[a-z0-9_]{1,40}$")]
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class Decision(_M):
    id: Annotated[str, StringConstraints(pattern=r"^ADR-\d{3}$")]
    title: _s(200)
    decision: _s(2000)
    alternatives: Annotated[list[_s(300)], Field(max_length=10)] = []
    rationale: _s(2000) = ""
    requirement_ids: Annotated[list[ReqId], Field(max_length=50)] = []


class Note(_M):
    id: Annotated[str, StringConstraints(pattern=r"^note_[a-z0-9_]{1,40}$")]
    text: _s(500)
    position: Position = Position()


class Viewport(_M):
    x: float = 0
    y: float = 0
    zoom: float = 1


FieldType = Literal[
    "uuid", "string", "text", "int", "float", "bool", "datetime", "date", "json", "enum", "decimal"
]


class DataField(_M):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    type: FieldType
    pk: bool = False
    nullable: bool = True
    unique: bool = False
    ref: _s(100) | None = None  # "ent_x.field"


class Entity(_M):
    id: Annotated[str, StringConstraints(pattern=r"^ent_[a-z0-9_]{1,40}$")]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    database_node_id: str
    description: _s(500) = ""
    requirement_ids: Annotated[list[ReqId], Field(max_length=50)] = []
    fields: Annotated[list[DataField], Field(max_length=40)] = []


class Relation(_M):
    id: Annotated[str, StringConstraints(pattern=r"^rel_[a-z0-9_]{1,40}$")]
    from_entity: str
    to_entity: str
    kind: Literal["1-1", "1-n", "n-n"]
    label: _s(80) = ""


class DataModel(_M):
    entities: Annotated[list[Entity], Field(max_length=40)] = []
    relations: Annotated[list[Relation], Field(max_length=100)] = []


class ArchitectureContent(_M):
    schema_version: Literal["1.0"] = "1.0"
    diagram_type: Literal["system"] = "system"
    nodes: Annotated[list[Node], Field(max_length=200)] = []
    edges: Annotated[list[Edge], Field(max_length=400)] = []
    groups: Annotated[list[Group], Field(max_length=30)] = []
    decisions: Annotated[list[Decision], Field(max_length=50)] = []
    notes: Annotated[list[Note], Field(max_length=50)] = []
    viewport: Viewport = Viewport()
    data_model: DataModel = DataModel()
