# Autonomous story — design

Goal: roleplay as a single character while the story unfolds around you, instead of
manually driving the narrator, choosing who speaks next, and asking for the story to
progress.

## Decisions taken

| Decision | Choice |
| --- | --- |
| Compute | Local-first, cloud optional. Local model always sufficient to play; cloud director slots in when configured. |
| Turn model | Scene-dependent. Some scenes wait for the player, others push forward on their own. |
| Where it lives | Fork core (`scene-loop.json`, `select-actor-for-turn.json`, agent config), designed additively to keep upstream merges readable. |
| Autonomy | Pacing, world motion, authorship, and consequence all permitted without asking. |
| Cadence | Plan-ahead batches, with the director re-planning in the background while the player reads and types. |
| Off-plan handling | Plans carry branch hints so the local model can follow a contingency without a cloud round-trip. |
| Hand-back | Direct address, explicit director yield, decision points, plus a hard safety cap as backstop. |

## Baseline — what is already working

The following are enabled in `config.yaml` and already deliver a large part of the goal.

- **Scene Direction** (`director.scene_direction`). The `agents/director/SceneDirection`
  node in `scene-loop.json` runs an autonomous director turn each loop iteration.
  `direction_execute_turn` takes up to `max_actions_per_turn` (10) sequential actions and
  returns `yield_to_user`. Available actions, all currently permitted:
  `direct_scene.direct_actor`, `direct_scene.direct_narrator`, `direct_scene.advance_time`,
  `direct_scene.yield_to_user`, `roll_dice`, `gamestate.update`,
  `update_context.create_characters`, `.edit_characters`, `.world`, `.history`,
  `.story_configuration`, `query.*`, `user_interaction.prompt_text`, `.display_notice`,
  `visuals.create_image`.
- **Action gating is per-scene, default-open.** `get_disabled_action_ids`
  (`action_core/gating.py:187`) reads `disabled_sub_actions` from scene state, default `{}`.
  Nothing is denied unless explicitly denied. `direct_scene.yield_to_user` is
  `force_enabled` and cannot be denied.
- **Guide Scene** (`director.guide_scene`). Injects 384 tokens of per-turn guidance into
  both actors and the narrator.
- **Dynamic Actions** (`director._generate_choices`). 30% chance per turn of offering three
  suggested actions for the player.
- **Per-scene direction override.** `SceneIntent.direction` (`scene/schema.py:74`) carries
  `always_on` and `run_immediately`, wired into the loop via
  `Get Scene Intent.direction_always_on → Scene Direction.always_on`. A scene can force
  direction on regardless of agent config.
- **Plan primitives.** `Plan`, `Task`, `Beat`, `PlanStatus` (`director/plan/schema.py`) plus
  nodes `CreatePlan`, `GetActivePlan`, `CompleteTask`, `InsertTask`, `EditTask`,
  `RemoveTask`, `DeletePlan` (`director/plan/nodes.py`). `Beat` already carries `type`
  (narration / dialogue / action / transition / reveal), `characters`, `pacing`, `tension`,
  `order`, and `status`.

### Baseline caveat

**Auto Narration is inert.** `narrator.auto_narration.disable_during_scene_direction`
defaults `True`, and `auto_narration_enabled_actions` (`narrator/auto_narration.py:104-107`)
returns `[]` whenever `director.direction_enabled_with_override` is true. With Scene
Direction on, the `chance: 0.9` and the `progress_story` / `narrate_scene` /
`narrate_after_dialogue` weights have no effect. Narration now comes exclusively from the
director calling `direct_scene.direct_narrator`.

Leave it suppressed. Two systems independently deciding to narrate produces double
narration. Note it so the setting is not mistaken for a live control.

## Gap analysis — what still needs building

### 1. The hard 3-beat cap (the actual cause of the complaint)

In `scene-loop.json`: a `data/number/Make` node titled `MAX AI TURNS - 3` with value `"3"`
feeds `Compare.b`; `GET shared.ai_turns` feeds `Compare.a`; the operation is
`greater_equal`. The result drives a `Switch` whose `yes` output sets
`shared.skip_to_player` — the author's own `core/Watch` on that branch is titled
**EMERGENCY BREAK**. `GET shared.skip_to_player` then inverts into
`Select Actor For Turn.state`, so when the cap trips, actor selection is skipped entirely
and control returns to the player.

This fires regardless of what the director wants. It is not exposed in config or the UI.
Every third AI beat you are pulled back in, which is the mechanical source of "I have to
do a lot of actions for anything to happen."

Change: make the cap a configured value, raise its default substantially, and demote it to
a genuine backstop. The primary hand-back becomes the director's `yield_to_user` plus the
triggers in section 6.

### 2. Scene-dependent pacing

`SceneType` (`scene/schema.py:62`) carries only `id`, `name`, `description`, `instructions`,
`instruction_template`. There is nowhere to express "this scene waits for the player" versus
"this scene pushes forward."

`WaitForInput` (`game/engine/nodes/scene.py:781`) already has an **`abort_condition`** input
socket. The chain connected to it is evaluated on each poll of the input loop; resolving
truthy raises `AbortWaitForInput`, propagated as `LoopContinue`. Nothing in `scene-loop.json`
connects it.

Change: add pacing fields to `SceneType` (turn mode, idle threshold, per-scene beat cap) and
wire `abort_condition` so push-mode scenes advance when the player is idle.

### 3. Loop-bound plans

Plans are currently bound to director *chat*, not to the scene loop —
`GetActiveChatPlanId`, `chat.plan_id` (`chat/mixin.py:560`) — and the surrounding pipeline
(`create-outline` → `critique-outline` → `expand`) generates arc *prose*, which is a
different feature. Nothing in `scene-loop.json` reads a plan.

Change: bind a plan to the scene loop, and add loop nodes that pull the next pending `Beat`
and execute it against the local model. Reuse the existing schema and task nodes rather than
inventing a parallel structure.

### 4. Branch hints on beats

`Beat` has no contingency field. For the local model to follow a deviation without a cloud
round-trip, each beat needs alternatives keyed to player behaviour, plus a cheap local rule
for choosing between them.

Change: extend `Beat` with branch hints, and teach the planner prompt to write them.

### 5. Background director

The `SceneDirection` node runs synchronously inside the loop. On a 12B, an analysis pass plus
up to ten actions is a visible stall — and the same stall, if pointed at a cloud model,
lands on every turn. The intent is for the director to think while the player reads and
types.

Change: run the director turn asynchronously, publishing a plan the loop picks up when ready.
The loop must never block on it; if no fresh plan exists, the current plan (or its branch
hints) continues.

### 6. Hand-back triggers

Of the four chosen triggers, one exists (`direct_scene.yield_to_user`), one exists but is
misconfigured (the hard cap, section 1), one is partially present (`roll_dice` plus Dynamic
Actions give decision texture but nothing gates the loop on a decision), and **direct
address is entirely absent** — nothing detects that a character just spoke to or acted on the
player.

Change: implement direct-address detection as a cheap local check, and let decision points
gate the loop.

### 7. Cloud / local split and the abstraction bridge

Not started. Everything currently runs on `Ollama` / `Rocinante-X-12B` at 8192 context.

**Ollama Cloud is reachable with no code changes, via the local daemon.** The client sets
`enable_api_auth: bool = False` (`client/ollama.py:46`), so there is no API-key field and
pointing it at `https://ollama.com` will not authenticate. Instead, pull a cloud model into
the signed-in local daemon (`ollama pull gpt-oss:120b-cloud`); it then appears in
`fetch_available_models` (`ollama.py:145`) alongside local models. Add a second Ollama client
entry in `config.yaml` and set `agents.director.client` to it.

Two constraints:

- `ollama.py:225` passes `raw=self.can_be_coerced`, because Talemate builds its own prompt
  template and bypasses Ollama's. Cloud-hosted models may not honour `raw`, nor
  `options.num_ctx` (`ollama.py:218`). Mitigation is per-client and already exists: set
  `api_handles_prompt_template: True` on the cloud client only.
- Both clients address `localhost:11434`, and Ollama serialises by default. A cloud director
  call will block local beat generation unless `OLLAMA_NUM_PARALLEL` is at least 2. Cloud
  inference does not consume local VRAM, so the parallelism is free once permitted.

**The abstraction bridge.** Scene Direction's prompt currently includes verbatim scene text:
`scene-context-chat.jinja2:2` calls `scene.context_history(min_dialogue=20, show_hidden=True)`.
A cloud director therefore *reads* explicit prose even though it only writes plans. Two
consequences: content leaves the machine, and a single refusal mid-turn kills the autopilot —
returning the player to exactly the manual clicking this work exists to remove.

Change: add an abstracted context mode for scene direction — arc summaries, world state,
character goals, scene intent, no verbatim dialogue. The cloud director plans beats; the
local model renders them at whatever intensity the scene calls for.

Note that censorship is a property of the *model*, not of "cloud". Talemate already tracks
this per client: `decensor_enabled` (`client/base.py:292`) defaults `True`, and is set
`False` on `anthropic.py:106`, `openai.py:124`, `openrouter.py:209`, `deepseek.py:42`, which
swaps in the tamer `system-no-decensor.jinja2` system prompt. Among Ollama Cloud models,
`gpt-oss:120b-cloud` is heavily safety-trained and will refuse explicit prose;
`deepseek-v3.1:671b-cloud` and `kimi-k2:1t-cloud` are considerably more permissive. With the
abstraction bridge in place, `gpt-oss:120b-cloud` becomes viable and is the strongest
reasoner of the three — which is what a planner wants.

### 8. Context budget (cheap, do first)

`max_token_length: 8192` on the Ollama client. Scene Direction reserves `response_length`
2048 for its own output and splits the remainder by `scene_context_ratio` 0.3 — so roughly
1.8k of scene context and 4.3k of direction history, before world state and instructions.
The director is planning through a keyhole, and that alone will make it look dumber than the
model is.

Rocinante-X-12B is Mistral-Nemo-based and handles far more. Raising this is a config change
with no code, and should be measured before concluding anything about the director's quality.

## Sequencing

Ordered so that each step is independently playable and the cheapest diagnostics come first.

1. **Context budget.** Raise `max_token_length`. Play. This may account for more of the
   perceived dumbness than any code change.
2. **Unblock the cap.** Make `MAX AI TURNS` configurable, raise the default, keep it as
   backstop. This is the single change that most directly addresses the complaint, and it is
   small.
3. **Hand-back triggers.** Direct-address detection and decision-point gating, so raising the
   cap does not mean the story runs away.
4. **Scene pacing fields plus `abort_condition`.** Wait-versus-push per scene type.
5. **Loop-bound plans.** Bind a plan to the loop; execute beats from it.
6. **Branch hints.** Extend `Beat`; teach the planner to write contingencies.
7. **Abstraction bridge.** Abstracted context mode for scene direction.
8. **Cloud director.** Second Ollama client, `agents.director.client`, `OLLAMA_NUM_PARALLEL`.
9. **Background director.** Async planning. Last, because it is the riskiest and every
   earlier step is testable without it.

Steps 1-4 are playable without any cloud model and without touching the plan system. If they
alone fix the feel, steps 5-9 become optional.

## Upstream merge exposure

`scene-loop.json` and `select-actor-for-turn.json` are among the files upstream Talemate
changes most. Keep edits additive: add nodes rather than rewiring existing ones where
possible, put new logic in new modules referenced from the loop, and add config through new
`AgentAction` entries rather than altering existing ones.

Worth noting that the fork is not the only option — `scenes/infinity-quest-dynamic-story-v2/nodes/scene-loop.json`
already demonstrates per-scene loop override via `"extends"`, and `dynamic-storyline-package.json`
demonstrates the package mechanism (`util/packaging/InstallNodeModule`, `PromoteConfig`,
`installable`, `restart_scene_loop`). The fork was chosen deliberately for global default
behaviour and a proper config UI; the packaging route remains available if merge pain becomes
the dominant cost.

## Resolved: how yield and the cap interact

Traced by node ID. The two paths are independent writers to the same flag, and both work:

- **Director yield.** `Scene Direction.yield_to_user` (`bad4de23`) → `core/Watch` (`070dcc40`)
  → `state/ConditionalSetState` `SET shared.skip_to_player` (`88c066b8`). Conditional, so it
  only sets the flag when the director actually wants to yield.
- **The cap.** `data/number/Make` `"3"` (`d5a5c1d5`) → `Compare.b` (`7b31eda5`, `greater_equal`,
  with `GET shared.ai_turns` on `a`) → `Switch` (`c31806be`) → `state/SetState`
  `SET shared.skip_to_player` (`f8fe5739`). Unconditional.
- **Consumer.** `GET shared.skip_to_player` (`4b748e41`) → `NOT` (`8f39be2c`), whose `no`
  output gates `Select Actor For Turn`. Separately `GET scene loop.yield_to_user`
  (`f3171f31`) → `Watch` (`4967ef4d`) → `RSwitch.check` (`149280f6`) picks player input
  versus actor generation.

Consequence for sequencing: raising the cap does **not** disturb the director's yield. They
share the channel but not the wiring, so step 2 is a one-node change plus config plumbing,
and is low risk.

## Open questions

- What is the actual per-turn latency of `direction_execute_turn` on Rocinante-12B at raised
  context, with `enable_analysis: True` and 10 max actions? This number decides whether the
  background director is a nice-to-have or mandatory.
- Should `maintain_turn_balance` remain on once plans drive beat selection? It exists to
  encourage variety, which a plan should already handle, and the two may fight.
