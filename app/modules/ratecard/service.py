from decimal import Decimal
from typing import Any

from app.core.db import Db
from app.core.documents import now
from app.core.errors import AppError
from app.modules.artifacts.models import Revision
from app.modules.artifacts.service import mark_stale
from app.modules.audit.service import record_audit
from app.modules.auth.models import User
from app.modules.delivery.facts import on_approved_sources
from app.modules.delivery.routing import NO_RATES
from app.modules.delivery.service import notify
from app.modules.projects.models import Project
from app.modules.ratecard.models import RateCard, RoleRate

RATES_CHANGED = "Rates changed"
SEED = RateCard(
    currency="USD",
    hours_per_day=Decimal(8),
    roles=[
        RoleRate(role=r, hourly_rate=Decimal(p))
        for r, p in [
            ("frontend_engineer", 60),
            ("backend_engineer", 65),
            ("devops_engineer", 75),
            ("qa_engineer", 50),
            ("designer", 55),
            ("project_manager", 60),
        ]
    ],
)


def _num(d: Decimal) -> str:
    return format(d.normalize(), "f")


def snapshot(card: RateCard) -> dict[str, Any]:
    """JSON-safe, canonical form (so 65 and 65.00 compare equal)."""
    return {
        "currency": card.currency,
        "hours_per_day": _num(card.hours_per_day),
        "roles": [{"role": r.role, "hourly_rate": _num(r.hourly_rate)} for r in card.roles],
    }


async def default_doc(db: Db) -> dict[str, Any]:
    doc = await db.settings.find_one({"_id": "rate_card"})
    if doc is None:
        # Startup seeds it, but a recreated database or a worker that starts first must not fail
        # every plan and budget run. The upsert is idempotent, so concurrent callers are safe.
        await seed_default(db)
        doc = await db.settings.find_one({"_id": "rate_card"})
    if doc is None:
        raise AppError("INTERNAL", "Default rate card could not be created", 500)
    return doc


async def get_default(db: Db) -> RateCard:
    return RateCard.model_validate(await default_doc(db))


async def seed_default(db: Db) -> None:
    await db.settings.update_one(
        {"_id": "rate_card"},
        {
            "$setOnInsert": {
                **SEED.model_dump(mode="json"),
                "updated_at": now(),
                "updated_by": None,
            }
        },
        upsert=True,
    )


async def effective(db: Db, project: Project) -> RateCard:
    if project.rate_card:
        return RateCard.model_validate(project.rate_card)
    return await get_default(db)


async def mark_budgets_stale(db: Db, projects: list[Project]) -> list[Project]:
    marked = []
    for project in projects:
        art = await db.artifacts.find_one({"project_id": project.id, "kind": "budget"})
        if art is None or art["latest_revision"] == 0:
            continue
        rev = await db.artifact_revisions.find_one(
            {"artifact_id": art["_id"], "revision": art["latest_revision"]}
        )
        if rev["content"].get("rate_card") != snapshot(await effective(db, project)):
            await mark_stale(db, project, "budget", RATES_CHANGED)
            marked.append(project)
        elif await on_approved_sources(db, project, Revision.from_doc(rev), "budget"):
            # Rates changed back to what the budget used, on current inputs: undo only that.
            await db.artifacts.update_one(
                {"_id": art["_id"], "stale": True, "stale_reason": RATES_CHANGED},
                {"$set": {"stale": False, "stale_reason": None, "updated_at": now()}},
            )
    return marked


async def save_default(db: Db, admin: User, card: RateCard) -> RateCard:
    await db.settings.update_one(
        {"_id": "rate_card"},
        {"$set": {**card.model_dump(mode="json"), "updated_at": now(), "updated_by": admin.id}},
        upsert=True,
    )
    await record_audit(db, admin.id, "rate_card.updated", "settings", None)
    # shortcut: scans every project without an override, fine for the pilot;
    # move to a background job past ~1k projects
    ids = await db.artifacts.distinct(
        "project_id", {"kind": "budget", "latest_revision": {"$gt": 0}}
    )
    docs = db.projects.find({"_id": {"$in": ids}, "rate_card": None})
    marked = await mark_budgets_stale(db, [Project.from_doc(d) async for d in docs])
    # Projects paused because the card had no roles can continue now.
    waiting = await db.delivery_threads.distinct(
        "project_id",
        {"status": "PAUSED", "pending.kind": "needs_input", "pending.payload.reason": NO_RATES},
    )
    seen = {p.id for p in marked}
    waiting_docs = db.projects.find(
        {"_id": {"$in": [i for i in waiting if i not in seen]}, "rate_card": None}
    )
    for project in marked + [Project.from_doc(d) async for d in waiting_docs]:
        await notify(db, project, "rates_changed")
    return card


async def save_override(db: Db, project: Project, user: User, card: RateCard | None) -> RateCard:
    pref = ((project.preferences or {}).get("budget") or {}).get("currency")
    if card and pref and card.currency != pref:
        raise AppError(
            "CURRENCY_MISMATCH",
            f"The rate card currency ({card.currency}) must match the project budget currency "
            f"({pref}).",
            422,
        )
    project.rate_card = card.model_dump(mode="json") if card else None
    await db.projects.update_one(
        {"_id": project.id}, {"$set": {"rate_card": project.rate_card, "updated_at": now()}}
    )
    await record_audit(db, user.id, "project.rate_card_updated", "project", project.id)
    await mark_budgets_stale(db, [project])
    await notify(db, project, "rates_changed")
    return await effective(db, project)
