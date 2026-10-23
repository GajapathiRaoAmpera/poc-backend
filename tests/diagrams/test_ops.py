import json
import pathlib

import pytest

from app.modules.diagrams.ops import OpError, apply_ops, parse_ops
from app.modules.diagrams.schema import ArchitectureContent

DATA = json.loads(pathlib.Path("tests/fixtures/diagram_ops_cases.json").read_text())
BASE = ArchitectureContent.model_validate(DATA["base"])


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
def test_shared_cases(case):
    ops = parse_ops(case["ops"])
    if "error" in case:
        with pytest.raises(OpError) as e:
            apply_ops(BASE, ops)
        assert e.value.index == case["error"]["index"]
        assert case["error"]["contains"] in str(e.value)
        return
    out = apply_ops(BASE, ops)
    assert [n.id for n in out.nodes] == case["expect"]["node_ids"]
    assert [e.id for e in out.edges] == case["expect"]["edge_ids"]
    if "node" in case["expect"]:
        node = next(n for n in out.nodes if n.id == case["expect"]["node"]["id"])
        for k, v in case["expect"]["node"].items():
            assert getattr(node, k) == v


def test_apply_does_not_mutate_base():
    apply_ops(BASE, parse_ops([{"op": "remove_node", "id": "n_api"}]))
    assert len(BASE.nodes) == 3


def test_update_cannot_change_id():
    with pytest.raises(OpError):
        apply_ops(
            BASE, parse_ops([{"op": "update_node", "id": "n_api", "fields": {"id": "n_other"}}])
        )


def test_add_group_and_decision():
    out = apply_ops(
        BASE,
        parse_ops(
            [
                {"op": "add_group", "group": {"id": "g_prod", "label": "Production"}},
                {"op": "update_node", "id": "n_api", "fields": {"group": "g_prod"}},
                {
                    "op": "add_decision",
                    "decision": {
                        "id": "ADR-001",
                        "title": "t",
                        "decision": "d",
                        "alternatives": [],
                        "rationale": "r",
                        "requirement_ids": [],
                    },
                },
            ]
        ),
    )
    assert (
        out.groups[0].id == "g_prod"
        and out.nodes[1].group == "g_prod"
        and out.decisions[0].id == "ADR-001"
    )


def test_unknown_op_rejected():
    with pytest.raises(OpError):
        parse_ops([{"op": "explode", "id": "n_api"}])
