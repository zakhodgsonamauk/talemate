# Director trust and autonomy levers — specification

## Goal

Two failure classes surfaced in live play (2026-07-31 session, gambling-den scene):

1. **Trust**: the director chat in No Spoilers mode still leaks story structure (arc/beat
   position, undisclosed character roles, spelled-out outcome menus) and defers steering
   decisions to the player instead of deciding. Prompt-only self-censorship demonstrably
   fails — the model's own ANALYSIS reasoned about nospoilers and then leaked "mid-Beat 3"
   in the same reply (captured in backend.log 2026-07-31 20:29).
2. **Passivity/stall**: with Scene Direction + Guide Scene enabled the story still failed
   to adjudicate player-initiated stakes (repeated peaceful-negotiation attempts were
   "vibed past" until the player escalated to the director chat manually). The director
   needs configurable levers for how assertively it runs the story, and it needs to keep
   getting turns during manual play.

This track hardens the no-spoilers contract with a mechanical redaction gate, makes
director write-actions trustworthy (verbatim storage, retries), and adds autonomy /
malleability levers to scene direction.

## Relationship to the `autonomous-story` track

`conductor/tracks/autonomous-story/` owns the deep scene-loop rework (AI-turn cap /
EMERGENCY BREAK, hand-back triggers, loop-bound plans, branch hints, background director).
This track must NOT rewire those paths. The single scene-loop change here (R9) is a
minimal OR-gate widening, recorded in `conductor/decision-log.md` for the autonomous-story
implementer.

## Requirements

### R1 — No Spoilers contract v2 (prompt layer)

`src/talemate/prompts/templates/director/chat-instructions.jinja2` nospoilers block
(already rewritten once this session — extend, don't replace wholesale):

- Fold decisiveness in: in nospoilers mode the director NEVER asks the player to choose
  between story outcomes and never asks how hands-on to be. When the player requests
  steering ("progress it how you want"), it decides silently, acts, and replies vaguely.
- Ban, explicitly, the three observed leak shapes:
  a. arc/beat position or structure ("we're mid-Beat 3", "the full arc is mapped" is fine
     to *know* but not to enumerate position/counts),
  b. undisclosed narrative roles ("that Weequay is the pivot point / higher authority"),
  c. outcome menus (describing two or more possible futures and asking the player to pick).
- Add the observed failure as a BAD few-shot with a GOOD rewrite (short).

### R2 — Redaction gate (mechanical layer)

Every director chat reply in a nospoilers chat passes through a second LLM call before
display:

- Hook: `src/talemate/agents/director/chat/mixin.py::chat_generate_response` — both
  append sites where `parsed_response` / `follow_parsed` become a `DirectorChatMessage`
  (currently ~lines 610-615 and ~706-711; locate by `DirectorChatMessage(message=`).
- Schema: add `unredacted_message: str | None = None` to `DirectorChatMessage`
  (`src/talemate/agents/director/chat/schema.py`). The director's own context building
  (`chat_history_for_prompt` and the serialize path in
  `src/talemate/agents/director/action_core/utils.py::serialize_history` /
  `chat.jinja2` history rendering) must use the unredacted text when present so the
  director keeps planning continuity; the frontend/user only ever receives the redacted
  `message`.
- New template `src/talemate/prompts/templates/director/redact-spoilers.jinja2`:
  input = the draft reply + scene history budget; task = "rewrite for the player: remove
  anything the player has not already witnessed in the scene; keep acknowledgments,
  questions, tone; one short reply". Use the director's client. Follow the pattern of
  `director/supervise-narration.jinja2` (same session, same author) for context includes
  and extractor comment style.
- Gate runs only when `chat.mode == "nospoilers"`. On any exception, fall back to the
  unredacted text (never block the reply).
- Do not modify the frontend; redacted text flows through the existing message payloads.

### R3 — Verbatim world-entry path

`src/talemate/agents/director/modules/instruct-world-updates.json`: the `new_world_entry`
and `update_world_entry` callbacks currently route everything through
`agents/creator/ContextualGenerate` (creative rewrite, 512 tokens) — the director cannot
store literal content (a story-arc table came back as scenery prose in play).

Add an optional `verbatim_content` focal Argument to both callbacks. When set, bypass
ContextualGenerate and feed the content directly to `scene/worldstate/SaveWorldEntry`
(new) / `context_id/ContextIDSetValue` (update). Use `core/RSwitchAdvanced` +
`data/string/StringCheck` or `validation/ValidateValueIsNotSet` patterns already present
in that module. Update the callback Metadata instructions so the director knows when to
use it ("when the user asks to store exact content, pass it verbatim here").

Node-graph JSON editing is error-prone: after editing, verify with `json.load`, then load
the node registry in the project venv and extract the callback descriptors
(`talemate.agents.director.action_core.gating.extract_callback_descriptors`) to prove the
graph still parses and the new argument appears.

### R4 — Retry rollout

Set `"retries": 1` on the `focal/Focal` node in each director instruct-* write module
(instruct-world-updates.json already done this session). Apply to:
instruct-character-updates, instruct-character-creation, instruct-character-changes,
instruct-character-config-updates, instruct-gamestate-updates, instruct-history-updates,
instruct-story-config-updates, instruct-world-updates (verify), manage-plan — wherever a
`focal/Focal` node with `"retries": 0` exists. Read-only/query modules keep 0.
The retry infrastructure was fixed this session (`game/focal/__init__.py` — retry now
preserves `prompt=` and only counts executed calls as progress).

### R5 — Directorial stance lever

New config on the director's `scene_direction` action
(`src/talemate/agents/director/scene_direction/mixin.py::add_scene_direction_actions`):
`stance`, choices `hands_off` / `nudge` / `drive` / `showrunner`, default `nudge`.
Inject a stance-specific instruction block into the scene-direction prompt
(`src/talemate/prompts/templates/director/scene-direction-instructions.jinja2` — follow
how `custom_instructions` and `turn_balance` already flow through `extra_vars` in
`_direction_generate`). Stance scales: how readily the director acts vs yields, and how
many of its `max_actions_per_turn` it should typically spend.

### R6 — Adjudication policy

Config on `scene_direction`: `adjudication` bool (default on) + `adjudication_window`
number (default 3). Prompt block: "when the player attempts something with stakes
(negotiation, persuasion, deception, a plan), resolve it within N of their turns —
success, partial success, or failure, using dice if genuinely uncertain — do NOT let it
simmer unresolved." This is the direct fix for the gambling-den stall.

### R7 — Stale-beat pressure

When an active plan exists and its current task/beat has not advanced for N rounds
(config `stale_beat_rounds`, default 6, 0=off), inject an escalating note into the
direction prompt ("the current beat has been static for N rounds — advance or resolve
it this turn"). Implementation: in `_direction_generate`, compare plan task statuses
against a remembered snapshot in director scene state (`self.get_scene_state` /
`set_scene_states` — the pattern used for `disabled_sub_actions` and chats). Explore
`src/talemate/agents/director/plan/schema.py` and `plan/util.py::get_plan` for status
fields before choosing the mechanism; keep it cheap (no LLM calls).

### R8 — Direction frequency

Config on `scene_direction`: `frequency` number, default 1 (= every eligible round),
N = take a direction turn only every Nth eligible round. Enforce at the top of
`direction_execute_turn` (count scene messages of type character/narrator since the last
direction turn — `direction_get()` messages carry types; or use a round counter in scene
state). `always_on` calls (websocket-triggered manual runs) bypass the frequency check.

### R9 — Direction during manual play

In `src/talemate/game/engine/nodes/modules/scene/scene-loop.json` the SceneDirection
node's `state` input is gated by an AND Router: `Get Scene State.auto_progress` AND NOT
`shared.skip_to_player`. Widen to (`auto_progress` OR `Get Scene Intent.direction_always_on`)
AND NOT `skip_to_player`. `GetSceneIntent` already outputs `direction_always_on` and it
is already wired to the SceneDirection node's `always_on` input — reuse that same output
into a new `core/ORRouter` (two already exist in the file as wiring examples). Record the
change in `conductor/decision-log.md` referencing autonomous-story R2/R3.

### R10 — Pacing dial

Config on `scene_direction`: `pacing`, choices `simmer` / `steady` / `escalating`,
default `steady`. Injected into the scene-direction prompt and, when Guide Scene is
enabled, into the guidance templates (`director/guide-narration*.jinja2` /
`guide-conversation.jinja2` — via `extra_vars` or agent context state, follow how
`director__narrator_guidance` state flows). Note for future: autonomous-story R4 adds
per-SceneType pacing; this dial is the fallback when the scene type does not specify.

### R11 — Agency guardrail

Config on `scene_direction`: `player_agency`, choices
`strict` ("never author the player character's actions, dialogue or decisions"),
`consequences` (default — "never author their choices, but narrate the world's response
to them decisively"), `assist` ("may act for the player character when the scene has
stalled"). Injected into both scene-direction and chat instructions.

## Out of scope

- Everything in autonomous-story's plan (turn cap default, hand-back triggers, loop-bound
  plans, branch hints, abstraction bridge internals, background director).
- Frontend changes.
- Restarting the user's backend (they are mid-session; static verification only).

## Acceptance criteria

- AC1: nospoilers block contains decisiveness + the three leak-shape bans + few-shot; renders.
- AC2: in a nospoilers chat, the stored `DirectorChatMessage` carries both texts; the
  prompt history renders the unredacted text; the emitted payload carries the redacted
  text. Unit-testable without a live LLM by stubbing the redaction call.
- AC3: callback descriptors for world updates list `verbatim_content`; graph loads.
- AC4: every director write-module focal node has `retries: 1`.
- AC5-AC11 (levers): each config appears in `DirectorAgent.init_actions()` output with
  correct default; each renders its block into the scene-direction prompt template when
  set; `.venv/Scripts/python.exe -m pytest tests/ -k "director or focal"` passes.
- AC12: scene-loop.json change — JSON loads, gate widened, decision-log entry written.
- Periodic second-opinion reviews were run (see plan) and blocking findings addressed.
