# Agent observability — execution report

Executed 2026-07-31, directly in the main session (per user request for
visibility) after standing down the orchestrator agent. One collision from the
handover was reconciled: the agent had already built `talemate/debuglog.py` +
schema block + run.py wiring + 12 tests before the stand-down landed; its
implementation was kept (better failure-degradation and exc_info handling than
the main session's parallel draft, which was deleted). Duplicate config
schema entries from the two drafts were merged into one (`DebugLogConfig`).

## Commits

| Commit | Content |
|--------|---------|
| 15d0e9c | JSONL debug file sink (tee processor, console byte-identical), DebugLogConfig, run.py wiring, 12 tests |
| 3f29374 | llm.call.completed instrumentation (client/base.py, reusing PromptData), flowlog module (flow ids, round binding, flow.summary), 7 tests |
| (this)  | docs/fork/debug-logging.md, track close |

## Acceptance criteria

- AC1 (file sink, console unchanged) — DONE. Tee processor spliced before the
  default ConsoleRenderer; console gate reproduces the old wrapper level.
  Caveat (accepted): lines inside flows now carry `flow=`/`round=` keys on
  console too — level and format unchanged.
- AC2 (debug decision events reach file) — DONE, covered by
  tests/test_debuglog.py (debug event asserted in file while absent from
  console pipeline level).
- AC3 (LLM completion attribution) — DONE. Reuses PromptData (agent_type,
  agent_action, agent_stack already assembled there); adds duration + tok/s.
- AC4 (flow ids + summary) — DONE. Websocket dispatch wraps in
  flowlog.flow(router:action); game loop binds round=N;
  flow.summary aggregates count/seconds/per-agent. Background-task caveat
  documented in docs and module docstring.
- AC5 (valid JSONL, utf-8, rotation) — DONE, tested including emoji content.
- AC6 (docs) — DONE: docs/fork/debug-logging.md with Windows-friendly recipes.

## Verification

- 19 track tests + 315 adjacent (client/websocket/scene_loop) pass.
- Pre-existing failures unrelated (time-tick flakes, cp1252 validator bug).

## Activation

Backend restart required. First diagnosis to run with it: reproduce the
Adjust & Visualize blank-prompt/wrong-reference bug, note the time, then:
`grep "visual:visualize" logs/talemate-debug.jsonl` → filter by the flow id →
the choose_subject / attach_character_references / distill_prompt decisions
and every LLM call are all there.
