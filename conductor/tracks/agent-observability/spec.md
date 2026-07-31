# Agent observability — specification

## Goal

Debugging today means reconstructing agent behavior from an info-level console log that
lacks attribution, durations, and decision detail — or spelunking node graphs. Tonight's
example: "Adjust & Visualize produced a blank prompt and attached the wrong character's
reference" was undiagnosable because every relevant decision event
(`choose_subject`, `attach_character_references.*`, `distill_prompt.*`) logs at debug
level and never reaches the console, `Sending prompt` lines don't say which agent/action
sent them, and nothing records durations.

Deliver a persistent, structured, always-on debug log file that makes any agent flow
reconstructable after the fact with grep/jq — no code reading, no graph traversal.

## Requirements

### R1 — Structured JSONL file sink

- All structlog output additionally written to a rotating file (e.g.
  `logs/talemate-debug.jsonl` under the project root), one JSON object per line.
- File sink level: DEBUG (captures the existing decision events immediately — they are
  already written, just filtered). Console stays INFO, unchanged.
- UTF-8 explicitly (Windows: cp1252 default has bitten this project twice already).
- Rotation: size-based (e.g. 20 MB × 5 backups). No unbounded growth.
- Config block in `config.yaml` (enabled, path, level, rotation) with sane defaults ON.
- Locate the existing structlog configuration (likely `src/talemate/server/run.py` or a
  logging module) and extend it — do not replace console behavior.

### R2 — LLM call instrumentation

Every client generation (one place: `src/talemate/client/base.py` send/generate path)
logs a single completion event carrying:
- client name + model
- agent_type and agent action (available from `active_agent` context —
  `talemate.agents.context.active_agent.get()` has `.agent.agent_type`, `.action`, and
  `.agent_stack` — the stack string is the attribution chain)
- prompt kind, prompt token length, response token length
- duration ms and derived tokens/sec
- the existing "Sending prompt" start line gains agent/kind attribution too.

This single event answers "what were the N calls in this turn, who made them, how long
did each take" — the question that took manual gap-arithmetic tonight.

### R3 — Flow correlation

- A correlation id contextvar, set at the two flow entry points:
  websocket actions (`server/websocket_plugin.py` action dispatch — id = router:action +
  short uuid) and game-loop rounds (scene loop iteration counter).
- Bound into structlog context (structlog contextvars integration) so every log line in
  a flow carries `flow=...`. Reconstructing "everything that happened for this visualize
  click" becomes `grep flow=visual:visualize:ab12`.

### R4 — Flow summaries

- At websocket-action completion (the existing done/failed envelope points in
  `websocket_plugin.py`) emit one summary line: flow id, total LLM calls, total LLM time,
  per-agent call counts. Cheap: aggregate from R2 events held in the contextvar.

### R5 — Reading documentation

- Short doc (`docs/fork/debug-logging.md`): where the file is, jq/grep recipes for the
  common questions (all calls in a flow, slowest calls, decision events for a visualize,
  agent fan-out per turn), and the caveat list (rotation, level config).

## Acceptance criteria

- AC1: fresh backend start writes JSONL debug file; console output unchanged.
- AC2: a `choose_subject` / `attach_character_references` debug event appears in the file
  during a visualize (statically testable: logger call at debug level reaches the file
  handler in a unit test).
- AC3: every LLM completion event has agent_type, action, client, kind, token counts,
  duration. Unit test with a stubbed client.
- AC4: log lines within one websocket action share a flow id; summary line emitted.
- AC5: file is valid JSONL (each line parses), UTF-8, rotates at configured size.
- AC6: doc exists with working recipes.

## Out of scope

- Changing console log level or format.
- A log viewer UI.
- Tracing spans/OpenTelemetry — JSONL + flow ids is the 90% answer at 10% cost.
- Fixing the Adjust & Visualize bugs themselves (separate work; this track is what makes
  that diagnosis cheap).

## Technical notes

- structlog is already used everywhere (`structlog.get_logger(...)`); the win is sink +
  context, not new call sites. Existing debug events become visible for free.
- `active_agent` context (agents/context.py) already computes an agent stack string —
  reuse it, do not invent parallel attribution.
- Client base already logs prompt head/tail on empty responses (added 2026-07-31);
  R2's completion event complements it.
- Beware duplicate handlers on backend restart-in-place (uvicorn reload) — guard handler
  installation idempotently.
