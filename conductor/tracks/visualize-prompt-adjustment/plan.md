# Plan: visualize-prompt-adjustment

Spec: `spec.md`. Read it first — this plan does not restate the architecture or the
reasoning behind it.

Order is deliberate: the return leg is proven end-to-end at the protocol level (T2)
before any UI is built on top of it, because a hand-edited node graph is the riskiest
part and the cheapest thing to get wrong silently.

## T1 — Establish the `return_prompt` flag and its no-op path

Add a `data/Get` for `obj.return_prompt` in `wsh-visualize.json` and a `core/Switch`
on it, with **both** branches currently leading to the existing
`Format Body` → `EmitSystemMessage` sink. Nothing behavioural changes yet.

Purpose is to de-risk the JSON surgery in isolation: node entries carry UUID keys and
connections live in a separate map at the bottom of the file, so a mis-wired socket is
easy to introduce and hard to see.

**Done when**: scene loads, the module still registers (plain Visualize chip works), and
the no-backend prompt-only fallback still dumps to chat. Deps: none.

## T2 — Wire the structured return leg

Build the payload with `data/DictCollector` (`prompt`, `negative_prompt`, `vis_type`,
`character_name`, `format`, `message_ids` — reusing the existing `data/Get`
for `obj.message_ids`) and sink it into `websocket/WebsocketResponse` with
`action: "prompt_preview"`. Point the `return_prompt` = true branch at it, and ensure
that branch does **not** reach `EmitSystemMessage`.

Remember the envelope is flat — `WebsocketResponse` spreads `**data`.

**Done when**: sending `visual/visualize` with `prompt_only: true, return_prompt: true`
by hand produces exactly one `prompt_preview` envelope with populated `prompt` and
`negative_prompt` and no system message; with `return_prompt` omitted, behaviour is
unchanged. Satisfies AC2, AC3, AC4. Deps: T1.

## T3 — Confirm how the modal gets mounted outside the Visual Library

Investigation task, not a code task — resolve before T4 so T4 isn't a rewrite.

`VisualLibraryGenerate.vue` needs `scene`, `visualAgentStatus`, `templates`,
`generationAvailable`, `editAvailable`, `maxReferences`. Established already:
`SceneMessages.vue` has `scene` in scope (used at `:1202`) and `agentStatus` with a
`visualAgentReady` computed (`:614`), so `visualAgentStatus` is reachable as
`agentStatus.visual`. Unresolved: where `templates` comes from, how `maxReferences` /
`editAvailable` are derived today in `VisualLibrary.vue`, and whether the injected
`openVisualLibraryWithAsset` already provides a usable mounting route.

**Done when**: written decision on direct mount vs thin wrapper, with the source of every
required prop named. Deps: none (can run parallel with T1/T2).

## T4 — Add `asset_attachment_context` passthrough to the modal

Additive optional prop on `VisualLibraryGenerate.vue`; its prompt-mode `onSubmit`
currently builds `generation_request` with no attachment context. When supplied, include
it so the generated image attaches to the originating message.

Keep it optional and default-absent so the Visual Library's own use of the modal is
unaffected.

**Done when**: the Visual Library's existing generate flow is unchanged, and a supplied
context reaches `visual/generate`. Satisfies part of AC6. Deps: T3.

## T5 — Add the chip

Second chip in `MessageToolbar.vue` behind its own prop (mirroring `showVisualize` /
`visualizeBusy`), passed from `ContextInvestigationMessage.vue` under the same
`showVisualize` condition. `NarratorMessage.vue` untouched.

**Done when**: chip renders on exactly the messages Visualize appears on, disabled in
the same conditions, and hidden once an asset is attached. Satisfies AC1. Deps: none.

## T6 — Request/response plumbing in `SceneMessages.vue`

New method alongside `visualizeMessage` (`:1218`) that reuses `buildVisualizeRequest`
(`:1189`) and sends `prompt_only: true, return_prompt: true`, without the `save_asset` /
auto-attach flags (nothing is being saved yet). Handle the `prompt_preview` envelope in
the existing handler near `:773`, correlating on the echoed `message_ids`, and open the
modal.

Watch `visualizingMessageIds`: the existing `operation_done` sweep clears the whole set
(`:773-775`). Confirm it still covers the new path, or extend it — a preview that errors,
is cancelled, or whose modal is dismissed must not leave a stuck spinner.

**Done when**: click → modal opens immediately with instructions/vis_type filled and
prompt fields loading → fields populate on arrival; cancelling mid-flight leaves no stuck
spinner. Satisfies AC5, AC10. Deps: T2, T3, T5.

## T7 — Generate and attach

Submit through the modal to `visual/generate` with the attachment context from T4.

**Done when**: a real generation attaches to the originating message indistinguishably
from today's Visualize, and the spinner clears. Satisfies AC6, AC7. Deps: T4, T6.
Needs ComfyUI up and a single connected frontend.

## T8 — Verify edits survive

Add a distinctive hand-typed token in the modal, generate, and read it back from the
saved asset's metadata.

Assert on a token that survives `_finalize_prompt` — it filters unrenderable keywords,
drops out-of-shot anchors and trims to budget, so byte-equality with the modal contents
is the wrong assertion. See the spec's fidelity note.

**Done when**: the token is present in the recorded prompt. Satisfies AC8. Deps: T7.

## T9 — Regression pass

Run the visual suite including `tests/test_visual_anchor.py`. Exercise plain Visualize
with a backend configured and with none (the prompt-only fallback), plus the Visual
Library's own generate flow.

**Done when**: suite green and all three flows behave as before. Satisfies AC9. Deps: T7.

## T10 — Document and commit

Note the `return_prompt` flag and the `prompt_preview` envelope wherever the fork's
visual docs live (`docs/fork/`). Commit the graph change, the frontend change and the
docs separately.

Do **not** commit `scenes/*/assets/library.json` or generated PNGs — disposable test
output. `git add -A` would sweep them in.

**Done when**: working tree clean apart from deliberately-ignored files. Deps: T8, T9.

## Notes

Graph-edit safety: after every `wsh-visualize.json` change, reload the scene and confirm
the plain Visualize chip still works before testing anything new — that is the module's
registration smoke test.

Do not add `handle_visualize` to `VisualWebsocketHandler`. Python `handle_<action>`
methods take precedence over node-module sub-handlers
(`websocket_plugin.py:177-195`), so it would shadow the module and silently bypass the
shared generation path this track is built on.

A second session is editing the visual agent's Python. This track's writes are confined
to `modules/wsh-visualize.json` and the frontend, but re-read `generation.py` rather than
trusting the spec's line numbers.
