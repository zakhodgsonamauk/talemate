# Director trust and autonomy levers — implementation plan

**Spec**: `conductor/tracks/director-trust-and-levers/spec.md` (read it fully first — it
carries file paths and designs from a live debugging session; do not re-derive them).

Session context the executor must know:
- The focal retry/error-surfacing fixes, the nospoilers v1 contract, narration
  supervision, and the memory ghost fixes were all committed earlier today (git log
  2026-07-31: a697399..b541801). Build on them.
- The user's backend is running and mid-play-session. NEVER restart or kill it. Verify
  statically: `ast.parse` / `json.load` / jinja2 parse / pytest / venv imports via
  `.venv/Scripts/python.exe`.
- Windows + Git Bash. Project venv at `.venv/Scripts/python.exe`. Tests:
  `.venv/Scripts/python.exe -m pytest tests/ -q -k "..."`. Known pre-existing flakes:
  `test_chat_list_sorted_by_created_at`, `test_falls_back_to_most_recent` (time-tick
  sorting) — not yours.
- Commit per phase with conventional messages ending
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. The code-review-graph
  post-commit hook prints a cosmetic UnicodeEncodeError traceback — ignore it.

## Review cadence (user-mandated)

After each phase, run a fast second-opinion review over that phase's diff using the
second-opinion MCP tools (prefer `start_featherweight_review`; poll with `check_panel` /
`check_investigation` as the tool result indicates). Address findings graded blocking;
log non-blocking findings in the track's `review-notes.md`. Do not proceed to the next
phase with an unaddressed blocking finding.

## DAG

Phase 1 (T1,T2) -> Phase 2 (T3,T4) -> Phase 3 (T5..T11 parallel-safe) -> Phase 4 (T12) -> Phase 5 (T13)
T3 and T4 are independent of T2. Within Phase 3, all tasks touch
`scene_direction/mixin.py::add_scene_direction_actions` and the same jinja2 template —
execute sequentially in one worker to avoid merge conflicts, but they are logically
independent levers.

---

## Phase 1 — No Spoilers trust

### [ ] T1 — Contract v2 (spec R1)

`src/talemate/prompts/templates/director/chat-instructions.jinja2`, nospoilers block.
Keep the existing NEVER/MAY structure and the ACTIONS carve-out; add decisiveness, the
three leak-shape bans, and one BAD/GOOD pair built from the observed "mid-Beat 3 /
Weequay pivot / two-path menu" failure (generalize names). Verify: jinja2 env parse.

### [ ] T2 — Redaction gate (spec R2)

1. Schema: `unredacted_message` field on `DirectorChatMessage`.
2. New method on `DirectorChatMixin` (`chat/mixin.py`), e.g.
   `chat_redact_message(chat, text) -> str`: renders `director.redact-spoilers` via
   `Prompt.request` with the director client; returns redacted text; exceptions -> log +
   return original.
3. Hook both `DirectorChatMessage(message=...)` append sites in
   `chat_generate_response`: when `mode == "nospoilers"` and the redacted text differs,
   store `message=redacted, unredacted_message=original`.
4. History for prompt: wherever chat history is serialized for the director's own prompt
   (`chat_history_for_prompt` -> `action_utils.serialize_history` -> `chat.jinja2`),
   prefer `unredacted_message or message`. Trace the actual serialize path before editing.
5. New template `director/redact-spoilers.jinja2` — mirror the context-include style of
   `director/supervise-narration.jinja2` but much lighter: scene snapshot + recent
   history only; task: remove anything not already witnessed by the player.
6. Test (new, `tests/test_director_redaction.py` or extend existing director tests):
   stub `chat_redact_message`, run the append path, assert both fields stored and the
   prompt-history serializer picks the unredacted text. Follow existing async test
   patterns (`tests/prompts/test_director_templates.py` has the fixtures to copy).

**Phase gate: featherweight second-opinion review of the Phase 1 diff.**

## Phase 2 — Trustworthy writes

### [ ] T3 — Verbatim world-entry path (spec R3)

The risky one. Study the existing graph first:
`python -c "import json; d=json.load(open('src/talemate/agents/director/modules/instruct-world-updates.json'))"`,
dump nodes/edges around `DEF new_world_entry` / `DEF update_world_entry` (the
DefineFunction subgraph pattern is visible in instruct-character-updates.json too).
Add `verbatim_content` focal/Argument to both callbacks; RSwitch: set -> bypass
ContextualGenerate straight to SaveWorldEntry / ContextIDSetValue; unset -> existing
path. New node UUIDs must be fresh (uuid4). Update focal/Metadata instructions +
examples for both callbacks.
Verify: json.load; then in venv:
`from talemate.agents.director.action_core.gating import extract_callback_descriptors`
(requires node registry init — see how `tests/` bootstrap node modules, e.g. via
`_node_test_helpers.py`) or minimally re-run whatever module-load path the tests use.
If registry-level verification proves impractical, verify by structural assertions on
the JSON (argument present, edges reference existing node ids, every new edge socket
name exists on its node's known socket list).

### [ ] T4 — retries: 1 rollout (spec R4)

Script the sweep: for each listed instruct-* module, find `focal/Focal` nodes with
`"retries": 0`, set 1. Skip query/read-only modules (query-*.json,
director-agent-retrieve-context.json). Print a table of module -> changed. json.load
each.

**Phase gate: featherweight second-opinion review.**

## Phase 3 — Autonomy and malleability levers (spec R5-R8, R10, R11)

All configs go in `add_scene_direction_actions` (`scene_direction/mixin.py`), property
helpers follow the existing `direction_*` pattern, values flow through `extra_vars` in
`_direction_generate` into `scene-direction-instructions.jinja2`. One worker, sequential
tasks, single commit at phase end is acceptable.

### [ ] T5 — Stance (R5): config + prompt block with per-stance instruction text.
### [ ] T6 — Adjudication (R6): config (bool + window) + prompt block.
### [ ] T7 — Stale-beat pressure (R7): plan-status snapshot in scene state, rounds
counter, escalating prompt note. Explore plan schema first; keep zero-LLM-cost.
### [ ] T8 — Frequency (R8): config + early-return in `direction_execute_turn`
(`always_on=True` bypasses). Careful: the early-return must return `([], False)` shape.
### [ ] T9 — Pacing dial (R10): config + direction prompt injection; guide templates
only if a clean injection point exists (agent context state), otherwise direction-only
and note it.
### [ ] T10 — Agency guardrail (R11): config + blocks in BOTH
`scene-direction-instructions.jinja2` and `chat-instructions.jinja2`.

Tests: extend director tests to assert each new config exists with its default in
`DirectorAgent.init_actions()`, and at least one template-render test per prompt block
(the template test fixtures in `tests/prompts/test_director_templates.py` show how to
render director templates with fake vars).

**Phase gate: featherweight second-opinion review.**

## Phase 4 — Loop gate

### [ ] T12 — Direction during manual play (spec R9)

`scene-loop.json`: widen the AND Router gate to
(`auto_progress` OR `GetSceneIntent.direction_always_on`) AND NOT `skip_to_player`.
Trace the exact node ids first (the AND Router feeding `Scene Direction.state`; a
`core/ORRouter` node type is already used twice in the file — copy its shape). Fresh
uuid4 for the new node. json.load + structural assertions. Write the decision-log entry
referencing autonomous-story R2/R3.

**Phase gate: featherweight second-opinion review.**

## Phase 5 — Track close

### [ ] T13 — Full verification + report

- `.venv/Scripts/python.exe -m pytest tests/ -q -k "director or focal or template"`
- jinja2-parse every touched template; json.load every touched module.
- Confirm every AC in the spec, one line each, in `conductor/tracks/director-trust-and-levers/report.md`.
- Update `conductor/tracks.md` row + `metadata.json` status.
- Final second-opinion review over the whole track diff (fast/featherweight).
- Leave a note in the report: user must restart the backend to load module/config
  changes (do NOT restart it yourself), then live-test: nospoilers chat steering
  question, verbatim arc storage, stance=drive + adjudication in the gambling-den scene.
