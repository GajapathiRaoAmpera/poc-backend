from app.modules.artifacts import registry
from app.modules.artifacts.models import SourceRef
from app.modules.diagrams.schema import ArchitectureContent
from app.modules.diagrams.validate import validate_semantics, validate_structure


async def load_context(db, project, sources: list[SourceRef]) -> registry.ValidationContext:
    ref = next((s for s in sources if s.kind == "requirements"), None)
    reqs: list[dict] = []
    if ref is not None:
        doc = await db.artifact_revisions.find_one(
            {"project_id": project.id, "kind": "requirements", "revision": ref.revision}
        )
        reqs = (doc or {}).get("content", {}).get("requirements", [])
    return registry.ValidationContext(
        sources={
            "requirement_ids": {r["id"] for r in reqs},
            "must_ids": {r["id"] for r in reqs if r.get("priority") == "MUST"},
            "requirement_texts": [f"{r.get('title', '')} {r.get('description', '')}" for r in reqs],
        }
    )


def _approval(c: ArchitectureContent, ctx: registry.ValidationContext):
    s = ctx.sources
    return validate_semantics(
        c, s.get("requirement_ids", set()), s.get("must_ids", set()), s.get("requirement_texts", [])
    )


def register() -> None:
    registry.register(
        registry.KindSpec(
            kind="architecture",
            schema_version="1.0",
            model=ArchitectureContent,
            validate_save=lambda c, ctx: validate_structure(c),
            validate_approval=_approval,
            load_context=load_context,
        )
    )
