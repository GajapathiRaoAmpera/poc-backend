import json
import pathlib

from app.modules.diagrams.diff import diff, impact
from app.modules.diagrams.ops import apply_ops, parse_ops
from app.modules.diagrams.schema import ArchitectureContent

DATA = json.loads(pathlib.Path("tests/fixtures/diagram_ops_cases.json").read_text())
BASE = ArchitectureContent.model_validate(DATA["base"])


def test_diff_added_removed_modified():
    after = apply_ops(BASE, parse_ops(DATA["cases"][0]["ops"]))
    after = apply_ops(
        after, parse_ops([{"op": "update_node", "id": "n_api", "fields": {"label": "Core API"}}])
    )
    d = diff(BASE, after)
    assert d.nodes.added == ["n_redis"] and d.nodes.removed == []
    assert d.nodes.modified[0].id == "n_api"
    assert d.nodes.modified[0].fields["label"] == {"from": "API", "to": "Core API"}
    assert sorted(d.edges.added) == ["e_api_redis", "e_redis_worker"] and d.edges.removed == [
        "e_api_worker"
    ]


def test_diff_ignores_position_unless_requested():
    moved = apply_ops(
        BASE,
        parse_ops(
            [{"op": "update_node", "id": "n_web", "fields": {"position": {"x": 99, "y": 9}}}]
        ),
    )
    assert diff(BASE, moved).nodes.modified == []
    assert diff(BASE, moved, include_layout=True).nodes.modified[0].id == "n_web"


def test_impact_lists_requirements():
    after = apply_ops(BASE, parse_ops([{"op": "remove_node", "id": "n_web"}]))
    imp = impact(BASE, after)
    assert imp.removed == ["n_web", "e_web_api"] and imp.requirement_ids == ["FR-001"]


def test_diff_and_impact_carry_readable_labels():
    after = apply_ops(BASE, parse_ops([{"op": "remove_node", "id": "n_web"}]))
    labels = impact(BASE, after).labels
    assert labels["n_web"] == BASE.nodes[0].label  # removed items keep their name
    assert labels["e_web_api"] == f"{BASE.nodes[0].label} → API (REST)"
    assert diff(BASE, after).labels == labels
