import operator
import uuid
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.modules.delivery.checkpoint import make_checkpointer


class S(TypedDict, total=False):
    log: Annotated[list[str], operator.add]
    answer: str


def _toy(checkpointer, with_gate: bool = False):
    g = StateGraph(S)
    g.add_node("a", lambda s: {"log": ["a"]})

    def b(s):
        if with_gate:
            return {"log": ["b"], "answer": interrupt({"kind": "approval"})}
        return {"log": ["b"]}

    g.add_node("b", b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g.compile(checkpointer=checkpointer)


def _cfg():
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


async def test_checkpoint_roundtrip(db):
    cfg = _cfg()
    await _toy(make_checkpointer(db)).ainvoke({"log": []}, cfg)
    state = await _toy(make_checkpointer(db)).aget_state(cfg)
    assert state.values["log"] == ["a", "b"]
    assert state.next == ()


async def test_interrupt_resume_persists(db):
    cfg = _cfg()
    await _toy(make_checkpointer(db), with_gate=True).ainvoke({"log": []}, cfg)
    paused = await _toy(make_checkpointer(db), with_gate=True).aget_state(cfg)
    assert paused.next == ("b",)
    assert paused.tasks[0].interrupts[0].value == {"kind": "approval"}

    out = await _toy(make_checkpointer(db), with_gate=True).ainvoke(Command(resume="yes"), cfg)
    assert out["answer"] == "yes"
    assert out["log"] == ["a", "b"]
