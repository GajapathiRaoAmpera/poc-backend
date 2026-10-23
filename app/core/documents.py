import uuid
from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field


def now() -> datetime:
    # MongoDB stores milliseconds; truncate so values round-trip exactly.
    t = datetime.now(UTC)
    return t.replace(microsecond=t.microsecond // 1000 * 1000)


class Document(BaseModel):
    """A MongoDB document with a UUID ``_id`` exposed as ``id``."""

    model_config = ConfigDict(use_enum_values=False)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)

    def to_doc(self) -> dict[str, Any]:
        data = self.model_dump(mode="python")
        data["_id"] = data.pop("id")
        for k, v in data.items():
            if hasattr(v, "value"):  # enums → plain strings in the database
                data[k] = v.value
        return data

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> Self:
        data = dict(doc)
        data["id"] = data.pop("_id")
        return cls.model_validate(data)


class Timestamped(Document):
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
