# Agent observability — implementation plan

**Spec**: `conductor/tracks/agent-observability/spec.md`

Constraints carried from the session: the user's backend may be running — static
verification (pytest, imports, parse) only, unless the user explicitly hands over
restart control. `.venv/Scripts/python.exe` for everything. Windows: file handlers must
pass `encoding="utf-8"`. Commit per phase, conventional messages, footer
`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Phase 1 — File sink + config (R1, AC1, AC5)

### [ ] T1 — Locate and extend logging setup
Find structlog configuration (search `structlog.configure` — expect `src/talemate/log.py`
or `server/run.py`). Add a `logging.handlers.RotatingFileHandler`
(`encoding="utf-8"`, maxBytes/backupCount from config) at DEBUG with a JSON renderer,
installed idempotently (guard against double-install on reload). Console pipeline
untouched.

### [ ] T2 — Config schema
Add `debug_log` block (enabled=True, path="logs/talemate-debug.jsonl", level="DEBUG",
max_mb=20, backups=5) to the config schema (`src/talemate/config/schema.py` — follow an
existing General-style block). Wire into T1. Failing test first: config round-trip +
handler present when enabled.

### [ ] T3 — JSONL validity test
Unit test: emit info+debug events through the configured pipeline into a temp file;
assert each line json-parses, debug event present, utf-8 content (include an emoji).

**Commit phase 1.**

## Phase 2 — LLM call instrumentation (R2, AC3)

### [ ] T4 — Completion event in client base
In `src/talemate/client/base.py` generate/send path (same function that logs
"Sending prompt" and the empty-response warning): capture t0/t1 around the backend call;
after completion log `llm.call.completed` with client name, model, kind, prompt/response
token counts (`util.count_tokens`), duration_ms, tokens_per_sec. Pull agent attribution
from `talemate.agents.context.active_agent.get()` (agent_type, action, agent_stack) with
None-safe fallbacks. Add the same attribution fields to the "Sending prompt" line.

### [ ] T5 — Test with stubbed client
Existing client tests (`tests/test_client_base.py`) show how to instantiate a client with
a fake backend — extend with a capturing log handler, assert the completion event fields.

**Commit phase 2.**

## Phase 3 — Flow correlation (R3, R4, AC4)

### [ ] T6 — Correlation contextvar
New tiny module (e.g. `src/talemate/flowlog.py`): `flow_id` contextvar +
`structlog.contextvars.bind_contextvars` integration (verify how the existing structlog
config processes contextvars; add the merge processor if absent — it changes ALL log
lines, keep the key set minimal: `flow`).

### [ ] T7 — Entry points
- `server/websocket_plugin.py` action dispatch: set `flow = f"{router}:{action}:{uuid4hex[:6]}"`
  around handler invocation (both direct handlers and sub_handlers), reset after.
- Scene loop round: bind `round=N` where the loop iterates (find the counter the
  scene-loop graph uses — `shared` state; a Python-side bind at `game_loop` signal send
  is acceptable and simpler than graph changes).

### [ ] T8 — Flow summary (R4)
Accumulate R2 completion events in a contextvar list when a flow is active; on the
websocket action's done/failed envelope (`signal_operation_done` / the done-callback in
`websocket_plugin.py`), emit `flow.summary` with call count, total duration, per-agent
counts. Test: fake two completion events inside a flow, assert summary aggregates.

**Commit phase 3.**

## Phase 4 — Docs + close (R5, AC6)

### [ ] T9 — `docs/fork/debug-logging.md`
Recipes (jq + grep, Windows-friendly): all lines for a flow, slowest LLM calls, decision
events during a visualize, per-turn agent fan-out, tail-follow during play. Note
rotation, config knobs, and that console behavior is unchanged.

### [ ] T10 — Track close
Full test run (`-k "log or client or flow"` plus touched suites), report.md with AC
checklist, tracks.md + metadata update. Note for user: restart backend to activate, then
one visualize click gives the Adjust & Visualize diagnosis data this track was born from.

## DAG
T1 -> T2 -> T3 (phase 1) -> T4 -> T5 (phase 2) -> T6 -> T7 -> T8 (phase 3) -> T9 -> T10.
Sequential; phases are the checkpoint boundaries.
