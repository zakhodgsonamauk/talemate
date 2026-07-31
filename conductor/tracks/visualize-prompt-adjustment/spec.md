# Track: visualize-prompt-adjustment

**Type**: feature (UI + node graph)
**Created**: 2026-07-30
**Brainstormed with**: user, 2026-07-30 (decisions recorded in `metadata.json`)

Adds a second chip beside the existing **Visualize** action that lets the user see and
edit the composed image prompt before any image is generated.

## Problem

`Visualize` is fire-and-forget. `SceneMessages.vue:1218` sends
`visual/visualize` with `message_ids` and auto-attach flags, and the next thing the
user sees is a finished image. The prompt that produced it — LLM keywords, the applied
art style, the character anchors, the negative prompt — is never shown and cannot be
corrected. A near-miss illustration can only be retried blind.

The information exists. `agents/visual/UnpackPrompt` (`nodes.py:278`) already exposes
`positive_prompt`, `negative_prompt` and `instructions`, and the `prompt_only` branch of
`wsh-visualize.json` already unpacks them — but it sinks them into a formatted
`EmitSystemMessage` dump in chat, which is read-only and cannot be fed back into a
generation.

## Goal

Clicking the new chip opens the existing generation modal, pre-filled with the prompt
and negative prompt the app would actually have used. The user edits them and generates.
The resulting image attaches to the originating message exactly as today's Visualize
does. The prompt is composed by the same path a normal Visualize uses — not a parallel
implementation.

## Constraint set by the user

> should share the generation path with the rest of the codebase

This is load-bearing and it decides the architecture. Prompt composition (LLM keywords,
`ApplyStyles`, anchors) exists **only** inside the `agents/visual/generateVisualAsset`
node module. It cannot be called standalone from Python: its `core/Input` sockets require
a connected `source` in an outer graph (`core/__init__.py:2002-2014`), and no Python code
references it. Therefore the prompt-preview leg is wired **in the node graph**, and the
final generation reuses the existing `visual/generate` handler. No new prompt-building
code is written.

## Acceptance criteria

1. **AC1 — Chip placement.** A second chip ("Visualize with prompt adjustment", or a
   pencil-badged variant of the same icon) renders beside the existing Visualize chip,
   on exactly the messages that show Visualize today: `ContextInvestigationMessage.vue`
   with `sub_type` in `examine` / `visual-scene` / `visual-character`, hidden once an
   asset is attached. `NarratorMessage.vue` is **not** changed. Both chips share the
   same `uxLocked || appBusy` disabling.
2. **AC2 — Composition is shared, not duplicated.** The preview prompt is produced by
   the same `agents/visual/generateVisualAsset` node instance inside
   `wsh-visualize.json` that a normal Visualize uses, running with `prompt_only` true.
   No new prompt-assembly code exists in Python or in a new module. Verified by
   inspection of the diff: `wsh-visualize.json` gains a branch, not a second
   `generateVisualAsset` node.
3. **AC3 — Structured return leg.** With `return_prompt` true in the payload, the
   `prompt_only` branch routes `UnpackPrompt`'s `positive_prompt` / `negative_prompt`
   into `websocket/WebsocketResponse` (`nodes/websocket.py:158`) instead of
   `EmitSystemMessage`. The client receives one `{type: "visual", action:
   "prompt_preview", ...}` envelope carrying at minimum `prompt`, `negative_prompt`,
   `vis_type`, `character_name`, `format` and the echoed `message_ids`.
4. **AC4 — No chat spam.** With `return_prompt` true, **no** system message is emitted.
   With `return_prompt` absent or false, behaviour is byte-identical to today: the
   no-backend fallback still dumps the prompt to chat as a system message.
5. **AC5 — Modal opens immediately.** The modal appears on click, already showing
   `vis_type`, character and instructions, with the prompt and negative-prompt fields in
   a loading state. They populate when the preview envelope arrives. The modal is
   cancellable while waiting, and cancelling mid-flight leaves no stuck spinner and no
   orphaned state.
6. **AC6 — Generation reuses `visual/generate`.** Submitting sends
   `visual/generate` with a `generation_request` whose
   `asset_attachment_context` carries the originating `message_ids`,
   `allow_auto_attach` and `allow_override` — the same handler
   (`websocket_handler.py:59`) and the same `visual.generate()` call the Visual Library
   already uses. No new generation endpoint.
7. **AC7 — The image attaches to the message.** The generated image lands on the
   originating message, indistinguishable from today's Visualize result, and the
   toolbar spinner clears.
8. **AC8 — Edits are honoured.** An edit made in the modal is present in the prompt
   recorded on the saved asset's metadata. A deliberately distinctive token added by
   hand survives to the asset record.
9. **AC9 — Existing Visualize is untouched.** The plain Visualize chip behaves exactly
   as before, including the no-backend prompt-only fallback. `tests/test_visual_anchor.py`
   and the rest of the visual suite still pass.
10. **AC10 — Spinner hygiene.** `visualizingMessageIds` does not leak. A preview that
    errors, is cancelled, or whose modal is dismissed clears the message's busy state —
    the existing `operation_done` sweep at `SceneMessages.vue:773` must still cover the
    new path or be extended to.

## Non-goals

- Adding Visualize (with or without adjustment) to plain narrator messages. Considered
  and explicitly dropped: those messages have no `source_arguments`, so `vis_type` and
  character would have to be guessed.
- Changing how prompts are composed — no new keywords, styles, anchors or budget logic.
  This track only surfaces and re-submits what the existing path produces.
- A new modal. `VisualLibraryGenerate.vue` is reused.
- Editing reference images in this flow beyond whatever that modal already offers.
- Persisting or replaying edited prompts across sessions.

## Technical notes

**How the websocket action binds.** `wsh-visualize.json` is an
`agents/AgentWebsocketHandler` module whose `properties.name` (`"visualize"`) is the
websocket action name and whose `properties.agent` (`"visual"`) selects the router.
`agents/base.py:456-503` walks every node of that base type at scene-loop startup and
calls `register_sub_handler`. Dispatch precedence matters:
`websocket_plugin.py:177-195` prefers a Python `handle_<action>` method and only falls
through to sub-handlers when none exists. **Do not add `handle_visualize` in Python** —
it would shadow the node module and break the shared path. The module already receives
`websocket_router` and `data` as `core/functions/Argument` nodes, so
`websocket/WebsocketResponse` can be wired without `GetWebsocketRouter`.

**Envelope shape is flat, not nested.** `WebsocketResponse` spreads its `data` input:
`{"type": router, "action": action, **data}`. So payload keys arrive top-level. That
matches the existing convention — `VisualImageView.vue:509-518` reads top-level
`message.asset_id` / `message.tags`, and `SceneMessages.vue:773` reads
`data.type` / `data.action`. Build the payload with `data/DictCollector`
(`data.py:1149`, dynamic named inputs) rather than chaining `DictSet`.

**Nodes to wire.** `data/Get` for `obj.return_prompt`; `core/Switch` (`logic.py:215`,
yes/no flow sockets with deactivation) to branch, matching how the existing
`prompt_only` branch is built; `data/DictCollector`; `websocket/WebsocketResponse`.
`obj.message_ids` already has a `data/Get` node in the graph — reuse it for the echo
rather than adding a second.

**Styles are not double-applied.** `visual.generate()` (`generation.py:809`) runs
`attach_character_references`, `_apply_seed` and `_finalize_prompt` — it does **not**
apply styles. Styles and anchors come from `ApplyStyles` inside the node graph, which
the preview leg has already run. So re-submitting a previewed prompt through
`visual/generate` does not restyle it.

**Known and accepted fidelity gap.** `_finalize_prompt` never runs on the `prompt_only`
branch, but does run on `visual/generate`. It drops keywords a diffusion model cannot
render, drops anchors for characters not in the shot, and trims to the token budget
(`generation.py:229-244`). So the prompt that reaches the backend is a filtered version
of what the modal showed. This is deliberate — the user's edits should be subject to the
same safety pass — but AC8 must therefore assert on a token that *survives* finalisation,
not on byte-equality with the modal's contents.

**Modal reuse is the largest frontend unknown.** `VisualLibraryGenerate.vue` is
currently mounted inside `VisualLibrary.vue` and needs `scene`, `visualAgentStatus`,
`templates`, `generationAvailable`, `editAvailable` and `maxReferences`. Mounting it from
`SceneMessages.vue` means sourcing those, and its `onSubmit` currently builds a
`generation_request` with **no** `asset_attachment_context` — that needs an additive
optional prop. Confirm what `SceneMessages.vue` already has in scope before assuming a
wrapper is needed.

**Coordination.** A second session is working on the visual agent's Python
(`agent.py`, `generation.py`, `style.py`, `references.py`, `backends/comfyui.py`). This
track touches `modules/wsh-visualize.json` and the frontend, so overlap should be nil —
but `generation.py` is read (not written) by the fidelity note above, so re-read it
before relying on line numbers.

## Verification

AC1, AC2, AC4, AC9 by inspection and the existing test suite. AC3 by observing the
websocket envelope. AC5, AC10 by driving the UI. AC6, AC7, AC8 need real generations,
so ComfyUI must be up (`start-fork.bat` with `USE_COMFYUI=1`) and **only one frontend may
be connected** — the backend accepts a single websocket, so a stray browser tab locks
Playwright out. Set `PYTHONIOENCODING=utf-8` when redirecting backend output or startup
crashes on its banner.

Node-graph edits are hand-edited JSON with UUID keys and a separate connection map;
after editing, reload the scene and confirm the module still registers its sub-handler
(the plain Visualize chip working at all is the smoke test) before testing the new leg.
