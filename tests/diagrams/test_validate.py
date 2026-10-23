import copy
import json
import pathlib

import pytest
from pydantic import ValidationError

from app.modules.diagrams.schema import ArchitectureContent
from app.modules.diagrams.validate import validate_semantics, validate_structure

BASE = json.loads(pathlib.Path("tests/fixtures/diagram_ops_cases.json").read_text())["base"]


def content(mut=None) -> ArchitectureContent:
    d = copy.deepcopy(BASE)
    if mut:
        mut(d)
    return ArchitectureContent.model_validate(d)


def codes(issues, severity=None):
    return {i.code for i in issues if severity is None or i.severity == severity}


def test_base_is_valid():
    assert validate_structure(content()) == []


def test_duplicate_node_ids():
    c = content(lambda d: d["nodes"].append(copy.deepcopy(d["nodes"][0])))
    assert "DUPLICATE_ID" in codes(validate_structure(c))


def test_edge_to_unknown_node():
    c = content(lambda d: d["edges"].append({**d["edges"][0], "id": "e_x", "target": "n_ghost"}))
    issues = validate_structure(c)
    assert "UNKNOWN_NODE" in codes(issues) and "n_ghost" in issues[0].message


def test_self_loop():
    c = content(lambda d: d["edges"].append({**d["edges"][0], "id": "e_loop", "target": "n_web"}))
    assert "SELF_LOOP" in codes(validate_structure(c))


def test_unknown_group():
    c = content(lambda d: d["nodes"][0].update(group="g_missing"))
    assert "UNKNOWN_GROUP" in codes(validate_structure(c))


@pytest.mark.parametrize("bad_id", ["web", "n_Web", "n-web", "n_" + "a" * 41])
def test_node_id_format(bad_id):
    with pytest.raises(ValidationError):
        content(lambda d: d["nodes"][0].update(id=bad_id))


def test_unknown_node_type():
    with pytest.raises(ValidationError):
        content(lambda d: d["nodes"][0].update(type="blockchain"))


def test_limits():
    def many(d):
        d["nodes"] = [{**d["nodes"][0], "id": f"n_{i}"} for i in range(201)]
        d["edges"] = []

    with pytest.raises(ValidationError):
        content(many)


def test_semantic_unknown_requirement_is_error():
    c = content(lambda d: d["nodes"][1].update(requirement_ids=["FR-999"]))
    issues = validate_semantics(c, requirement_ids={"FR-001"}, must_ids=set(), requirement_texts=[])
    assert "UNKNOWN_REQUIREMENT" in codes(issues, "error")


def test_semantic_uncovered_must_is_error():
    issues = validate_semantics(
        content(),
        requirement_ids={"FR-001", "FR-002"},
        must_ids={"FR-001", "FR-002"},
        requirement_texts=[],
    )
    errs = [i for i in issues if i.code == "UNCOVERED_REQUIREMENT"]
    assert len(errs) == 1 and "FR-002" in errs[0].message


def test_semantic_warnings():
    c = content(
        lambda d: (
            d["nodes"].append({**d["nodes"][0], "id": "n_lonely", "requirement_ids": []}),
            d["edges"].append({**d["edges"][0], "id": "e_dup"}),
        )
    )
    issues = validate_semantics(
        c,
        requirement_ids={"FR-001"},
        must_ids=set(),
        requirement_texts=["Users sign in with email"],
    )
    warns = codes(issues, "warning")
    assert {"ISOLATED_NODE", "NO_AUTH", "NO_MONITORING", "DUPLICATE_EDGE"} <= warns
    assert codes(issues, "error") == set()


def test_decisions_reference_requirements():
    c = content(
        lambda d: d["decisions"].append(
            {
                "id": "ADR-001",
                "title": "Use queue",
                "decision": "Redis",
                "alternatives": ["SQS"],
                "rationale": "Simple",
                "requirement_ids": ["FR-404"],
            }
        )
    )
    assert "UNKNOWN_REQUIREMENT" in codes(validate_semantics(c, {"FR-001"}, set(), []), "error")
