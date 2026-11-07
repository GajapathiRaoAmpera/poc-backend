from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MAX_HTML = 30 * 1024
MAX_SCREENS = 12
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
ScreenId = Annotated[str, StringConstraints(pattern=r"^S-\d{3}$")]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Theme(_M):
    primary: Short  # CSS colour, e.g. #4f46e5
    surface: Short
    text: Short
    font_family: Short
    radius: Short  # CSS length, e.g. 12px


class Screen(_M):
    id: ScreenId
    name: Short
    purpose: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
    requirement_ids: Annotated[list[str], Field(max_length=40)] = []
    html: Annotated[str, StringConstraints(max_length=MAX_HTML)]


class Flow(_M):
    from_screen: ScreenId
    action: Short
    to_screen: ScreenId


class Shot(_M):
    screen_id: ScreenId
    media_id: str


class DesignAgentOutput(_M):
    summary: Annotated[str, StringConstraints(max_length=500)]
    theme: Theme
    screens: Annotated[list[Screen], Field(min_length=1, max_length=MAX_SCREENS)]
    flows: Annotated[list[Flow], Field(max_length=40)] = []


class DesignContent(DesignAgentOutput):
    # Written by the platform after rendering, never by the LLM.
    screenshots: list[Shot] = []


class ScreenChange(_M):
    op: Literal["write", "delete"]
    screen: Screen | None = None
    id: ScreenId | None = None


class DesignChanges(_M):
    summary: Annotated[str, StringConstraints(max_length=500)]
    changes: Annotated[list[ScreenChange], Field(min_length=1, max_length=MAX_SCREENS)]
