# Director trust and autonomy levers — execution report

Executed 2026-07-31 against the spec/plan authored the same day from live debugging.
All verification was static (the user's backend was running and mid-play-session
throughout; it was never touched).

## Commits

| Commit | Phase | Content |
|--------|-------|---------|
| 5ffdddf | 1 | nospoilers contract v2 + mechanical redaction gate + tests |
| a1d15e3 | 1 (review follow-up) | prefill design documented on REDACTED_PATTERN + untagged-completion test |
| 2cf2a84 | 2 | verbatim world-entry path (instruct-world-updates.json) + retries=1 on 7 write modules |
| ed32a97 | 3 | six autonomy levers (configs, prompt blocks, 33 tests, regenerated baselines) |
| 1908f7c | 4 | scene-loop OR gate (direction during manual play) + DECISION-005 |

## Acceptance criteria

- **AC1** (nospoilers v2) — DONE. `chat-instructions.jinja2` nospoilers block now has the
  DECISIVENESS rule, explicit bans on the three observed leak shapes (beat position,
  undisclosed roles, outcome menus), and a second BAD/GOOD few-shot generalized from the
  live "mid-Beat 3 / pivot / two-path menu" failure. jinja2-parses; renders in tests.
- **AC2** (redaction gate) — DONE. `DirectorChatMessage.unredacted_message` added; both
  append sites in `chat_generate_next` route through `_chat_build_director_message`;
  in nospoilers mode the reply passes through the new `director.redact-spoilers`
  template with the director's client; displayed/stored `message` is the redacted text,
  prompt history (`chat_history_for_prompt`) swaps in the unredacted text via
  `model_copy` (stored message never mutated); any gate failure falls back to the
  original text. 8 tests in `tests/test_director_redaction.py`, no live LLM needed.
- **AC3** (verbatim path) — DONE. `verbatim_content` argument on `new_world_entry` and
  `update_world_entry`. Non-empty value routes the exact text to SaveWorldEntry /
  ContextIDSetValue and starves ContextualGenerate's required `state` input so the
  512-token creative rewrite is skipped entirely; unset/empty keeps the old path
  byte-for-byte. Confirm gates preserved in both branches; the update confirm diff
  works in both. Registry-level verification: full node-definition import, module graph
  instantiation (79 nodes), `verbatim_content` discovered as a real FunctionArgument of
  both callbacks via the same `get_nodes_connected_to` mechanism the runtime uses, every
  edge socket checked against its live node. (The plan's `extract_callback_descriptors`
  check runs at the parent-action level — this module is a SubAction graph, so the
  argument-discovery check above is the meaningful equivalent; descriptor examples carry
  `verbatim_content`.)
- **AC4** (retries) — DONE. retries 0→1 on the focal/Focal node of instruct-avatar-generation,
  instruct-character-changes, instruct-character-config-updates, instruct-character-updates,
  instruct-history-updates, instruct-story-config-updates, manage-plan;
  instruct-world-updates was already 1. instruct-character-creation /
  instruct-gamestate-updates / instruct-character / instruct-narrator have no focal/Focal
  node (they delegate). Read-only modules (query-*, director-agent-retrieve-context) and
  director-action-* orchestrators deliberately keep 0.
- **AC5** (stance, R5) — DONE. Config default `nudge`, choices hands_off/nudge/drive/
  showrunner; per-stance block in scene-direction prompt; config + property + render tests.
- **AC6** (adjudication, R6) — DONE. `adjudication` bool default on + `adjudication_window`
  default 3; prompt block orders resolution of player stakes within the window.
- **AC7** (stale-beat pressure, R7) — DONE. `stale_beat_rounds` default 6 (0=off);
  zero-LLM-cost snapshot tracker in agent scene state
  (`_direction_compute_stale_beat_pressure`, active plan = executing else ready-with-
  pending); escalation payload rendered as a STALE BEAT PRESSURE block. 5 unit tests.
- **AC8** (frequency, R8) — DONE. `frequency` default 1; `_direction_frequency_gate`
  counter in scene state; gated rounds return `([], False)` from
  `direction_execute_turn`; `always_on` (manual/websocket) bypasses. 5 unit tests.
- **AC9/AC10** (pacing, R10) — DONE. `pacing` default steady; simmer/escalating blocks in
  the scene-direction prompt and in `guide-narration.jinja2` / `guide-conversation.jinja2`
  (passed as a prompt var from `guide.py` — the "clean injection point" existed, so guide
  templates are covered; steady renders nothing, keeping default output unchanged).
- **AC11** (player agency, R11) — DONE. `player_agency` default consequences; reworks the
  user-controlled-character block in scene-direction instructions and adds a Player
  agency block to chat instructions (wired via chat extra_vars). Render tests for all
  three values in both templates.
- **AC12** (R9, direction during manual play) — DONE. New `core/ORRouter` in
  scene-loop.json: gate is now (auto_progress OR GetSceneIntent.direction_always_on)
  AND NOT skip_to_player. Verified by graph instantiation + live-socket wiring
  assertions. Recorded as DECISION-005 in `conductor/decision-log.md`, referencing the
  autonomous-story track boundary.
- **Reviews** — Ran after every phase plus a final whole-track pass (featherweight,
  second-opinion MCP). Findings and dispositions in `review-notes.md`; the only
  "blocking" findings (Phase 1 prefill/regex) were analyzed as a misread of the
  codebase's prefill-coercion pattern — the suggested fix would make the spoiler gate
  fail open — and were addressed with documentation plus the reviewer's missing test
  case instead. Later phases reported no blocking issues.

## Verification summary (all static)

- `pytest tests/ -k "director or focal or template"`: 1159 passed. Remaining failures
  are pre-existing: the two known time-tick flakes
  (`test_chat_list_sorted_by_created_at`, `test_falls_back_to_most_recent`) and
  `tests/prompts/test_template_section_validation.py` (both tests) which fail on a clean
  checkout too — the validator reads templates without `encoding="utf-8"` and an editor
  template contains a non-cp1252 byte (see review-notes.md for the one-line fix).
- jinja2-parse of all 5 touched templates; `json.load` of all 9 touched module JSONs.
- Director prompt baselines regenerated (18 files); diffs are exactly the new
  default-config blocks (nudge stance, adjudication window 3, consequences agency,
  chat Player-agency block).
- New tests added: 8 (redaction) + 15 (levers) + 18 (template blocks) = 41.

## What was NOT done / notes

- `director-action-*` orchestration modules keep `retries: 0` — outside the spec's R4
  list; flagged in case a follow-up wants them.
- The guide pacing block has no direct render test (the guide templates need a heavy
  var harness); it is covered by jinja2 parse + a unit test asserting the pacing var
  reaches both guide prompts.
- Nothing in the autonomous-story track's scope was touched; the single scene-loop
  change is the minimal OR gate (DECISION-005).

## User action required (do NOT restart the backend from automation)

**Restart the talemate backend when convenient** — node-module JSON, agent configs and
templates load at startup. Then live-test:

1. **nospoilers steering**: in a No Spoilers chat, ask "things feel stuck — progress it
   however you want". Expect: no beat/arc position, no hidden-role labels, no
   outcome menus, no "how hands-on should I be?" — a vague acknowledgment plus actions.
   The stored chat now keeps the unredacted text internally, so follow-up planning
   should stay coherent.
2. **verbatim arc storage**: in a director chat, ask it to store an exact story-arc
   table as a world entry ("store this word-for-word"). Expect the confirm dialog to
   show the literal content and the saved entry to match byte-for-byte (no scenery
   prose rewrite).
3. **gambling-den stall**: enable Scene Direction, set stance=drive (Director agent →
   Scene Direction), leave adjudication on, and replay a stakes attempt (peaceful
   negotiation). Expect resolution within 3 player turns.
4. **manual-play direction**: with auto-progress OFF and the scene intent's
   direction_always_on set, confirm the director still takes direction turns between
   your manual turns (and that `frequency` > 1 spaces them out).
