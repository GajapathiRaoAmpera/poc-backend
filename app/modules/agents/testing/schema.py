from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MAX_CASES = 40
MAX_STEPS = 15
MAX_RESULT_CHARS = 3000

Tool = Literal[
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_snapshot",
    "browser_take_screenshot",
    "http_request",
    "report_result",
]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class TestCase(_M):
    __test__ = False  # not a pytest class

    id: Annotated[str, StringConstraints(pattern=r"^TC-\d{3}$")]
    requirement_id: Annotated[str, StringConstraints(max_length=20)]
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    kind: Literal["ui", "api"]
    steps: Annotated[list[Annotated[str, StringConstraints(max_length=300)]], Field(max_length=15)]


class TestPlan(_M):
    __test__ = False

    summary: Annotated[str, StringConstraints(max_length=500)]
    cases: Annotated[list[TestCase], Field(min_length=1, max_length=MAX_CASES)]


class Step(_M):
    tool: Tool
    args: dict[str, str] = {}


class CaseResult(TestCase):
    passed: bool
    evidence: Annotated[str, StringConstraints(max_length=3000)] = ""
    screenshot_media_id: str | None = None
    steps_used: int = 0


class TestReport(_M):
    __test__ = False

    summary: Annotated[str, StringConstraints(max_length=500)]
    results: list[CaseResult]
    passed: int
    failed: int
    must_failed: list[str] = []  # requirement ids of MUST requirements with a failed case
