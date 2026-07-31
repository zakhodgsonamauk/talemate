# Autonomous story — specification

**Design doc**: `docs/fork/autonomous-story-design.md` (read it first; this spec does not
repeat the code trace)

## Goal

Roleplay as a single character while the story unfolds around you. The player should act as
their character and nothing else — no telling the narrator to narrate, no choosing who
speaks next, no asking the story to progress. The engine decides those things and hands
control back only when the player's character is genuinely needed.

Success looks like: a long stretch of play in which the player types only in-character
dialogue and actions, other characters converse with each other, the narrator moves scenes
on its own, time passes, and the story still coheres.

## Context — what already works

Scene Direction, Guide Scene, Dynamic Actions and Auto Narration have been enabled in
`config.yaml`. The autonomous director is live, all its action bundles are permitted
(`disabled_sub_actions` defaults to `{}`), and it can already direct actors, direct the
narrator, advance time, roll dice, author characters and world entries, and yield to the
player.

The dominant remaining obstacle is not missing capability. It is a hardcoded cap that
overrides the director every third beat. See Requirement 2.

## Requirements

### R1 — Context budget

The Ollama client runs at `max_token_length: 8192`. Scene Direction reserves
`response_length` 2048 for its own output and splits the remainder by `scene_context_ratio`
0.3, leaving roughly 1.8k of scene context. Raise the context budget and measure director
quality before drawing conclusions about the model.

### R2 — The AI-turn cap must stop overriding the director

`scene-loop.json` contains a `data/number/Make` node valued `"3"` feeding a
`greater_equal` comparison against `shared.ai_turns`; the true branch unconditionally sets
`shared.skip_to_player`, which gates out `Select Actor For Turn`. The author's own watch node
on that branch is named `EMERGENCY BREAK`.

The cap must become a configured value with a substantially higher default, retained as a
backstop rather than the primary pacing mechanism. The director's `yield_to_user` becomes the
primary hand-back. The design doc records the resolved node-ID trace confirming these two
paths are independent, so this change does not disturb the director's yield.

### R3 — Hand-back triggers

Control must return to the player when, and broadly only when:

- **Direct address** — a character speaks to, asks something of, or acts upon the player's
  character. Currently undetected. Must be a cheap local check, not an LLM call per beat.
- **Director yield** — already works via `direct_scene.yield_to_user` (force-enabled).
- **Decision points** — the story reaches a fork whose outcome depends on the player, or a
  dice roll needs their call.
- **Safety cap** — R2's configured ceiling, as backstop only.

### R4 — Scene-dependent pacing

`SceneType` must carry pacing configuration so that quiet dialogue scenes wait for the player
while action, travel or crowd scenes push forward on their own.

`WaitForInput` already exposes an `abort_condition` input socket, evaluated on each poll of
the input loop, which raises `AbortWaitForInput` (propagated as `LoopContinue`) when truthy.
Nothing connects it. Push-mode scenes must use it to advance when the player is idle.

### R5 — Loop-bound plans

The director must plan several beats ahead, and the local model must execute that batch
without a further director call per beat.

`Plan`, `Task`, `Beat` and `PlanStatus` already exist, as do `CreatePlan`, `GetActivePlan`,
`CompleteTask`, `InsertTask`, `EditTask`, `RemoveTask` and `DeletePlan`. `Beat` already
carries `type`, `characters`, `pacing`, `tension`, `order` and `status`. But plans are bound
to director *chat* (`GetActiveChatPlanId`, `chat.plan_id`) and the surrounding pipeline
generates arc prose, which is a separate feature. Nothing in the scene loop reads a plan.

Reuse the schema and task nodes. Do not build a parallel plan structure.

### R6 — Branch hints

`Beat` must carry contingencies so that when the player does something the plan did not
anticipate, the local model can follow an alternative without a cloud round-trip. The planner
prompt must be taught to write them, and a cheap local rule must select among them.

### R7 — Abstraction bridge

Scene Direction's prompt currently includes verbatim scene text —
`scene-context-chat.jinja2:2` calls `scene.context_history(min_dialogue=20, show_hidden=True)`.

Add an abstracted context mode supplying arc summaries, world state, character goals and
scene intent, with no verbatim dialogue. Two reasons: explicit content must not leave the
machine, and a single refusal mid-turn kills the autopilot, returning the player to exactly
the manual clicking this work exists to remove.

### R8 — Cloud director, optional

Support routing the director to a stronger model without changing anything else. Ollama Cloud
is reachable through the signed-in local daemon (`ollama pull gpt-oss:120b-cloud`) — no client
code change needed, since `fetch_available_models` surfaces cloud models alongside local ones.

Constraints: `ollama.py:225` passes `raw=self.can_be_coerced` because Talemate builds its own
prompt template, and cloud-hosted models may honour neither `raw` nor `options.num_ctx`; the
per-client `api_handles_prompt_template` flag is the mitigation. Both clients address
`localhost:11434` and Ollama serialises by default, so `OLLAMA_NUM_PARALLEL` must be at least
2 or cloud director calls will block local beat generation.

The system must remain fully playable with no cloud model configured.

### R9 — Background director

The director must be able to think while the player reads and types. The loop must never block
on it; if no fresh plan is ready, the current plan or its branch hints continue.

## Acceptance criteria

- **AC1** — Raising the context budget is verified in play, and the effect on director quality
  is recorded, before any conclusion is drawn about model capability.
- **AC2** — The AI-turn cap is read from configuration, not a literal in the node graph, and
  its default is materially higher than 3.
- **AC3** — With the cap raised, the director's `yield_to_user` still returns control to the
  player. Verified in play, not only by reading the graph.
- **AC4** — A character addressing the player's character returns control to the player,
  without an LLM call dedicated to detecting it.
- **AC5** — A scene type configured to push forward advances while the player is idle; a scene
  type configured to wait does not.
- **AC6** — A plan created by the director is executed beat by beat from the scene loop, with
  no director call per beat.
- **AC7** — When the player deviates, a branch hint is followed without a director call.
- **AC8** — With abstracted context enabled, the scene-direction prompt contains no verbatim
  scene dialogue. Verified by inspecting the rendered prompt.
- **AC9** — With `agents.director.client` pointed at a cloud model, a full director turn
  completes and beats render on the local model. With no cloud client configured, play is
  unaffected.
- **AC10** — A background director turn does not stall the loop; play continues while it
  thinks.
- **AC11** — A sustained play session in which the player types only in-character input, with
  the number of out-of-character actions recorded and compared against the baseline captured
  before any change was made.
- **AC12** — Current behaviour is recoverable by configuration alone, with no code revert:
  turning off `director.scene_direction` and returning the AI-turn cap to 3 restores today's
  play. This is both a safety property and what makes AC11's comparison possible.

## Out of scope

- Arc prose generation (`create-outline` → `critique-outline` → `expand`). Existing separate
  feature; reuse its schema, leave its pipeline alone.
- Re-enabling Auto Narration. It is deliberately suppressed while Scene Direction runs
  (`narrator/auto_narration.py:104-107`). Two systems independently deciding to narrate
  produces double narration. Document it as inert; do not wire it back in.
- Adding API-key support to the Ollama client. Cloud access goes through the signed-in local
  daemon, which needs no auth field.
- Non-Ollama cloud providers. The client abstraction already supports them; nothing here
  should depend on which provider is chosen.
- Packaging this as an installable package. The fork route was chosen deliberately for global
  default behaviour and a proper config UI.

## Technical notes

**Additive edits, to survive upstream merges.** `scene-loop.json` and
`select-actor-for-turn.json` are among the files upstream Talemate changes most. Prefer adding
nodes over rewiring existing ones, put new logic in new modules referenced from the loop, and
add configuration through new `AgentAction` entries rather than altering existing ones.

**Config plumbing pattern.** `General` (`config/schema.py:200`) holds `auto_progress` and
friends; `GetSceneState` (`game/engine/nodes/scene.py:76-96`) exposes them as node outputs;
`quick_settings.py:42` shows how a setting becomes a UI quick toggle. Follow that chain for
the AI-turn cap rather than inventing a new path.

**Fallback route if merge pain dominates.** `scenes/infinity-quest-dynamic-story-v2/nodes/scene-loop.json`
demonstrates per-scene loop override via `"extends"`, and `dynamic-storyline-package.json`
demonstrates the package mechanism (`util/packaging/InstallNodeModule`, `PromoteConfig`,
`installable`, `restart_scene_loop`). Not the chosen route, but available.

**Censorship is a property of the model, not of "cloud".** Talemate tracks this per client:
`decensor_enabled` (`client/base.py:292`) defaults `True` and is set `False` on
`anthropic.py:106`, `openai.py:124`, `openrouter.py:209`, `deepseek.py:42`, swapping in the
tamer `system-no-decensor.jinja2`. Among Ollama Cloud models, `gpt-oss:120b-cloud` is heavily
safety-trained and will refuse explicit prose; `deepseek-v3.1:671b-cloud` and
`kimi-k2:1t-cloud` are considerably more permissive. With the abstraction bridge in place
`gpt-oss:120b-cloud` becomes viable and is the strongest reasoner of the three.

**Local GPU contention.** Per the standing note on this project, a running Talemate and a
graph embed pass will fight over the GPU. Stop one before the other.

## Phasing note

R1-R4 are independently playable, need no cloud model, and do not touch the plan system. If
they alone fix the feel, R5-R9 become optional. Splitting R5-R9 into a follow-on track is
reasonable and matches the `visual-reference-consistency` → `visual-reference-closure`
precedent. That call belongs to the user, so the plan carries all nine.
