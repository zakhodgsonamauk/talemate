"""
Flow correlation for the debug log.

A "flow" is one externally-triggered unit of work - a websocket action like
`visual:visualize` or a game-loop round. Binding a flow id into structlog's
contextvars makes every log line emitted anywhere inside that work (including
inside spawned tasks, which inherit the contextvar copy) carry `flow=...`,
so one grep reconstructs the whole fan-out.

LLM calls made during a flow are accumulated and summarized when the flow
closes: total calls, total LLM seconds, per-agent counts. The list object is
shared with tasks spawned inside the flow, so calls they record before the
flow closes are counted; calls after close are still flow-tagged in the log
but miss the summary - the summary is best-effort, the tag is authoritative.

Never raises: correlation is a diagnostic aid and must not take a flow down.
"""

import contextvars
import uuid
from contextlib import contextmanager

import structlog

__all__ = ["flow", "record_llm_call"]

log = structlog.get_logger("talemate.flowlog")

_flow_calls: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "flow_llm_calls", default=None
)


@contextmanager
def flow(name: str):
    """
    Open a correlation flow. All log lines inside carry flow=<name>:<id6>,
    and a flow.summary line is emitted on close when LLM calls were recorded.
    """
    flow_id = f"{name}:{uuid.uuid4().hex[:6]}"
    calls: list[dict] = []
    calls_token = _flow_calls.set(calls)
    # bound_contextvars restores any outer flow's value on exit, so nested
    # flows do not clobber each other
    with structlog.contextvars.bound_contextvars(flow=flow_id):
        try:
            yield flow_id
        finally:
            try:
                if calls:
                    per_agent: dict[str, int] = {}
                    for call in calls:
                        agent = call.get("agent") or "unattributed"
                        per_agent[agent] = per_agent.get(agent, 0) + 1
                    log.info(
                        "flow.summary",
                        llm_calls=len(calls),
                        llm_seconds=round(
                            sum(c.get("duration") or 0 for c in calls), 2
                        ),
                        agents=per_agent,
                    )
            except Exception:
                pass
            _flow_calls.reset(calls_token)


def record_llm_call(**fields):
    """
    Record one LLM call against the active flow (no-op outside a flow).
    Called by the client base after each generation completes.
    """
    try:
        calls = _flow_calls.get()
        if calls is not None:
            calls.append(fields)
    except Exception:
        pass
