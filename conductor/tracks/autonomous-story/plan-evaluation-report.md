# Plan Evaluation Report — autonomous-story

**Date**: 2026-07-30
**Verdict**: **FAIL** — plan revision required before execution
**Evaluated by**: orchestrator session directly. The `loop-plan-evaluator` and
`cto-plan-reviewer` agents were dispatched twice each and both returned idle with no output,
so this evaluation was performed inline. Every code claim below was verified firsthand against
the files, not inherited from an agent report.

## Verification of the plan's factual claims

All confirmed against source:

| Claim | Status |
| --- | --- |
| `data/number/Make` `d5a5c1d5`, value `"3"`, titled `MAX AI TURNS - 3` → `Compare.b` (`7b31eda5`, `greater_equal`) | Confirmed |
| True branch → `Switch` `c31806be` → `state/SetState` `f8fe5739` setting `shared.skip_to_player` | Confirmed |
| Director yield: `SceneDirection.yield_to_user` (`bad4de23`) → `Watch` (`070dcc40`) → `ConditionalSetState` (`88c066b8`), independent of the cap | Confirmed |
| Consumer: `GetState` (`4b748e41`) → `NOT` (`8f39be2c`) gating `Select Actor For Turn` | Confirmed |
| `WaitForInput.abort_condition` exists (`nodes/scene.py:843`) and is connected to nothing in `scene-loop.json` | Confirmed |
| `get_disabled_action_ids` reads `disabled_sub_actions`, default `{}`, permitting everything | Confirmed |
| `GetSceneState` exposes `auto_progress` and is the right extension point (`nodes/scene.py:76-96`) | Confirmed |
| `General` config holds `auto_progress` (`config/schema.py:200`) | Confirmed |
| Auto Narration inert under Scene Direction (`narrator/auto_narration.py:104-107`) | Confirmed |

## Findings

### F1 — Phase 5 misdescribes the gap, and overestimates the work (severity: high)

The plan and spec both assert that "nothing in the scene loop reads a plan" and that beat
execution must be built. `plan/nodes.py:13-14` says otherwise:

> Beat execution is handled by the existing `direct_scene` FOCAL action.
> Plan state is injected into the chat context when a plan exists.

So beat execution already exists. The director executes beats by calling `direct_scene`, and
`direct_scene` is already enabled and already reachable from a scene-direction turn. What is
actually missing is narrower: **plan state is injected into the chat prompt but not the
scene-direction prompt.** Chat does it at `chat/mixin.py:560` via
`extra_vars["scene_plan"] = get_plan(self.scene, plan_id)`; `scene-direction.jinja2` has no
plan section.

T13-T15 as written would build a parallel beat-execution path that duplicates `direct_scene`.
Correct scope is: give scene direction a plan reference, inject it into the prompt, done.

### F2 — `cleanup_orphaned_plans` will delete a loop-bound plan (severity: high)

`plan/util.py:130-153` removes every plan not referenced by a chat's `plan_id`. A plan created
by the scene loop has no chat, so it is orphaned by definition and will be garbage-collected.
`resolve_plan_id` (`util.py:107-112`) and `get_active_plan` (`util.py:115-120`) also both
resolve exclusively through `director_chat_context`, which does not exist during a loop turn.

T13 must handle all three or loop plans will silently vanish. The plan does not mention this.

### F3 — T4 raises the cap before any hand-back trigger exists (severity: high)

T8's dependencies are `[T7, T4]`, so T4 lands first. Between T4 and T8 the only brake on the
story is a 12B's own judgement, which is precisely the risk the plan's own risk section
identifies — and the mitigation offered is prose advice ("keep the raised default moderate"),
not a dependency.

Fix: split T4 so the mechanical change is behaviour-neutral. T4 rewires the comparison to read
config while keeping the default at 3 — no behavioural change, independently verifiable. A new
task raises the default, depending on the hand-back triggers actually being in place.

### F4 — T1 destroys the baseline AC11 measures against (severity: medium)

AC11 requires comparing out-of-character action count "against the current baseline", but T1 —
the first task — changes the context budget, which changes behaviour. After T1 there is no
baseline left to compare against.

Fix: capture the baseline before touching anything.

### F5 — `Beat.type` is a pydantic discriminator, not just a label (severity: medium)

`TaskType = Annotated[Union[Beat, Task], pydantic.Field(discriminator="type")]`
(`plan/schema.py:107`), with `Task.type` as `Literal["task"]` and `Beat.type` as the five
narrative kinds. `Beat.type` therefore does double duty as the union discriminator and the beat
kind. T14 cannot introduce new beat kinds casually, and `_validate_task` (`plan/nodes.py:57`)
picks `Beat` versus `Task` "based on fields present", which is fragile.

T14 must also state explicitly how all five existing kinds map onto execution paths.
`narration` and `dialogue` are obvious; `action`, `transition` and `reveal` are not, and the
plan hand-waves them.

### F6 — T16 has no regression guard on arc generation (severity: medium)

`Beat` is consumed by the arc-prose pipeline: `expand.py:67`, `:96`, `:100` read `.tension`,
`as_text()` (`schema.py:95-103`) formats every field, and `ExpandStoryArc` (`nodes.py:353`)
drives the whole thing. Adding optional fields with defaults should be safe, but "should be" is
not a test. T16 must assert `ExpandStoryArc` still works and that existing saved plans still
validate.

### F7 — T24's concurrency guard is one sentence (severity: medium, deferred)

"Guard against a stale plan being applied over a newer scene state" is not an implementable
specification. Needs a concrete mechanism — a generation counter on scene state, compared at
apply time, discarding stale plans — plus a decision on what happens when the player acts while
the director is mid-thought (cancel, or complete and discard). Acceptable to defer the detail
to a design pass at Phase 9, but the task must say that is what it is.

### F8 — T12 names no file (severity: low)

The scene-type editor is `talemate_frontend/src/components/TemplateSceneType.vue`, with
`WorldStateManagerSceneDirection.vue` and `templateMappings.js` also in the path. T12 should
name them.

### F9 — No stated revert path (severity: medium)

The track adds substantial autonomy with no acceptance criterion for turning it back off.
A config-only revert to current behaviour should be explicit, both as a safety property and
because AC11's comparison depends on being able to return to baseline.

### F10 — Six acceptance criteria are human-verified (severity: high, structural)

AC1, AC3, AC5, AC9, AC10 and AC11 verify only by a human playing the application — T1, T5, T11,
T20, T23, T25. This is honest in the plan's wording ("verification: play, not unit test"), and
the automatable subset is coherent on its own, but it means the track cannot self-certify
complete. The loop must stop at a play-test handoff rather than report PASS.

## Assessment of areas that hold up

- Dependency graph is otherwise sound; no cycles, and the parallel groups are legitimate.
- T4 and T11 both edit `scene-loop.json` but touch disjoint regions (the cap comparison versus
  the `WaitForInput` input chain), and the DAG orders T4 first. No conflict.
- Reusing `Plan`/`Beat` rather than inventing a structure is the right call — reinforced by F1,
  since the execution path already exists too.
- Gating T21 (cloud director) behind T19 (abstraction bridge) is correct and should not be
  relaxed.
- Phases 1-4 genuinely are independently playable, as claimed.

## Required corrections

1. Add T0 — capture the out-of-character baseline before T1. (F4)
2. Split T4 into a behaviour-neutral config rewire, plus a later raise gated on hand-back
   triggers. (F3)
3. Rewrite Phase 5 around injecting plan state into the scene-direction prompt, not building
   beat execution. (F1)
4. Add loop-plan survival against `cleanup_orphaned_plans`, `resolve_plan_id` and
   `get_active_plan` to T13. (F2)
5. State the beat-kind to execution-path mapping in T14, and note the discriminator
   constraint. (F5)
6. Add an arc-generation regression guard to T16. (F6)
7. Mark T24 as requiring its own design pass, and name the staleness mechanism. (F7)
8. Name the UI files in T12. (F8)
9. Add AC12 — config-only revert to current behaviour. (F9)
10. Record that the track completes at a play-test handoff, not at PASS. (F10)
