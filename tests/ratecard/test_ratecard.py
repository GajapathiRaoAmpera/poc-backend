from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.modules.artifacts.models import Artifact, Revision
from app.modules.projects.models import Project
from app.modules.ratecard import service
from app.modules.ratecard.models import RateCard, RoleRate
from tests.factories import make_user

SEED_RATES = {
    "frontend_engineer": 60,
    "backend_engineer": 65,
    "devops_engineer": 75,
    "qa_engineer": 50,
    "designer": 55,
    "project_manager": 60,
}


def card(rate=70, currency="USD", hpd=8, role="backend_engineer"):
    return RateCard(
        currency=currency,
        hours_per_day=Decimal(str(hpd)),
        roles=[RoleRate(role=role, hourly_rate=Decimal(str(rate)))],
    )


async def project(db, owner, **kw):
    p = Project(owner_id=owner.id, name="P", **kw)
    await db.projects.insert_one(p.to_doc())
    return p


async def add_budget(db, p, rate_card):
    art = Artifact(project_id=p.id, owner_id=p.owner_id, kind="budget", latest_revision=1)
    await db.artifacts.insert_one(art.to_doc())
    rev = Revision(
        artifact_id=art.id,
        project_id=p.id,
        owner_id=p.owner_id,
        kind="budget",
        revision=1,
        schema_version="1",
        content={"rate_card": service.snapshot(rate_card)},
        origin="agent",
        summary="s",
        created_by=None,
    )
    await db.artifact_revisions.insert_one(rev.to_doc())


async def test_seed_written_once(db):
    await service.seed_default(db)
    await service.seed_default(db)
    got = await service.get_default(db)
    assert got.currency == "USD" and got.hours_per_day == 8
    assert {r.role: r.hourly_rate for r in got.roles} == {
        k: Decimal(v) for k, v in SEED_RATES.items()
    }
    assert (await service.default_doc(db))["updated_by"] is None
    assert await db.settings.count_documents({}) == 1


@pytest.mark.parametrize(
    "kw",
    [
        {"hpd": 0},
        {"hpd": 13},
        {"hpd": "7.3"},
        {"rate": 0},
        {"rate": -1},
        {"role": "Bad Role"},
        {"currency": "usd"},
    ],
)
def test_validation_rules_single_field(kw):
    with pytest.raises(ValidationError):
        card(**kw)


def test_validation_rules_roles():
    rr = RoleRate(role="designer", hourly_rate=Decimal(5))
    with pytest.raises(ValidationError):
        RateCard(currency="USD", hours_per_day=Decimal(8), roles=[rr, rr])
    roles = [RoleRate(role=f"role_{i}", hourly_rate=Decimal(5)) for i in range(21)]
    with pytest.raises(ValidationError):
        RateCard(currency="USD", hours_per_day=Decimal(8), roles=roles)
    assert card(hpd="7.5").rate("backend_engineer") == 70 and card().rate("x") is None


async def test_effective_prefers_override(db):
    await service.seed_default(db)
    user = await make_user(db)
    p = await project(db, user)
    default = await service.get_default(db)
    assert await service.effective(db, p) == default
    assert await service.save_override(db, p, user, card()) == card()
    p = Project.from_doc(await db.projects.find_one({"_id": p.id}))
    assert await service.effective(db, p) == card()
    await service.save_override(db, p, user, None)
    p = Project.from_doc(await db.projects.find_one({"_id": p.id}))
    assert await service.effective(db, p) == default


async def test_override_currency_must_match_budget_pref(db):
    await service.seed_default(db)
    user = await make_user(db)
    p = await project(db, user, preferences={"budget": {"currency": "EUR"}})
    with pytest.raises(AppError) as e:
        await service.save_override(db, p, user, card())
    assert e.value.code == "CURRENCY_MISMATCH" and e.value.status == 422
    await service.save_override(db, p, user, card(currency="EUR"))


async def test_save_default_marks_only_differing_budgets_stale(db):
    await service.seed_default(db)
    admin = await make_user(db, email="a@x.com")
    new = card(rate=99)
    same, differs, overridden = [await project(db, admin) for _ in range(3)]
    overridden.rate_card = card(rate=1).model_dump(mode="json")
    await db.projects.update_one(
        {"_id": overridden.id}, {"$set": {"rate_card": overridden.rate_card}}
    )
    await add_budget(db, same, new)
    await add_budget(db, differs, await service.get_default(db))
    await add_budget(db, overridden, card(rate=2))
    await service.save_default(db, admin, new)

    async def art(p):
        return await db.artifacts.find_one({"project_id": p.id, "kind": "budget"})

    a = await art(differs)
    assert a["stale"] is True and a["stale_reason"] == "Rates changed"
    assert (await art(same))["stale"] is False
    assert (await art(overridden))["stale"] is False


async def test_audit_written(db):
    await service.seed_default(db)
    user = await make_user(db)
    p = await project(db, user)
    await service.save_default(db, user, card())
    await service.save_override(db, p, user, card())
    actions = {a["action"] async for a in db.audit_logs.find()}
    assert {"rate_card.updated", "project.rate_card_updated"} <= actions


async def test_default_seeds_itself_when_missing(db):
    # A fresh or recreated database (no init-db, worker first) must not fail every agent run.
    assert await db.settings.find_one({"_id": "rate_card"}) is None
    card = await service.get_default(db)
    assert card == service.SEED
    doc = await db.settings.find_one({"_id": "rate_card"})
    assert doc is not None and doc["updated_by"] is None
