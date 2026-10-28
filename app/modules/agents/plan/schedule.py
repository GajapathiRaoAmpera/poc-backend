import heapq
import math
from datetime import date, timedelta

from app.modules.agents.plan.schema import (
    PlanAgentOutput,
    PlanContent,
    Schedule,
    ScheduledMilestone,
    ScheduledTask,
)


def _roll(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _add_wd(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _count_wd(a: date, b: date) -> int:
    return sum(1 for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5)


def compute_schedule(
    out: PlanAgentOutput, start: date, team_size: dict[str, int], target: date | None
) -> PlanContent:
    """List scheduling in topological order (ties by id). Assumes validated input."""
    first = _roll(start)
    by_id = {t.id: t for t in out.tasks}
    pending = {t.id: len(t.depends_on) for t in out.tasks}
    children: dict[str, list[str]] = {}
    for t in out.tasks:
        for d in t.depends_on:
            children.setdefault(d, []).append(t.id)
    ready = [i for i, n in pending.items() if n == 0]
    heapq.heapify(ready)
    slots: dict[str, list[date | None]] = {}
    ends: dict[str, date] = {}
    scheduled: dict[str, ScheduledTask] = {}
    while ready:
        t = by_id[heapq.heappop(ready)]
        role_slots = slots.setdefault(t.role, [None] * max(1, team_size.get(t.role, 1)))
        k = min(range(len(role_slots)), key=lambda i: (role_slots[i] or date.min, i))
        floor = [ends[d] for d in t.depends_on] + ([role_slots[k]] if role_slots[k] else [])
        s = _roll(_add_wd(max(floor), 1)) if floor else first
        s = max(s, first, _roll(t.not_before) if t.not_before else first)
        e = _add_wd(s, math.ceil(t.effort_days) - 1)
        role_slots[k] = ends[t.id] = e
        scheduled[t.id] = ScheduledTask(**t.model_dump(), start=s, end=e)
        for c in children.get(t.id, []):
            pending[c] -= 1
            if pending[c] == 0:
                heapq.heappush(ready, c)
    if len(scheduled) != len(by_id):
        raise ValueError("Dependency cycle or unknown dependency")
    tasks = [scheduled[t.id] for t in out.tasks]
    # Starts on the start date even if a pinned first task begins later; edits anchor on it.
    p_start = first
    p_end = max(t.end for t in tasks)
    milestones = []
    for m in out.milestones:
        mine = [t for t in tasks if t.milestone_id == m.id]
        milestones.append(
            ScheduledMilestone(
                **m.model_dump(),
                start=min((t.start for t in mine), default=p_start),
                end=max((t.end for t in mine), default=p_start),
            )
        )
    warnings = []
    if target and p_end > target:
        late = _count_wd(target + timedelta(days=1), p_end)
        warnings.append(f"Ends {late} working days after your target date ({target.isoformat()})")
    return PlanContent(
        summary=out.summary,
        milestones=milestones,
        tasks=tasks,
        risks=out.risks,
        schedule=Schedule(
            start=p_start, end=p_end, working_days=_count_wd(p_start, p_end), target_date=target
        ),
        warnings=warnings,
    )
