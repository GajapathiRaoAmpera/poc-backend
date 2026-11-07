import json

from app.modules.agents.architecture.mock_fixture import architecture_output
from app.modules.diagrams.ops import apply_ops, parse_ops
from app.modules.diagrams.schema import ArchitectureContent
from app.modules.diagrams.validate import data_model_issues


def _f(name, type_="string", pk=False, ref=None):
    return {"name": name, "type": type_, "pk": pk, "nullable": not pk, "unique": False, "ref": ref}


def _ent(id_, fields, db="n_db"):
    return {"id": id_, "name": id_[4:], "database_node_id": db, "fields": fields}


def _content(entities=(), relations=(), db_type="database"):
    return ArchitectureContent.model_validate(
        {
            "nodes": [
                {"id": "n_db", "type": db_type, "label": "DB"},
                {"id": "n_api", "type": "backend", "label": "API"},
            ],
            "data_model": {"entities": list(entities), "relations": list(relations)},
        }
    )


def _codes(c):
    return {i.code for i in data_model_issues(c)}


USER = _ent("ent_user", [_f("id", "uuid", pk=True)])
ORDER = _ent("ent_order", [_f("id", "uuid", pk=True), _f("user_id", "uuid", ref="ent_user.id")])
REL = {"id": "rel_user_orders", "from_entity": "ent_user", "to_entity": "ent_order", "kind": "1-n"}


def test_old_revision_without_data_model_loads():
    c = ArchitectureContent.model_validate({"nodes": []})
    assert c.data_model.entities == []


def test_entity_must_point_at_database_node():
    assert "DM_DB_NODE" in _codes(_content([_ent("ent_user", USER["fields"], db="n_api")]))


def test_exactly_one_pk():
    assert "DM_PK" in _codes(_content([_ent("ent_user", [_f("id")])]))
    assert "DM_PK" in _codes(_content([_ent("ent_user", [_f("a", pk=True), _f("b", pk=True)])]))
    assert _codes(_content([USER])) == set()


def test_ref_must_exist():
    bad = _ent("ent_order", [_f("id", pk=True), _f("u", ref="ent_user.nope")])
    assert "DM_REF" in _codes(_content([USER, bad]))
    assert "DM_REF" in _codes(_content([USER], [REL]))


def test_duplicate_names():
    assert "DM_DUPLICATE" in _codes(_content([USER, USER]))


def test_database_node_requires_entities():
    assert "DM_MISSING" in _codes(_content())
    assert _codes(_content(db_type="backend")) == set()


def test_remove_entity_cascades():
    c = _content([USER, ORDER], [REL])
    out = apply_ops(c, parse_ops([{"op": "remove_entity", "id": "ent_user"}]))
    assert out.data_model.relations == []
    assert [e.id for e in out.data_model.entities] == ["ent_order"]
    assert out.data_model.entities[0].fields[1].ref is None


def test_upsert_ops():
    c = _content([USER])
    out = apply_ops(
        c,
        parse_ops(
            [{"op": "upsert_entity", "entity": ORDER}, {"op": "upsert_relation", "relation": REL}]
        ),
    )
    assert len(out.data_model.entities) == 2 and len(out.data_model.relations) == 1
    out = apply_ops(
        out,
        parse_ops(
            [
                {"op": "upsert_entity", "entity": {**ORDER, "description": "x"}},
                {"op": "remove_relation", "id": "rel_user_orders"},
            ]
        ),
    )
    assert len(out.data_model.entities) == 2 and out.data_model.relations == []


def test_mock_fixture_has_valid_data_model():
    out = architecture_output(
        "",
        "<approved_requirements>\n"
        + json.dumps({"requirements": []})
        + "\n</approved_requirements>",
    )
    c = ArchitectureContent.model_validate(out["content"])
    assert len(c.data_model.entities) >= 3
    assert data_model_issues(c) == []
