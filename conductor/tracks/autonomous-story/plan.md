# Autonomous story — implementation plan

**Spec**: `conductor/tracks/autonomous-story/spec.md`
**Design**: `docs/fork/autonomous-story-design.md`

Nine phases, ordered so each is independently playable and the cheapest diagnostics come
first. Phases 1-4 need no cloud model and do not touch the plan system.

Tests run with `pytest` (see `tests/test_visual_anchor.py`, `tests/test_visual_reference.py`
for the established style). Node-graph changes are not unit-testable end to end; those tasks
verify by rendering the graph and by play, and say so explicitly rather than claiming coverage
they do not have.

---

## Phase 0 — Baseline (AC11 prerequisite)

### [ ] T0 — Capture the out-of-character baseline before changing anything

AC11 compares against "the current baseline", but T1 changes behaviour, so the baseline must be
captured first or it is gone.

1. Play a scene at current settings. Count out-of-character actions — every time you tell the
   narrator to narrate, pick who speaks, or ask the story to progress.
2. Record the count, the scene, and how many beats it covered in `conductor/decision-log.md`.

**Acceptance**: a recorded baseline exists that AC11 can be measured against.
**Verification**: play. Human-only.

---

## Phase 1 — Context budget (R1, AC1)

### [ ] T1 — Raise the local context budget as far as VRAM allows

Config only, no code. Bounded by hardware, so the ceiling is a measurement, not a target.

Measured headroom: RTX 4080, 16376 MiB total, 7522 MiB in use — roughly 8.8 GB free with the
model loaded. Rocinante-X-12B is Mistral-Nemo-based: 40 layers, 8 KV heads, 128 head dim, fp16,
so KV cache costs roughly 160 KB per token — about 1.3 GB at 8k, 2.6 GB at 16k, 5.2 GB at 32k.
16384 should be comfortable; 32768 is plausible but verify rather than assume.

1. Raise `clients.Ollama.max_token_length` from 8192 to 16384. Watch VRAM during play.
2. If it holds, try 32768. If generation slows sharply or the model offloads to CPU, step back.
3. Record the working ceiling and whether beat quality improved, in `conductor/decision-log.md`.

`num_ctx` is set **per client** (`ollama.py:218`), so this ceiling constrains only the local
model. A cloud director (T21) can carry a far larger context at zero VRAM cost — that is the real
fix for the director's context starvation, and the reason not to over-invest here.

**Acceptance**: AC1. A recorded working ceiling plus a before/after observation.

---

## Phase 2 — Unblock the turn cap (R2, AC2, AC3)

### [ ] T2 — Add `max_ai_turns` to game config

1. Add `max_ai_turns: int` to `General` (`src/talemate/config/schema.py:200`), defaulting
   materially higher than 3 — propose 12.
2. Expose it on `Scene` alongside `auto_progress` (`tale_mate.py:455`) and in the scene status
   payload (`tale_mate.py:1419`).
3. Failing test first: assert the default and that a configured value round-trips.

**Acceptance**: AC2 (config half). Test passes.

### [ ] T3 — Expose `max_ai_turns` as a `GetSceneState` output

1. Add `max_ai_turns` output to `GetSceneState` (`game/engine/nodes/scene.py:76-96`),
   following the existing `auto_progress` pattern exactly.
2. Test that the node emits the configured value.

**Acceptance**: node output present and correct. **Deps**: T2.

### [ ] T4 — Rewire the cap in `scene-loop.json` — behaviour-neutral

Deliberately **keeps the default at 3** so this task changes no behaviour and can be verified in
isolation. Raising the default is T4b, which waits for hand-back triggers.

1. In `src/talemate/game/engine/nodes/modules/scene/scene-loop.json`, redirect
   `Compare.b` (`7b31eda5`) to read from `GetSceneState.max_ai_turns` instead of the
   `data/number/Make` literal `d5a5c1d5` (titled `MAX AI TURNS - 3`, value `"3"`).
2. Retitle the watch node so `EMERGENCY BREAK` reads as a backstop, not the normal path.
3. Additive where possible — leave unrelated nodes untouched to keep the upstream diff
   readable.

**Acceptance**: AC2. Graph loads without error; the comparison reads config; play is
indistinguishable from before.
**Deps**: T3. **Verification**: graph load plus play, not unit test.

### [ ] T4b — Raise the cap default

Gated behind the hand-back triggers, because until they exist the only brake on the story is a
12B's judgement. T8 (direct address) is the safety-critical one and is done; T9 turned out to be
a design decision about dice rather than a brake, so it no longer gates this.

1. Raise `General.max_ai_turns` default to 12.
2. Play. Confirm the story runs several beats without dragging the player in, and still hands
   back when it should.

Revert is a single quick setting if 12 proves too loose.

**Acceptance**: AC2, AC3 together. **Deps**: T4, T8. **Verification**: play.

### [ ] T5 — Verify the director's yield still returns control

The design doc's node-ID trace says the cap (`SetState` `f8fe5739`) and the director's yield
(`ConditionalSetState` `88c066b8`) are independent writers to `shared.skip_to_player`, so this
should hold. Confirm it in play rather than trusting the trace.

1. Play with the cap raised until the director calls `direct_scene.yield_to_user`.
2. Confirm control returns to the player at that point and not merely at the cap.

**Acceptance**: AC3. **Deps**: T4.

### [ ] T6 — Quick-setting toggle for the cap

Follow `server/quick_settings.py:42` (the `auto_progress` case) so the cap is adjustable
without editing YAML.

**Acceptance**: setting changes take effect without restart. **Deps**: T2.

---

## Phase 3 — Hand-back triggers (R3, AC4)

### [ ] T7 — Direct-address detection

A cheap local check, no LLM call per beat.

1. Failing tests first, covering: a character naming the player's character; a question
   directed at them; an action performed upon them; and the negative cases — two NPCs talking
   about the player in the third person must **not** trigger.
2. Implement against `CharacterMessage` and the player character name. Mind the
   `movie_script` conversation format (`conversation.generation_override.format`), which
   shapes how names appear.
3. Expose as a node output usable by the loop.

Accept that this will be imperfect. Prefer false negatives to false positives: a missed
detection is covered by the T4b cap backstop and reads as the story being slightly slow to
notice you, whereas a false positive hands control back constantly and recreates the original
complaint. Record the observed miss rate rather than claiming reliability.

**Acceptance**: AC4. Tests pass including negatives.

### [ ] T8 — Wire direct address into the loop

Feed T7's signal into the `shared.skip_to_player` path — the same channel the cap and the
director's yield already use — so all three hand-back routes converge on one flag.

**Acceptance**: direct address returns control in play. **Deps**: T7, T4.

### [ ] T9 — Decision-point gating — **needs a decision, not code**

Investigated during execution. Most of this is already satisfied, and the remainder is a design
choice rather than a defect:

- **Choices already gate correctly.** `player_turn_start` fires *inside* `WaitForInput`
  (`nodes/scene.py:984`), immediately before the wait, and Dynamic Actions hangs off that signal
  (`generate_choices.py:117-121`). Choices therefore only ever appear when the player already
  holds the turn. Nothing to gate.
- **`never_auto_progress` is dead config.** The "Never Auto Progress on Action Selection" toggle
  (`generate_choices.py:76`, property at `:106`) has no consumer anywhere in `src/` or the
  frontend. Either give it meaning or remove it — leaving a toggle that does nothing is worse
  than not having it.
- **Dice are the open question.** `roll_dice` is currently the director's to call autonomously.
  Making a roll stop and ask the player is a gameplay decision with no single right answer:
  always ask, ask only when the player's own character is the actor, or never ask.

No longer blocks T4b — the safety-critical hand-back is T8, which is done. The cap backstop
covers the rest.

**Decision taken (2026-07-30, user)**: ask **only when it is the player's own character acting**.
Another character picking a lock is the director's roll and resolves silently; the player picking
one is theirs and stops to ask.

### [ ] T9a — Dice confirmation for the player's own rolls

Every primitive needed already exists; this is a node-graph build across two module files.

**Problem**: `roll_dice` has no idea whose action it resolves. Its focal arguments are `min`,
`max`, `reason`, `modifier`, `show_to_user` (`director-action-gameplay.json`). Whose roll it is
lives only in the free-text `reason` ("Elmer picks the lock"), which is not reliable to parse.

**Design**:

1. In `director-action-gameplay.json`, add a `focal/Argument` node `character` (`typ: str`) with
   instructions: the name of the character whose action this roll resolves, or blank when it is
   not tied to one character. Wire it into `focal/Callback: roll_dice` alongside the existing
   arguments.
2. Inside the `DEF roll_dice` function body, before invoking
   `agents/director/gameplay/rollDice`, compare `character` against
   `scene/GetPlayerCharacter`. Names only — no LLM call.
3. When it matches, build a `ux/BuildChoiceElement` titled from `reason` with choices
   `["Roll", "Skip"]` and emit via `ux/EmitElement`. Choice elements are awaitable by design:
   `EmitElement` blocks until the user selects or cancels, then returns `value`, `cancelled` and
   `timed_out` (`ux.py:609-631`).
4. Route the result: `Roll` proceeds into `rollDice`; `Skip`, `cancelled` or `timed_out` returns
   without rolling. Force `show_to_user` true on the player's own rolls — being asked and then
   not shown the outcome would be worse than not being asked.
5. When `character` is blank or another character, behaviour is unchanged: the director rolls
   silently as today.

**Risk**: this is hand-authored JSON in a graph with known traps — `Coallesce` uses `is_truthy`
despite its docstring, `ORRouter` deactivates its inactive output, and `core/Stage` nodes are
sinks that declare membership rather than gates. Endpoint and registry validation catches
structural errors but not runtime behaviour, so this needs a play-test. Fallback if it misbehaves
is to add `roll_dice` to the scene's `disabled_sub_actions` denylist, which needs no code change.

**Acceptance**: a roll for the player's character asks first and is shown; a roll for anyone else
resolves silently as before. **Deps**: T8. **Verification**: endpoint validation plus play.

### [ ] T9b — Resolve `never_auto_progress`

Dead config: `generate_choices.py:76` defines it, `:106` exposes it, nothing reads it in `src/`
or the frontend. Either implement it or remove it. Undecided — a toggle that silently does
nothing is worse than no toggle.

**Acceptance**: implemented or removed, with the choice recorded.

---

## Phase 4 — Scene-dependent pacing (R4, AC5)

### [ ] T10 — Pacing fields on `SceneType`

1. Add turn mode (wait / push), idle threshold, and an optional per-scene beat cap to
   `SceneType` (`scene/schema.py:62`).
2. Failing tests first: defaults preserve today's behaviour; existing scene files without the
   fields still load.

**Acceptance**: no regression on existing scenes. Tests pass.

### [ ] T11 — Wire `WaitForInput.abort_condition`

`WaitForInput` (`game/engine/nodes/scene.py:781`) already exposes `abort_condition`, evaluated
each poll, raising `AbortWaitForInput` → `LoopContinue`. Nothing connects it.

1. Build the condition chain from T10's pacing fields and connect it in `scene-loop.json`.
2. Push-mode scenes advance when the player is idle; wait-mode scenes never abort.

**Acceptance**: AC5. **Deps**: T10, T4. **Verification**: play, both modes.

### [ ] T12 — Pacing in the scene-type editor UI

Surface T10's fields so pacing is tunable without hand-editing scene JSON. Files:
`talemate_frontend/src/components/TemplateSceneType.vue` (the scene-type editor),
`WorldStateManagerSceneDirection.vue`, and `talemate_frontend/src/utils/templateMappings.js`.

**Acceptance**: fields editable and persisted. **Deps**: T10.

---

## Phase 5 — Loop-bound plans (R5, AC6)

**Scope corrected after plan evaluation (F1).** Beat execution already exists —
`plan/nodes.py:13-14`: "Beat execution is handled by the existing `direct_scene` FOCAL action.
Plan state is injected into the chat context when a plan exists." `direct_scene` is already
enabled and already reachable from a scene-direction turn.

The real gap is narrower than originally written: plan state reaches the **chat** prompt
(`chat/mixin.py:560`, `extra_vars["scene_plan"] = get_plan(self.scene, plan_id)`) but not the
**scene-direction** prompt, which has no plan section. Do not build a parallel beat-execution
path.

### [ ] T13 — Let a plan exist outside a chat

Storage already exists and is reusable — `save_plan` / `get_plan` against
`scene.agent_state["director"]["plans"]` (`plan/util.py:20-53`). Three things bind plans to
chats and each will break a loop plan:

1. `cleanup_orphaned_plans` (`util.py:130-153`) deletes every plan not referenced by a chat's
   `plan_id`. A loop plan is orphaned by definition and will be silently garbage-collected.
   Exempt loop plans, or give them a reference that survives cleanup.
2. `resolve_plan_id` (`util.py:107-112`) resolves only via `director_chat_context`, absent
   during a loop turn.
3. `get_active_plan` (`util.py:115-120`) has the same dependency.

Failing tests first, and one of them must assert a loop plan survives a `cleanup_orphaned_plans`
call — that is the trap this task exists to close.

**Acceptance**: a loop-scoped plan persists across turns and survives cleanup. Tests cover both.

### [ ] T14 — Inject plan state into the scene-direction prompt

1. Add a plan section to `src/talemate/prompts/templates/director/scene-direction.jinja2`,
   mirroring how chat receives `scene_plan`.
2. Extend the scene-direction instructions so the director works through pending beats and
   calls `CompleteTask` as it goes.
3. State the beat-kind mapping explicitly. `narration` → narrator direction, `dialogue` →
   actor direction. `action`, `transition` and `reveal` must be assigned to a concrete path
   before implementation, not left to the model to guess.
4. Note the constraint: `Beat.type` is also the pydantic union discriminator
   (`TaskType`, `plan/schema.py:107`, with `Task.type` as `Literal["task"]`), so new beat kinds
   are not a free addition. `_validate_task` (`plan/nodes.py:57`) discriminates "based on fields
   present" and is fragile — check it if the shape changes.

**Acceptance**: AC6 — the rendered scene-direction prompt contains pending beats, and the
director completes them via existing actions. Verified by inspecting the prompt.
**Deps**: T13.

### [ ] T15 — Director writes a plan from a scene-direction turn

Have the scene-direction turn produce a multi-beat plan into T13's storage, rather than only
executing immediate actions.

**Acceptance**: AC6 end to end — one director call, several beats rendered locally.
**Deps**: T14.

---

## Phase 6 — Branch hints (R6, AC7)

### [ ] T16 — Extend `Beat` with branch hints

Add contingencies keyed to player behaviour, as optional fields with defaults.

Regression guard is mandatory, because `Beat` is shared with the arc-prose pipeline: `expand.py`
reads `.tension` at lines 67, 96 and 100; `as_text()` (`schema.py:95-103`) formats every field;
`ExpandStoryArc` (`plan/nodes.py:353`) drives the pipeline. Optional fields with defaults should
be safe, but that must be tested rather than assumed.

Failing tests first, covering: beats without hints still load and execute; previously saved plans
still validate; and `ExpandStoryArc` still completes.

**Acceptance**: schema extended, old plans still work, arc generation unaffected. **Deps**: T13.

### [ ] T17 — Local branch selection

A cheap local rule choosing among hints, with no director call. Tests cover hint matched, no
hint matched, and multiple hints matching.

**Acceptance**: AC7. **Deps**: T16, T14.

### [ ] T18 — Teach the planner to write hints

Update the scene-direction planner prompt so beats carry useful contingencies. Verify by
inspecting generated plans, not only by test.

**Acceptance**: generated plans contain usable hints. **Deps**: T17.

---

## Phase 7 — Abstraction bridge (R7, AC8)

### [ ] T19 — Abstracted context mode for scene direction

`scene-context-chat.jinja2:2` calls `scene.context_history(min_dialogue=20, show_hidden=True)`,
so the director currently reads verbatim prose.

1. Add an abstracted mode supplying arc summaries, world state, character goals and scene
   intent, with no verbatim dialogue.
2. Make it a config flag on the `scene_direction` action, defaulting off so current behaviour
   is unchanged.
3. Test by rendering the prompt and asserting no scene dialogue appears.

**Acceptance**: AC8.

### [ ] T20 — Confirm plan quality on abstracted context

Play with abstracted context on and the local director. If plans degrade badly, that is a
finding worth recording before adding a cloud model on top — it separates "abstraction lost
too much" from "the local model is too small".

**Acceptance**: recorded observation in `conductor/decision-log.md`. **Deps**: T19.

---

## Phase 8 — Cloud director (R8, AC9)

### [ ] T21 — Second Ollama client for the director

1. `ollama pull gpt-oss:120b-cloud` on the signed-in daemon. No client code change —
   `fetch_available_models` (`ollama.py:145`) surfaces it.
2. Add a second client entry in `config.yaml` with a large `max_token_length` and
   `api_handles_prompt_template: True`, since cloud-hosted models may honour neither `raw`
   (`ollama.py:225`) nor `options.num_ctx` (`ollama.py:218`).
3. Set `agents.director.client` to it, leaving every other agent on the local model.
4. Set `OLLAMA_NUM_PARALLEL` to at least 2, or the cloud call blocks local generation.

**Acceptance**: AC9 — a director turn completes on the cloud model, beats render locally.
**Deps**: T19 (abstraction bridge first, so explicit prose never leaves the machine).

### [ ] T22 — Confirm graceful absence

With no cloud client configured, play must be unaffected.

**Acceptance**: AC9 (fallback half). **Deps**: T21.

---

## Phase 9 — Background director (R9, AC10)

### [ ] T23 — Measure director turn latency

Answers the design doc's open question, and decides whether background thinking is optional or
mandatory. Measure `direction_execute_turn` at raised context with `enable_analysis: True` and
10 max actions, locally and on cloud.

**Acceptance**: figures recorded. **Deps**: T21.

### [ ] T24 — Asynchronous director turn

**Requires its own design pass before implementation.** The concurrency questions below are not
answered by this plan, and guessing at them is how stale plans get applied over live scene state.

1. Run the director turn without blocking the loop, publishing a plan the loop picks up when
   ready.
2. If no fresh plan exists, the current plan or its branch hints continue — the loop must
   never wait.
3. Staleness mechanism, to be specified in the design pass: a generation counter on scene state,
   captured when the director turn starts and compared when its plan is applied, discarding the
   plan if the scene has moved on.
4. Decide what happens when the player acts while the director is mid-thought — cancel the
   in-flight turn, or let it finish and discard the result.
5. Both clients address `localhost:11434` and Ollama serialises by default, so `OLLAMA_NUM_PARALLEL`
   must be at least 2 for this to buy anything at all.

**Acceptance**: AC10. **Deps**: T23, T15, T17.

---

## Phase 10 — Verification and documentation

### [ ] T25 — Sustained play session

Play a long session typing only in-character input. Count out-of-character actions and compare
against the current baseline.

**Acceptance**: AC11. **Deps**: T24 (or the last completed phase, if the track is cut short).

### [ ] T26 — Documentation

1. Amend `docs/fork/autonomous-story-design.md` with what was actually built and what the
   measurements showed.
2. Record decisions in `conductor/decision-log.md`.
3. Document that Auto Narration is inert under Scene Direction
   (`narrator/auto_narration.py:104-107`) so the setting is not mistaken for a live control.

**Acceptance**: docs match the implementation. **Deps**: T25.

---

## DAG

```
T0 → T1               (baseline must precede the first change)
T2 → T3 → T4 → T5
T2 → T6
T7 → T8 → T9          T8 also needs T4
T4 + T8 + T9 → T4b    (cap raise gated on hand-back triggers)
T10 → T11             T11 also needs T4
T10 → T12
T13 → T14 → T15
T13 → T16 → T17       T17 also needs T14
T17 → T18
T19 → T20
T19 → T21 → T22
T21 → T23 → T24       T24 also needs T15, T17
T24 → T25 → T26
```

Parallel groups:

- **G0**: T0 alone — nothing may precede it
- **G1**: T1, T2, T7, T10 — no interdependencies
- **G2**: T3, T12 (after T2 / T10)
- **G3**: T4, T6
- **G4**: T5, T8, T11 (all need T4)
- **G5**: T9, T13, T19
- **G6**: T4b, T14, T16, T20
- **G7**: T15, T17, T21
- **G8**: T18, T22, T23
- **G9**: T24 → T25 → T26

## Human-verified tasks — the track cannot self-certify

These close only by a person playing Talemate, and no agent can close them:
**T0, T1, T4b, T5, T11, T20, T23, T25** — covering AC1, AC3, AC5, AC9, AC10, AC11.

Automatable: T2, T3, T4, T6, T7, T8, T9, T10, T12, T13, T14, T15, T16, T17, T18, T19, T21, T22,
T26. Execution stops at a play-test handoff listing the open human tasks, rather than reporting
the track complete.

## Complexity

**XL** overall. Per phase: Phase 1 S, Phase 2 S, Phase 3 M, Phase 4 M, Phase 5 L, Phase 6 M,
Phase 7 M, Phase 8 S, Phase 9 L, Phase 10 S.

Phases 1-4 (T1-T12) are M in total and deliver the change most likely to fix the complaint.
Consider stopping there and judging by play before committing to Phases 5-9 — the spec's
phasing note records that this split is the user's call, and the plan carries all nine either
way.

## Risks

- **Upstream merge conflicts.** T4 and T11 edit `scene-loop.json`, among the files upstream
  changes most. Keep edits additive and isolatable.
- **Runaway story.** Raising the cap (T4) before hand-back triggers land (T8) means the only
  brake is the director's own judgement on a 12B. Keep the raised default moderate until
  Phase 3 is in.
- **Direct-address false negatives.** T7 must cope with the `movie_script` conversation format;
  a missed detection reads to the player as the story ignoring them.
- **Stale plans.** T24 must not apply a plan computed against superseded scene state.
- **GPU contention.** Do not run a graph embed pass while Talemate is running.
